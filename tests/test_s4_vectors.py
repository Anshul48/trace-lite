"""S4 persistent vector serving (contract TL-QN-2026-09-12.1, steering S4).

Exit criteria under test:
- a one-source edit encodes ONLY changed content (no steady-state re-encode);
- first query NEVER triggers a corpus build (open-only serving, fail-closed);
- crash at each publication boundary recovers coherently;
- line-85 comparison: existing persisted adapter vs exact baseline vs
  persistent ANN candidate on cached identical vectors (filtered recall,
  cold/warm latency, update cost, disk/RAM, Windows support, recovery).

Writes ``evidence/query-native/_integration/S4/s4_vectors.json`` (comparison
table + measurements; machine-written artifact, INT-2 precedent).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np

from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router import HashBowEncoder, VectorIndex
from trace_lite.router.cascade import CascadeRouter
from trace_lite.router.index_adapter import _atomic_write_json
from trace_lite.router.vector_store import (
    EmbeddingCache,
    ServedVectors,
    ensure_served_persistent,
)
from trace_lite.source import service as svc
from trace_lite.store import generations as gen_store
from trace_lite.store.database import Database

REPO_ROOT = Path(__file__).resolve().parents[1]
S4_DIR = REPO_ROOT / "evidence" / "query-native" / "_integration" / "S4"

TOPICS = [
    "wal checkpoint tuning for sqlite filers",
    "faceted classification with hearst patterns",
    "obsidian vault synchronization strategies",
    "context compilation under token budgets",
    "reciprocal rank fusion for hybrid search",
    "truth maintenance across contract boundaries",
]

POOL = ("amber beacon cinder drift ember flint grove harbor inlet jetty "
        "kraal lagoon meadow narrows opal prairie quince ridge sedge tundra "
        "umbra vale wold xenon yarrow zephyr").split()


def _corpus(n_per=40):
    docs, queries = [], []
    for t, topic in enumerate(TOPICS):
        for i in range(n_per):
            w = " ".join(POOL[(i * 7 + t * 13 + j * 11) % len(POOL)] for j in range(4))
            docs.append((f"doc-t{t}-{i}.md",
                         f"note t{t} i{i}: {topic} with {w} measurements and analysis"))
        queries.append(f"how does {topic} behave under load")
        queries.append(f"{topic} measurements analysis")
    return docs, queries


def _publish_all(db, docs):
    for name, text in docs:
        res = svc.publish(db, name, text)
        assert res.created is True


def _atoms(db):
    return {int(r[0]): str(r[1]) for r in
            db.conn.execute("SELECT id, text FROM atom").fetchall()}


def _router(db):
    return CascadeRouter(db.conn, FilingEngine(db.conn, Taxonomy(db.conn)))


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 1. incremental-edit encoding
# ---------------------------------------------------------------------------

def test_s4_incremental_edit_encodes_only_changed(tmp_path):
    """One-source edit => exactly 1 encode; replay/delete => 0; same-content
    new occurrence shares the cached vector with a per-atom entry."""
    db = Database(tmp_path / "s4.db")
    try:
        docs, queries = _corpus(n_per=10)  # 60 atoms
        _publish_all(db, docs)
        before = _atoms(db)
        assert len(before) == 60

        served = ServedVectors(tmp_path / "idx")
        receipt = served.seed_from_db(db.conn, "gen-s4-1", watermark="w1")
        assert receipt["seeded"] is True and receipt["atoms"] == 60
        seed_encodes = served.cache.stats()["encodes"]
        assert seed_encodes == 60  # distinct contents, one encode each

        # Single-source edit through the real R01 path.
        edited = ("note t0 i0: wal checkpoint tuning for sqlite filers with"
                  " completely novel zephyr quantum harbor inlet analysis")
        res = svc.publish(db, "doc-t0-0.md", edited)
        assert res.created is True
        after = _atoms(db)
        removed = [aid for aid in before if aid not in after]
        added = [(aid, txt) for aid, txt in after.items() if aid not in before]
        assert len(removed) == 1 and len(added) == 1, (removed, len(added))
        for aid in removed:
            assert served.delete_atom(aid)["tombstoned"] is True
        enc0 = served.cache.stats()["encodes"]
        out = served.ingest_atom(added[0][0], added[0][1])
        assert out == {"encoded": True, "changed": True, "delta_size": 2}, out
        assert served.cache.stats()["encodes"] - enc0 == 1  # ONLY changed content

        # Replay of identical content: zero encodes, no write.
        enc1 = served.cache.stats()["encodes"]
        rep = served.ingest_atom(added[0][0], added[0][1])
        assert rep["encoded"] is False and rep["changed"] is False
        assert served.cache.stats()["encodes"] == enc1

        # Distinct occurrence of identical content: shared vector, own entry.
        enc2 = served.cache.stats()["encodes"]
        dup = served.ingest_atom(9001, added[0][1])
        assert dup == {"encoded": False, "changed": True, "delta_size": 3}, dup
        assert served.cache.stats()["encodes"] == enc2

        # Delete: zero encodes, tombstoned out of every search path.
        enc3 = served.cache.stats()["encodes"]
        assert served.delete_atom(added[0][0])["changed"] is True
        assert served.cache.stats()["encodes"] == enc3
        doomed = added[0][0]
        for q in queries[:4]:
            for hits, _ in (served.search(q, 10), served.search_exact(q, 10)):
                assert doomed not in {aid for aid, _ in hits}
        # Edited content itself is served (exact + ANN agree top-1).
        probe = "wal checkpoint tuning sqlite filers zephyr quantum"
        exact_top = served.search_exact(probe, 3)[0]
        ann_top = served.search(probe, 3)[0]
        assert exact_top and exact_top[0][0] == 9001, exact_top
        assert ann_top and ann_top[0][0] == 9001, ann_top
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. no build on first query
# ---------------------------------------------------------------------------

def test_s4_first_query_never_builds_corpus(tmp_path):
    """With no published base the serving layer detaches honestly (0 encodes,
    0 files); the legacy full build is NOT triggered. After the explicit
    background seed, the first served query still encodes 0 corpus vectors
    and writes 0 files."""
    db_path = tmp_path / "s4q.db"
    db = Database(db_path)
    try:
        docs, _ = _corpus(n_per=2)  # 12 atoms live, nothing published
        _publish_all(db, docs)
        index_dir = tmp_path / "idx"

        served = ServedVectors.open(index_dir)
        assert served.servable is False
        assert "CURRENT" in served.note
        router = _router(db)
        router.warm()  # legacy in-memory warm only; no S4 encodes/files
        res = ensure_served_persistent(router, served, "gen-absent")
        assert res == {"attached": False, "built": False,
                       "reason": served.note}, res
        assert served.cache.stats()["encodes"] == 0
        assert not index_dir.exists() or list(index_dir.rglob("*")) == []
        assert router.vector_generation_id is None
        # Named fallbacks still serve — never an error on the query path.
        answered = router.route("wal checkpoint tuning sqlite")
        assert answered.route in ("lexical", "beam", "hybrid", "none")

        # Explicit background seed (transitional repair, not a query).
        seed_receipt = served.seed_from_db(db.conn, "gen-s4-q1", watermark="w1")
        assert seed_receipt["seeded"] is True
        files_after_seed = sorted(p.relative_to(index_dir).as_posix()
                                  for p in index_dir.rglob("*") if p.is_file())
        assert any("manifest-gen-s4-q1" in f for f in files_after_seed)

        # First served query: attach + route with zero corpus encodes/writes.
        reopened = ServedVectors.open(index_dir, cache=served.cache)
        assert reopened.servable is True
        enc0 = reopened.cache.stats()["encodes"]
        out = ensure_served_persistent(router, reopened, "gen-s4-q1")
        assert out["attached"] is True and out["built"] is False
        assert out["atoms"] == 12
        assert reopened.cache.stats()["encodes"] == enc0
        q = router.route("wal checkpoint tuning sqlite")
        assert reopened.cache.stats()["encodes"] == enc0
        assert q.vector_generation_id == "gen-s4-q1"
        assert sorted(p.relative_to(index_dir).as_posix()
                      for p in index_dir.rglob("*") if p.is_file()) == files_after_seed
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. crash recovery at each publication boundary
# ---------------------------------------------------------------------------

def test_s4_crash_at_publication_boundaries(tmp_path):
    """Crash (a) after segment save, (b) after manifest, (c) after CURRENT
    before the generation row, (d) mid-delta-write, (e) after CURRENT before
    the sidecar write — every boundary recovers to a coherent served view."""
    db = Database(tmp_path / "s4c.db")
    try:
        docs, queries = _corpus(n_per=8)  # 48 atoms
        _publish_all(db, docs)
        served = ServedVectors(tmp_path / "idx")
        served.seed_from_db(db.conn, "gen-c1", watermark="0001")
        idx_dir = served.index_dir
        baseline = {q: [a for a, _ in served.search(q, 5)[0]] for q in queries[:4]}

        # (a) segments flushed, manifest never written: old gen serves.
        crash_a = served.materialize()
        files_a = crash_a.save(idx_dir, generation_id="gen-c2", watermark="0002")
        crash_a.manifest(files_a)  # manifest dict built but never written
        rec_a = ServedVectors.open(idx_dir, cache=served.cache)
        assert rec_a.generation_id == "gen-c1"
        assert {q: [a for a, _ in rec_a.search(q, 5)[0]]
                for q in queries[:4]} == baseline
        _, info_a = VectorIndex.open(idx_dir)
        assert any("gen-c2" in o for o in info_a["orphans"])
        VectorIndex.collect_orphans(idx_dir)
        assert (idx_dir / "s4-delta-gen-c1.json").exists()  # S4 files untouched

        # (b) manifest written, CURRENT never moved: old gen serves.
        files_b = crash_a.save(idx_dir, generation_id="gen-c2", watermark="0002")
        _atomic_write_json(idx_dir / "manifest-gen-c2.json", crash_a.manifest(files_b))
        rec_b = ServedVectors.open(idx_dir, cache=served.cache)
        assert rec_b.generation_id == "gen-c1"
        assert {q: [a for a, _ in rec_b.search(q, 5)[0]]
                for q in queries[:4]} == baseline

        # (c) CURRENT flipped, generation row missing: files serve new,
        # recover() repairs the row idempotently (no row growth).
        rows_before = len(gen_store.list_generations(db.conn, "vector_index"))
        pub_c = crash_a.publish(idx_dir, None, generation_id="gen-c2",
                                watermark="0002")
        assert (idx_dir / "CURRENT").read_text().strip() == pub_c["manifest"]
        rec_c = ServedVectors.open(idx_dir, cache=served.cache)
        assert "adopt" in rec_c.note  # sidecar for gen-c2 absent post-crash
        out_c = rec_c.recover(db.conn)
        assert out_c["recovered"] is True and out_c["sidecar_healed"] is True
        assert out_c["row"] == "created-and-activated"
        rows_mid = len(gen_store.list_generations(db.conn, "vector_index"))
        assert rows_mid == rows_before + 1
        out_c2 = rec_c.recover(db.conn)
        assert out_c2["row"] == "already-active"
        assert len(gen_store.list_generations(db.conn, "vector_index")) == rows_mid
        active = gen_store.get_active(db.conn, "vector_index")
        assert active is not None and active["manifest_hash"] == out_c["manifest_hash"]
        assert rec_c._note == "" and rec_c.generation_id == "gen-c2"
        assert {q: [a for a, _ in rec_c.search(q, 5)[0]]
                for q in queries[:4]} == baseline  # same corpus: identical ranks

        # (d) torn delta write: a leftover *.tmp never affects the opener.
        sidecar = idx_dir / "s4-delta-gen-c2.json"
        (idx_dir / (sidecar.name + ".tmp")).write_bytes(b'{"upserts": {"1": [torn')
        enc0 = rec_c.cache.stats()["encodes"]
        rec_d = ServedVectors.open(idx_dir, cache=rec_c.cache)
        assert rec_d.generation_id == "gen-c2" and rec_d._note == ""
        assert rec_d.cache.stats()["encodes"] == enc0
        (idx_dir / (sidecar.name + ".tmp")).unlink()

        # (e) CURRENT flipped to a merged gen before its sidecar write:
        # epoch adoption serves the merged content; recover heals + rows cohere.
        served2 = ServedVectors.open(idx_dir, cache=rec_c.cache)
        served2.ingest_atom(5001, "obsidian vault synchronization deep dive")
        merged = served2.materialize()
        merged.publish(idx_dir, None, generation_id="gen-c3", watermark="0003")
        rec_e = ServedVectors.open(idx_dir, cache=served2.cache)
        assert rec_e.generation_id == "gen-c3" and "adopt" in rec_e.note
        top, _ = rec_e.search("obsidian vault synchronization", 5)
        assert top and top[0][0] == 5001  # overlay content survived via the merge
        out_e = rec_e.recover(db.conn)
        assert out_e["sidecar_healed"] is True
        clean = ServedVectors.open(idx_dir, cache=served2.cache)
        assert clean.generation_id == "gen-c3" and clean._note == ""
        assert clean.search("obsidian vault synchronization", 5)[0][0][0] == 5001
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. bounded delta + controlled compaction (zero re-encode)
# ---------------------------------------------------------------------------

def test_s4_bounded_delta_and_compaction(tmp_path):
    """Overlay past the bound requires compaction; compact merges under the
    frozen epoch with zero text encodes and preserved recall."""
    db = Database(tmp_path / "s4d.db")
    try:
        docs, queries = _corpus(n_per=8)  # 48 atoms
        _publish_all(db, docs)
        served = ServedVectors(tmp_path / "idx", delta_bound=8)
        served.seed_from_db(db.conn, "gen-d1", watermark="0001")
        assert served.needs_compaction() is False

        for i in range(10):
            served.ingest_atom(6000 + i,
                               f"compaction probe doc {i} wal checkpoint harbor")
        served.delete_atom(1)
        served.delete_atom(2)
        assert served.delta_size == 12
        assert served.needs_compaction() is True
        pre = served.recall_vs_exact(queries, k=10)
        assert pre["recall_at_k"] >= 0.95, pre

        enc0 = served.cache.stats()["encodes"]
        receipt = served.publish(db.conn, "gen-d2", watermark="0002")
        assert receipt["compacted"] is True
        assert receipt["previous_generation"] == "gen-d1"
        assert served.cache.stats()["encodes"] == enc0  # zero re-encode
        assert served.delta_size == 0
        assert served.needs_compaction() is False
        post = served.recall_vs_exact(queries, k=10)
        assert post["recall_at_k"] >= 0.95, post
        # Frozen epoch preserved across the compaction (never silently rebuilt).
        assert served._doc_count == 48
        reopened = ServedVectors.open(tmp_path / "idx", cache=served.cache)
        assert reopened.generation_id == "gen-d2" and reopened._note == ""
        assert reopened.recall_vs_exact(queries, k=10)["recall_at_k"] >= 0.95
        # Tombstones are baked in by omission: deleted ids leave the segments.
        merged_ids, _ = reopened._merged()
        assert 1 not in merged_ids and 2 not in merged_ids
        hits, _ = reopened.search(queries[0], 10)
        assert 1 not in {aid for aid, _ in hits} and 2 not in {aid for aid, _ in hits}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. generation separation: readers keep the old base during staging
# ---------------------------------------------------------------------------

def test_s4_generation_separation(tmp_path):
    """A reader opened at G1 keeps serving G1 results while G2 publishes;
    only a fresh open sees G2. Generation and publication are separate."""
    db = Database(tmp_path / "s4g.db")
    try:
        docs, queries = _corpus(n_per=8)
        _publish_all(db, docs)
        served = ServedVectors(tmp_path / "idx")
        served.seed_from_db(db.conn, "gen-g1", watermark="0001")

        reader_old = ServedVectors.open(tmp_path / "idx", cache=served.cache)
        old_view = reader_old.materialize()
        old_ranks = {q: [a for a, _ in
                         VectorIndex.search(old_view,
                                            old_view.encoder.encode_weighted(q), 5)[0]]
                     for q in queries[:4]}

        # Stage + publish a generation that changes the top answer.
        served.ingest_atom(7001, "wal checkpoint tuning sqlite filers definitive guide")
        served.publish(db.conn, "gen-g2", watermark="0002")

        still_old = {q: [a for a, _ in
                         VectorIndex.search(old_view,
                                            old_view.encoder.encode_weighted(q), 5)[0]]
                     for q in queries[:4]}
        assert still_old == old_ranks  # old handle untouched by the publish
        fresh = ServedVectors.open(tmp_path / "idx", cache=served.cache)
        assert fresh.generation_id == "gen-g2"
        top, _ = fresh.search("wal checkpoint tuning definitive guide", 5)
        assert top and top[0][0] == 7001
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. disk: old + new generations + WAL coexist, then prune
# ---------------------------------------------------------------------------

def test_s4_disk_old_new_generations_and_wal(tmp_path):
    """Old and new generations verify side-by-side with the live DB; prune
    reclaims the superseded generation while CURRENT keeps serving."""
    db_path = tmp_path / "s4disk.db"
    db = Database(db_path)
    try:
        docs, _ = _corpus(n_per=8)
        _publish_all(db, docs)
        served = ServedVectors(tmp_path / "idx")
        served.seed_from_db(db.conn, "gen-k1", watermark="0001")
        served.ingest_atom(8001, "disk capacity probe harbor inlet jetty")
        served.publish(db.conn, "gen-k2", watermark="0002")
        idx_dir = served.index_dir

        # Both generations hash-verify while co-resident (plus live DB/WAL).
        for gen in ("gen-k1", "gen-k2"):
            body = json.loads((idx_dir / f"manifest-{gen}.json").read_text())
            for ref in body["files"].values():
                assert _sha_file(idx_dir / ref["path"]) == ref["sha256"], ref["path"]
        usage = served.disk_usage(db_path)
        assert set(usage["per_generation"]) == {"gen-k1", "gen-k2"}
        assert usage["footprint_bytes"] > 0
        assert usage["host_free_bytes"] > 100 * usage["footprint_bytes"], usage
        open_before, _ = VectorIndex.open(idx_dir)
        assert open_before.generation_id == "gen-k2"

        pruned = served.prune_generations(keep=1)
        assert pruned["pruned"] is True
        assert any("gen-k1" in r for r in pruned["removed"])
        assert not (idx_dir / "manifest-gen-k1.json").exists()
        assert pruned["bytes_reclaimed"] > 0
        open_after, _ = VectorIndex.open(idx_dir)
        assert open_after.generation_id == "gen-k2"
        fresh = ServedVectors.open(idx_dir, cache=served.cache)
        assert fresh.servable is True
        top, _ = fresh.search("disk capacity probe harbor", 5)
        assert top and top[0][0] == 8001
        usage_after = served.disk_usage(db_path)
        assert usage_after["footprint_bytes"] < usage["footprint_bytes"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. line-85 comparison on cached identical vectors
# ---------------------------------------------------------------------------

def test_s4_recall_latency_comparison_on_cached_vectors(tmp_path):
    """Methodology (steering line 85): existing persisted adapter (A) vs
    exact vector baseline (B) vs persistent ANN candidate (C) on cached
    IDENTICAL vectors. Choice by filtered recall, cold/warm latency, update
    cost, disk/RAM, Windows support, recovery. Results to s4_vectors.json."""
    db = Database(tmp_path / "s4cmp.db")
    try:
        docs, queries = _corpus(n_per=40)  # 240 atoms
        _publish_all(db, docs)
        texts = [t for _, t in docs]

        # Arm C seeds first: the cache + frozen epoch every arm shares.
        served = ServedVectors(tmp_path / "idx-c")
        seed_receipt = served.seed_from_db(db.conn, "gen-cmp-1", watermark="w1")
        assert seed_receipt["atoms"] == 240
        epoch_idf = list(served._idf or [])
        assert len(epoch_idf) == 128
        cache_before = served.cache.stats()["encodes"]

        # Identical vectors for all arms: cache hits only from here on.
        ids, mat = served._merged()
        assert served.cache.stats()["encodes"] == cache_before  # _merged encodes nothing
        assert len(ids) == 240
        qenc = HashBowEncoder()
        qenc.set_idf(list(epoch_idf), served._doc_count)
        qvecs = [qenc.encode_weighted(q) for q in queries]

        # Filter shape: one tombstone + a scattered superseded set.
        doomed = ids[0]
        allowed = set(ids) - {aid for i, aid in enumerate(ids) if i % 7 == 0}
        allowed.discard(doomed)

        # Arm A: existing persisted adapter (R03 IVF), publish + cold reopen.
        enc_a = HashBowEncoder()
        enc_a.set_idf(list(epoch_idf), served._doc_count)
        arm_a = VectorIndex(enc_a, nprobe=8)
        arm_a.build(list(ids), np.asarray(mat, dtype=np.float32),
                    generation_id="arm-a", watermark="w1")
        arm_a.delete(doomed)
        t0 = time.perf_counter()
        arm_a.publish(tmp_path / "idx-a", None, generation_id="arm-a",
                      watermark="w1")
        publish_a_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        cold_a, info_a = VectorIndex.open(tmp_path / "idx-a",
                                          expected_config_hash=enc_a.config_hash())
        cold_open_a_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        cold_a.search(qvecs[0], 10, allowed=allowed)
        cold_q_a_s = time.perf_counter() - t0
        warm_a: list[float] = []
        for _ in range(3):
            for qv in qvecs:
                t0 = time.perf_counter()
                cold_a.search(qv, 10, allowed=allowed)
                warm_a.append(time.perf_counter() - t0)
        recall_a = cold_a.recall_vs_exact(qvecs, k=10, allowed=allowed)

        # Arm B: exact baseline over the same matrix (recall reference).
        t0 = time.perf_counter()
        ref_sets = [ {aid for aid, _ in cold_a.search_exact(qv, 10, allowed)[0]}
                     for qv in qvecs ]
        exact_s = time.perf_counter() - t0
        assert all(ref_sets)  # reference is non-degenerate

        # Arm C: persistent ANN candidate (base + bounded delta overlay).
        served.delete_atom(doomed)
        served.ingest_atom(90001, "wal checkpoint tuning sqlite filers field manual")
        served.ingest_atom(90002, "obsidian vault synchronization strategies handbook")
        t0 = time.perf_counter()
        cold_c = ServedVectors.open(tmp_path / "idx-c", cache=served.cache)
        cold_open_c_s = time.perf_counter() - t0
        assert cold_c.servable is True
        t0 = time.perf_counter()
        cold_c.search(queries[0], 10, allowed=allowed | {90001, 90002})
        cold_q_c_s = time.perf_counter() - t0
        warm_c: list[float] = []
        for _ in range(3):
            for q in queries:
                t0 = time.perf_counter()
                cold_c.search(q, 10, allowed=allowed | {90001, 90002})
                warm_c.append(time.perf_counter() - t0)
        recall_c = cold_c.recall_vs_exact(
            queries, k=10, allowed=allowed | {90001, 90002})
        assert served.cache.stats()["encodes"] - cache_before == 2  # delta only

        # Legacy update cost: what the query-triggered full rebuild encodes
        # (same operations as CascadeRouter.refresh_served_vector: fit + all).
        legacy_enc = HashBowEncoder()
        t0 = time.perf_counter()
        legacy_enc.fit_idf(texts)
        legacy_mat = np.asarray([legacy_enc.encode_weighted(t) for t in texts],
                                dtype=np.float32)
        legacy_s = time.perf_counter() - t0
        assert legacy_mat.shape == (240, 128)

        # C update cost: one new content => one encode (measured live).
        enc0 = served.cache.stats()["encodes"]
        served.ingest_atom(90003, "brand new content narrows opal prairie quince")
        update_c_encodes = served.cache.stats()["encodes"] - enc0
        assert update_c_encodes == 1

        # Disk / RAM terms (both generations of C still co-resident here?
        # only gen-cmp-1 exists; arm A dir separate): record per-arm bytes.
        def _du(p: Path) -> int:
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

        disk_a = _du(tmp_path / "idx-a")
        disk_c = _du(tmp_path / "idx-c")
        ram_mat = int(np.asarray(mat).nbytes)

        # Windows support: static contract scan over the owned surface.
        banned = ("import fcntl", "import termios", "import pwd", "import grp",
                  "import curses")
        router_src = REPO_ROOT / "src" / "trace_lite" / "router"
        violations = []
        for py in sorted(router_src.glob("*.py")):
            src = py.read_text(encoding="utf-8")
            for token in banned:
                if token in src:
                    violations.append(f"{py.name}: {token}")
        assert violations == [], violations
        for mod in ("index_adapter.py", "vector_store.py"):
            assert "os.replace" in (router_src / mod).read_text(encoding="utf-8")

        assert recall_a["recall_at_k"] >= 0.95, recall_a
        assert recall_c["recall_at_k"] >= 0.95, recall_c

        def _p50(xs: list[float]) -> float:
            ordered = sorted(xs)
            return ordered[len(ordered) // 2]

        comparison = {
            "method": "steering line 85: arms A/B/C on cached identical vectors"
                      " (240 atoms, 12 queries, k=10, tombstone + 1/7 filter)",
            "vectors": {"atoms": len(ids), "dim": 128,
                        "shared_cache_entries": served.cache.stats()["entries"],
                        "new_encodes_after_seed": served.cache.stats()["encodes"]
                        - cache_before},
            "arms": {
                "A-persisted-adapter": {
                    "recall_at_k_filtered": recall_a["recall_at_k"],
                    "cold_open_s": round(cold_open_a_s, 4),
                    "cold_query_s": round(cold_q_a_s, 4),
                    "warm_p50_s": round(_p50(warm_a), 5),
                    "publish_s": round(publish_a_s, 4),
                    "disk_bytes": disk_a,
                },
                "B-exact-baseline": {
                    "role": "recall reference (brute force, same vectors/filters)",
                    "total_exact_s": round(exact_s, 4),
                },
                "C-persistent-ANN-candidate": {
                    "recall_at_k_filtered": recall_c["recall_at_k"],
                    "cold_open_s": round(cold_open_c_s, 4),
                    "cold_query_s": round(cold_q_c_s, 4),
                    "warm_p50_s": round(_p50(warm_c), 5),
                    "disk_bytes": disk_c,
                },
            },
            "update_cost_one_edit": {
                "legacy_full_reencode_encodes": len(texts),
                "legacy_full_reencode_s": round(legacy_s, 4),
                "candidate_encodes": update_c_encodes,
            },
            "ram": {"serving_matrix_bytes": ram_mat},
            "disk": {"arm_a_bytes": disk_a, "arm_c_bytes": disk_c},
            "windows": {"posix_only_imports": violations,
                        "atomic_replace": True,
                        "note": "stdlib+numpy only; no native Windows run on this"
                                " host (Linux/WSL); fsync-dir is best-effort"},
            "recovery": {"boundaries": ["segments-only", "manifest-only",
                                        "CURRENT-without-row",
                                        "torn-delta-tmp",
                                        "CURRENT-without-sidecar"],
                         "proven_in": "test_s4_crash_at_publication_boundaries"},
            "choice": "C-persistent-ANN-candidate",
            "choice_reasons": [
                "filtered recall meets the 0.95 bar on identical vectors",
                "steady-state edit costs 1 encode vs 240 (full re-encode)",
                "first query opens only (0 encodes, 0 writes; verified)",
                "crash at each publication boundary recovers coherently",
                "old/new generations co-reside within measured disk headroom",
            ],
        }
        S4_DIR.mkdir(parents=True, exist_ok=True)
        (S4_DIR / "s4_vectors.json").write_text(
            json.dumps(comparison, indent=2), encoding="utf-8")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. G1 immutable serving: clone isolation + batched overlay apply
# ---------------------------------------------------------------------------

def test_s4_clone_isolation_and_batch_apply(tmp_path):
    """A catch-up clone mutates privately; one batch apply costs one sidecar
    write; the published snapshot is untouched until the pointer swap."""
    import time as _time

    db = Database(tmp_path / "s4clone.db")
    try:
        docs, _ = _corpus(n_per=4)  # 24 atoms
        _publish_all(db, docs)
        live = _atoms(db)
        served = ServedVectors(tmp_path / "idx")
        served.seed_from_db(db.conn, "gen-cl1", watermark="0001")
        assert served.servable is True
        before_ids = served.served_ids()
        assert len(before_ids) == 24
        before_version = served.content_version

        clone = served.clone()
        assert clone.servable is True
        assert clone.served_ids() == before_ids
        assert clone.generation_id == served.generation_id

        victim = sorted(live)[0]
        sidecar = tmp_path / "idx" / "s4-delta-gen-cl1.json"
        mtime0 = sidecar.stat().st_mtime_ns
        _time.sleep(0.01)
        applied = clone.apply_batch(
            [(9001, "clone isolation probe harbor inlet jetty"),
             (9002, "second clone arrival quorum beacon vale")],
            [victim])
        assert applied["upserts"] == 2 and applied["deletes"] == 1
        assert applied["encoded"] == 2  # only the two novel contents
        assert clone.delta_size == 3
        # Exactly one sidecar persist for the whole batch.
        assert sidecar.stat().st_mtime_ns > mtime0
        assert clone.content_version == before_version + 1
        # The published snapshot is byte-identical: no overlay, no bump.
        assert served.delta_size == 0
        assert served.content_version == before_version
        assert served.served_ids() == before_ids
        assert victim in served.served_ids()
        # The clone serves the merged view (new ids ranked, victim gone).
        top, _ = clone.search("clone isolation probe harbor", 5)
        assert top and top[0][0] == 9001
        for q in ("clone isolation probe harbor", "faceted classification"):
            for hits, _ in (clone.search(q, 10), clone.search_exact(q, 10)):
                assert victim not in {aid for aid, _ in hits}
        # Replay-stable: re-applying the same batch changes nothing.
        again = clone.apply_batch([(9001, "clone isolation probe harbor inlet jetty")], [])
        assert again["upserts"] == 0 and again["encoded"] == 0
    finally:
        db.close()
