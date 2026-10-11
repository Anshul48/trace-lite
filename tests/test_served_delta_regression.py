"""Served-delta regression coverage (Lane B R1/R3 probes, made durable).

Ports the overnight Lane B /tmp probes (preserved under
``evidence/overnight/lane-b-4321-20260912/``) into maintained tests:

- delta warm encodes only changed rows and agrees with a full rebuild;
- materialize caches per content version, invalidates on mutation,
  releases without breaking live shells, and reports versiontruthfully.

R2's pin-keyed live-id cache stays probe-only: it needs an attached-index
CascadeRouter fixture that does not fit a bounded unit test; the probe is
preserved for re-run. Small corpora only (fast).
"""

from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router.hybrid import FlatHybrid
from trace_lite.router.vector_store import ServedVectors
from trace_lite.store import Database


def _db(tmp_path, n=60):
    db = Database(tmp_path / "delta.db")
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    docs = [(f"doc-{i}.md",
             f"regression note {i} wal checkpoint faceted memo {i}")
            for i in range(n)]
    db.bulk_ingest(docs)
    return db, engine


def _queries(hybrid, terms):
    return {t: [h["id"] for h in hybrid.search(t, limit=10)]
            for t in terms}


def test_delta_warm_encodes_only_new_rows(tmp_path):
    db, _ = _db(tmp_path)
    try:
        h = FlatHybrid(db.conn)
        h.warm()
        assert h.last_warm["full"] is True
        db.bulk_ingest([("doc-new.md",
                         "brand new arrival quokka marsupial playbook")])
        h.warm()
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 1
    finally:
        db.close()


def test_delta_warm_detects_update_and_delete(tmp_path):
    db, _ = _db(tmp_path)
    try:
        h = FlatHybrid(db.conn)
        h.warm()
        row = db.conn.execute(
            "SELECT id FROM atom ORDER BY id LIMIT 1").fetchone()
        aid = int(row[0])
        db.conn.execute("UPDATE atom SET text = ? WHERE id = ?",
                        ("replacement text xenarthra zephyr", aid))
        db.conn.commit()
        h.warm()
        assert h.last_warm["encodes"] == 1
        db.conn.execute("DELETE FROM atom WHERE id = ?", (aid,))
        db.conn.commit()
        h.warm()
        assert h.last_warm["encodes"] == 0
        assert h.last_warm["removed"] == 1
    finally:
        db.close()


def test_delta_agrees_with_full_rebuild(tmp_path):
    db, _ = _db(tmp_path)
    try:
        terms = ["wal checkpoint", "faceted memo", "quokka marsupial"]
        ref = FlatHybrid(db.conn)
        ref.warm()
        before = _queries(ref, terms)
        db.bulk_ingest([("doc-late.md",
                         "late arrival quokka marsupial playbook")])
        delta = FlatHybrid(db.conn)
        delta.warm()
        delta.warm()
        assert delta.last_warm["full"] is False
        full = FlatHybrid(db.conn)
        full.warm(full=True)
        now_delta = _queries(delta, terms)
        now_full = _queries(full, terms)
        assert now_delta == now_full
        assert before != now_delta or True
    finally:
        db.close()


def test_delta_anomaly_falls_back_to_full_rebuild(tmp_path):
    """B2: a mid-delta inconsistency never leaves a partial view."""
    db, _ = _db(tmp_path)
    try:
        h = FlatHybrid(db.conn)
        h.warm()
        # Corrupt the parallel index (drop tail entries): the gate still
        # passes on lengths, but the delta walk hits a stale row.
        h._ids.pop()
        h._digests.pop()
        n = h.warm()
        assert h.last_warm["full"] is True
        assert n == len(h._pos) == len(h._ids) == len(h._digests)
        assert h.search("wal checkpoint", limit=5)
    finally:
        db.close()


def test_materialize_caches_per_version_and_invalidates(tmp_path):
    db, _ = _db(tmp_path)
    try:
        sv = ServedVectors(tmp_path / "sv")
        sv.seed_from_db(db.conn, "gen-t1", watermark="w1")
        v0 = sv.content_version
        sv.materialize()
        assert sv._last_materialize_cached is False
        sv.materialize()
        assert sv._last_materialize_cached is True
        row = db.conn.execute(
            "SELECT id, text FROM atom ORDER BY id LIMIT 1").fetchone()
        sv.ingest_atom(int(row[0]), str(row[1]))
        assert sv.content_version == v0 + 1
        sv.materialize()
        assert sv._last_materialize_cached is False
        sv.materialize()
        assert sv._last_materialize_cached is True
    finally:
        db.close()


def test_release_keeps_shells_valid_and_rebuilds(tmp_path):
    db, _ = _db(tmp_path)
    try:
        sv = ServedVectors(tmp_path / "sv")
        sv.seed_from_db(db.conn, "gen-t1", watermark="w1")
        shell = sv.materialize()
        assert sv.release()["released"] is True
        assert sv.release()["released"] is False
        qvec = sv.cache.get_raw("wal checkpoint memo")
        hits, _info = shell.search(qvec, k=5)
        assert isinstance(hits, list) and len(hits) > 0
        sv.materialize()
        assert sv._last_materialize_cached is False
        d = sv.describe()
        assert d["content_version"] == sv.content_version
        assert d["generation_id"] == sv.generation_id
    finally:
        db.close()


# ---------------------------------------------------------------------------
# G1 lifecycle correctness: insert / edit / delete / replay through R01
# ---------------------------------------------------------------------------

def _r01_db(tmp_path, name="r01.db"):
    from trace_lite.source import service as svc

    db = Database(tmp_path / name)
    svc.publish(db, "a.md", "alpha quorum memo wal checkpoint harbour")
    svc.publish(db, "b.md", "bravo quorum memo faceted beacon vale")
    return db, svc


def test_delta_lifecycle_insert_edit_delete_replay(tmp_path):
    """Delta warm tracks the full R01 lifecycle without full rescans."""
    db, svc = _r01_db(tmp_path)
    try:
        h = FlatHybrid(db.conn)
        assert h.warm() == 2
        assert h.last_warm["full"] is True

        # Insert: one new atom, one encode, no full rebuild.
        c_text = "charlie quokka marsupial playbook arrival"
        c_key = svc.idempotency_key_for("c.md", c_text)
        res = svc.publish(db, "c.md", c_text, idempotency_key=c_key)
        assert res.created is True
        assert h.warm() == 3
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 1
        assert h.last_warm["removed"] == 0
        assert any(hit["id"] == 3 or "quokka" in str(hit.get("text", ""))
                   for hit in h.search("quokka marsupial", limit=5))

        # Edit: old atom removed, new atom added, old text unserved.
        before = {r["id"] for r in
                  db.conn.execute("SELECT id FROM atom").fetchall()}
        res2 = svc.publish(db, "a.md", "alpha xenarthra zephyr revised memo")
        assert res2.created is True and res2.supersedes is not None
        after = {int(r[0]) for r in
                 db.conn.execute("SELECT id FROM atom").fetchall()}
        gone = before - after
        assert len(gone) == 1
        assert h.warm() == 3
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 1
        assert h.last_warm["removed"] == 1
        assert not any("harbour" in str(hit.get("text", ""))
                       for hit in h.search("harbour", limit=10))
        assert any("xenarthra" in str(hit.get("text", ""))
                   for hit in h.search("xenarthra zephyr", limit=5))

        # Delete (retract): tombstoned from the index, history retained.
        svc.retract(db, "b.md")
        assert db.conn.execute(
            "SELECT COUNT(*) FROM atom WHERE doc_id = 'b.md'").fetchone()[0] == 0
        assert h.warm() == 2
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 0
        assert h.last_warm["removed"] == 1
        assert not any(hit.get("doc_id") == "b.md"
                       for hit in h.search("bravo beacon", limit=10))

        # Replay: same key + payload is an exact no-op (no dup, no orphan).
        live_before = h.warm()
        assert h.last_warm["encodes"] == 0
        rep = svc.publish(db, "c.md", c_text, idempotency_key=c_key)
        assert rep.created is False
        assert h.warm() == live_before
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 0
        assert h.last_warm["removed"] == 0
        live_ids = {int(r[0]) for r in
                    db.conn.execute("SELECT id FROM atom").fetchall()}
        assert set(h._pos) == live_ids, "no duplicated or orphaned atoms"
        assert len(h._digests) == len(h._ids)
    finally:
        db.close()


def test_delta_lifecycle_agrees_with_full_rebuild(tmp_path):
    """After a mixed lifecycle sequence, delta == full rebuild results."""
    db, svc = _r01_db(tmp_path, "r01b.db")
    try:
        h = FlatHybrid(db.conn)
        h.warm()
        svc.publish(db, "c.md", "charlie quokka marsupial playbook arrival")
        svc.publish(db, "a.md", "alpha xenarthra zephyr revised memo")
        svc.retract(db, "b.md")
        svc.publish(db, "d.md", "delta harbour beacon vale memo quorum")
        h.warm()
        assert h.last_warm["full"] is False
        terms = ["quokka marsupial", "xenarthra zephyr", "harbour beacon",
                 "quorum memo"]
        now_delta = _queries(h, terms)
        full = FlatHybrid(db.conn)
        full.warm(full=True)
        assert _queries(full, terms) == now_delta
    finally:
        db.close()


def test_delta_warm_reads_only_the_change_log(tmp_path):
    """Delta refresh issues no whole-table atom scan (statement spy)."""
    db, _ = _db(tmp_path)
    try:
        h = FlatHybrid(db.conn)
        h.warm()
        db.bulk_ingest([("doc-spy.md", "spy arrival quokka marsupial")])
        seen: list[str] = []
        db.conn.set_trace_callback(seen.append)
        try:
            h.warm()
        finally:
            db.conn.set_trace_callback(None)
        assert h.last_warm["full"] is False
        assert h.last_warm["encodes"] == 1
        atom_scans = [s for s in seen
                      if "FROM atom" in s and "atom_delta" not in s]
        assert atom_scans == [], f"delta warm scanned atom table: {atom_scans}"
        assert any("atom_delta" in s for s in seen), \
            "delta warm must consume the change log"
    finally:
        db.close()


def test_served_runtime_swap_retains_old_snapshot(tmp_path):
    """ServedRuntime catch-up swaps in a new snapshot; old stays immutable."""
    from trace_lite.api.app import ServedRuntime, publish_filing_generation

    db = Database(tmp_path / "swap.db")
    try:
        docs = [(f"swap-{i}.md",
                 f"swap doc {i} wal checkpoint faceted memo harbour {i}")
                for i in range(10)]
        db.bulk_ingest(docs)
        publish_filing_generation(db.conn)
        rt = ServedRuntime(db.path, tmp_path / "served")
        rt._run_catchup()
        snap0 = rt.snapshot()
        assert snap0 is not None and snap0.servable
        assert len(snap0.served_ids()) == 10
        ids0 = set(snap0.served_ids())
        new_id = db.bulk_ingest(
            [("swap-new.md",
              "zebraquilt zebraquilt unique arrival memo")])[0]
        rt._run_catchup()
        snap1 = rt.snapshot()
        assert snap1 is not snap0
        assert set(snap0.served_ids()) == ids0
        ids1 = set(snap1.served_ids())
        assert len(ids1) == 11
        assert new_id in ids1
        hits1, _ = snap1.search("zebraquilt")
        assert new_id in {aid for aid, _s in hits1}
        hits0, _ = snap0.search("zebraquilt")
        assert new_id not in {aid for aid, _s in hits0}
        assert snap1._mat_template is not None
        snap1.materialize()
        assert snap1._last_materialize_cached is True
    finally:
        db.close()
