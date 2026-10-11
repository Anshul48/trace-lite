"""R2+R3 verification: live-cache pinning, materialize cache, shell isolation."""
import sys
import time
from pathlib import Path

RUN = Path(sys.argv[1])
DB = RUN / "r23.db"
IDX = RUN / "r23served"
for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
    try:
        p.unlink()
    except OSError:
        pass
import shutil
shutil.rmtree(IDX, ignore_errors=True)

from trace_lite.store import Database
from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router.cascade import CascadeRouter
from trace_lite.router.vector_store import ServedVectors, ensure_served_persistent

db = Database(DB)
eng = FilingEngine(db.conn, Taxonomy(db.conn))
N = 300
db.bulk_ingest([(f"d{i}.md", f"cache probe note {i} wal checkpoint governor tuning {i%17}")
                for i in range(N)])

# --- R3: materialize cache ---
sv = ServedVectors(IDX)
sv.seed_from_db(db.conn, "gen-R3", watermark="w")
t0 = time.monotonic(); m1 = sv.materialize(); cold = time.monotonic() - t0
assert sv._last_materialize_cached is False
t0 = time.monotonic(); m2 = sv.materialize(); hot = time.monotonic() - t0
assert sv._last_materialize_cached is True
assert m1 is not m2 and m1._mat is m2._mat  # fresh shell, shared storage
assert hot < cold / 5, (hot, cold)
# shell isolation: publish from one shell never perturbs the template/sibling
m1.publish(IDX, None, generation_id="gen-R3b", watermark="wb")
assert m2.generation_id != "gen-R3b"
m2.delete(next(iter(m2.live_ids())))
m3 = sv.materialize()
assert sv._last_materialize_cached is True
assert m3.count == m1.count  # sibling delete did not leak into template
# overlay mutation invalidates; next attach rebuilds once, then caches
oid = db.insert_atom("fresh.md", "cache probe fresh arrival wal checkpoint governor")
sv.ingest_atom(oid, "cache probe fresh arrival wal checkpoint governor")
assert sv._last_materialize_cached is True  # flag still reflects last call
t0 = time.monotonic(); m4 = sv.materialize(); reb = time.monotonic() - t0
assert sv._last_materialize_cached is False
assert oid in m4.live_ids() and oid not in m3.live_ids()
m5 = sv.materialize()
assert sv._last_materialize_cached is True and oid in m5.live_ids()
# release drops template; shells stay valid; next call rebuilds
assert sv.release() == {"released": True}
assert m5.count > 0  # shell still serves after release
m6 = sv.materialize()
assert sv._last_materialize_cached is False and m6.count == m5.count
d = sv.describe()
assert d["generation_id"] == "gen-R3" and d["delta_size"] == 1, d
assert d["content_version"] >= 2 and d["servable"] is True, d
print(f"R3_OK cold={cold*1000:.1f}ms hot={hot*1000:.3f}ms rebuild={reb*1000:.1f}ms")

# --- R3: ensure additive keys, frozen detached shape ---
r = CascadeRouter(db.conn, eng)
r.warm()
out = ensure_served_persistent(r, sv, "gen-FILING-X")
assert out["attached"] is True and out["built"] is False and out["atoms"] == N + 1
assert out["base_generation_id"] == "gen-R3" and out["delta_size"] == 1
assert out["materialized_cached"] is True
assert r.vector_generation_id == "gen-FILING-X"  # frozen pin label untouched
assert r._vector_base_generation == "gen-R3"  # additive base truth
empty = ServedVectors.open(IDX.parent / "nosuch")
res = ensure_served_persistent(r, empty, "gen-absent")
assert res == {"attached": False, "built": False, "reason": empty.note}, res
assert r.vector_generation_id is None and r._vector_base_generation is None
print("R3_ENSURE_OK")

# --- R2: live-id cache per pin (varied corpus: uniform text collapses IVF
# k-means on base code too, verified above) ---
DB2 = RUN / "r23b.db"
for p in (DB2, Path(str(DB2) + "-wal"), Path(str(DB2) + "-shm")):
    try:
        p.unlink()
    except OSError:
        pass
POOL = ("amber beacon cinder drift ember flint grove harbor inlet jetty "
        "kraal lagoon meadow narrows opal prairie quince ridge sedge tundra "
        "umbra vale wold xenon yarrow zephyr").split()
TOPICS = ["wal checkpoint tuning for sqlite filers",
          "faceted classification with hearst patterns",
          "obsidian vault synchronization strategies",
          "context compilation under token budgets"]
db2 = Database(DB2)
eng2 = FilingEngine(db2.conn, Taxonomy(db2.conn))
docs2 = []
for t, topic in enumerate(TOPICS):
    for i in range(75):
        w = " ".join(POOL[(i * 7 + t * 13 + j * 11) % len(POOL)] for j in range(4))
        docs2.append((f"v-t{t}-{i}.md", f"note t{t} i{i}: {topic} with {w}"))
db2.bulk_ingest(docs2)
r2 = CascadeRouter(db2.conn, eng2)
r2.warm()
r2.refresh_served_vector("gen-pin1")
assert r2._vector_base_generation == "gen-pin1"
fetches = {"n": 0}
def counting(sql):
    if isinstance(sql, str) and sql.strip().startswith("SELECT id FROM atom"):
        fetches["n"] += 1
db2.conn.set_trace_callback(counting)
try:
    h1, i1 = r2._vector_search("wal checkpoint tuning sqlite filers", 10)
    assert h1, "varied corpus must yield vector hits"
    assert fetches["n"] == 1, fetches
    h2, i2 = r2._vector_search("wal checkpoint tuning sqlite filers", 10)
    assert fetches["n"] == 1, fetches  # same pin: zero SQL
    assert [a["id"] for a in h1] == [a["id"] for a in h2]
    # re-pin refetches once and sees retractions
    victim = h1[0]["id"]
    db2.delete_atom(victim)
    r2.refresh_served_vector("gen-pin2")
    h3, i3 = r2._vector_search("wal checkpoint tuning sqlite filers", 10)
    assert fetches["n"] == 2, fetches
    assert victim not in {a["id"] for a in h3}
    assert i3.get("served_adequate") in (True, False)
finally:
    db2.conn.set_trace_callback(None)
    db2.close()
    db.close()
print("R2_OK")
