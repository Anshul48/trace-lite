"""Lane B audit probe: measure corpus-sized work on persistent request path.

All measurements run against the lane worktree candidate (PYTHONPATH=worktree/src).
Writes measurements JSON + DBs under the unique run dir (argv[1]).
"""
import json
import sys
import time
from pathlib import Path

RUN = Path(sys.argv[1])
RUN.mkdir(parents=True, exist_ok=True)
DB = RUN / "audit.db"
IDX = RUN / "served"
for p in (DB, DB.with_suffix(".db-wal"), DB.with_suffix(".db-shm")):
    try:
        p.unlink()
    except OSError:
        pass
import shutil
shutil.rmtree(IDX, ignore_errors=True)

from trace_lite.store import Database  # noqa: E402
from trace_lite.filing import FilingEngine, Taxonomy  # noqa: E402
from trace_lite.router.cascade import CascadeRouter  # noqa: E402
from trace_lite.router.vector_store import ServedVectors, ensure_served_persistent  # noqa: E402
import trace_lite.filing.engine as engmod  # noqa: E402

N = 2000
out = {"n_atoms": N, "db": str(DB)}

db = Database(DB)
tax = Taxonomy(db.conn)
eng = FilingEngine(db.conn, tax)
docs = [(f"doc-{i}.md", f"audit note {i} wal checkpoint governor tuning observation payload {i%97}")
        for i in range(N)]
t0 = time.monotonic()
ids = db.bulk_ingest(docs)
out["bulk_ingest_s"] = round(time.monotonic() - t0, 3)

# --- M1: hybrid.warm full + refresh cost (text_vector call counts) ---
calls = {"n": 0}
orig_tv = engmod.text_vector
def counting_tv(text, dim=128):
    calls["n"] += 1
    return orig_tv(text, dim)
engmod.text_vector = counting_tv
# rebind names already imported into router modules (they hold direct refs)
import trace_lite.router.hybrid as hybridmod
hybridmod.text_vector = counting_tv

router = CascadeRouter(db.conn, eng)
t0 = time.monotonic()
warm1 = router.warm()
full_s = time.monotonic() - t0
out["M1_warm_full"] = {"s": round(full_s, 3), "text_vector_calls": calls["n"], "counts": warm1}

# one-atom generation step, then refresh (what _refresh_reader does per gen move)
calls["n"] = 0
new_id = db.insert_atom("doc-new.md", "audit note new wal checkpoint governor tuning fresh payload")
t0 = time.monotonic()
warm2 = router.warm()
ref_s = time.monotonic() - t0
out["M1_warm_refresh_after_1_insert"] = {
    "s": round(ref_s, 3), "text_vector_calls": calls["n"], "counts": warm2,
    "corpus_now": N + 1,
}

# --- M2: refresh_served_vector (legacy query-path build) cost ---
calls["n"] = 0
t0 = time.monotonic()
rep = router.refresh_served_vector("gen-legacy-1")
m2_s = time.monotonic() - t0
out["M2_refresh_served_vector"] = {
    "s": round(m2_s, 3), "text_vector_calls": calls["n"], "report": rep,
    "vector_gen": router.vector_generation_id,
}
engmod.text_vector = orig_tv
hybridmod.text_vector = orig_tv

# --- M3: S4 seed + persistent attach (materialize) cost ---
served = ServedVectors(IDX)
t0 = time.monotonic()
seed = served.seed_from_db(db.conn, "gen-A", watermark="w-A")
out["M3_seed"] = {"s": round(time.monotonic() - t0, 3), "atoms": seed["atoms"],
                  "cache": seed["cache"]}
t0 = time.monotonic()
m1 = served.materialize()
m3_mat_s = time.monotonic() - t0
out["M3_materialize_cold"] = {"s": round(m3_mat_s, 4), "count": m1.count,
                              "n_lists": m1.n_lists}
t0 = time.monotonic()
m2 = served.materialize()
out["M3_materialize_again_same_snap"] = {"s": round(time.monotonic() - t0, 4)}
t0 = time.monotonic()
res = ensure_served_persistent(router, served, "gen-filing-newer")
out["M3_ensure_attach"] = {"s": round(time.monotonic() - t0, 4), "result": res}

# overlay 1 atom then materialize again (per-refresh cost after catchup)
oid = db.insert_atom("doc-new2.md", "audit note newest wal checkpoint governor delta payload")
served.ingest_atom(oid, "audit note newest wal checkpoint governor delta payload")
t0 = time.monotonic()
m3 = served.materialize()
out["M3_materialize_after_1_overlay"] = {"s": round(time.monotonic() - t0, 4),
                                         "count": m3.count}

# split _merged vs k-means build inside materialize
t0 = time.monotonic()
mids, mmat = served._merged()
merge_s = time.monotonic() - t0
from trace_lite.router.index_adapter import VectorIndex
from trace_lite.router.encoder import HashBowEncoder
t0 = time.monotonic()
vi = VectorIndex(HashBowEncoder()).build(mids, mmat, generation_id="x")
build_s = time.monotonic() - t0
out["M3_materialize_split"] = {"merged_s": round(merge_s, 4), "index_build_kmeans_s": round(build_s, 4)}

# --- M4: per-query live-id scan inside _vector_search ---
t0 = time.monotonic()
live = {r[0] for r in db.conn.execute("SELECT id FROM atom").fetchall()}
out["M4_live_id_scan"] = {"s": round(time.monotonic() - t0, 4), "live": len(live)}
t0 = time.monotonic()
vhits, vinfo = router._vector_search("wal checkpoint governor tuning", 10)
out["M4_vector_search_total"] = {"s": round(time.monotonic() - t0, 4),
                                 "hits": len(vhits),
                                 "adequate": vinfo.get("served_adequate")}

# --- M5: generation labeling with stale snap + newer filing id ---
stale = ServedVectors.open(IDX)
r2 = CascadeRouter(db.conn, eng)
lab = ensure_served_persistent(r2, stale, "gen-FILING-NEWER-THAN-VECTOR-BASE")
out["M5_mislabel"] = {
    "snap_generation_id": stale.generation_id,
    "requested_filing_generation": "gen-FILING-NEWER-THAN-VECTOR-BASE",
    "router_vector_generation_id": r2.vector_generation_id,
    "mislabeled": r2.vector_generation_id == "gen-FILING-NEWER-THAN-VECTOR-BASE"
    and stale.generation_id != "gen-FILING-NEWER-THAN-VECTOR-BASE",
}

# --- M6: attached-view staleness has no version linkage ---
m_before = stale.materialize()
before_ids = set(m_before.live_ids())
stale.ingest_atom(oid, "audit note newest wal checkpoint governor delta payload")
m_after = stale.materialize()
out["M6_snapshot_identity"] = {
    "same_object": m_before is m_after,
    "before_has_overlay_atom": oid in before_ids,
    "after_has_overlay_atom": oid in set(m_after.live_ids()),
    "served_exposes_content_version": hasattr(stale, "content_version"),
    "served_exposes_dispose": hasattr(stale, "dispose") or hasattr(stale, "close"),
}

db.close()
(RUN / "audit_measurements.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
