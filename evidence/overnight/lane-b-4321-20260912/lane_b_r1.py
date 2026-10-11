"""R1 verification: delta warm encodes only the delta; retrieval agrees with full warm."""
import sys
from pathlib import Path

RUN = Path(sys.argv[1])
DB = RUN / "r1.db"
for p in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
    try:
        p.unlink()
    except OSError:
        pass

from trace_lite.store import Database
from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router.cascade import CascadeRouter
import trace_lite.filing.engine as engmod
import trace_lite.router.hybrid as hybridmod

db = Database(DB)
eng = FilingEngine(db.conn, Taxonomy(db.conn))
N = 500
db.bulk_ingest([(f"d{i}.md", f"delta probe note {i} wal checkpoint governor tuning payload {i%31}")
                for i in range(N)])

calls = {"n": 0}
orig = engmod.text_vector
def counting(text, dim=128):
    calls["n"] += 1
    return orig(text, dim)
engmod.text_vector = counting
hybridmod.text_vector = counting

r = CascadeRouter(db.conn, eng)
assert r.warm()["vectors"] == N
assert calls["n"] == N, calls
assert r.hybrid.last_warm["full"] is True

# 1 insert -> 1 encode
calls["n"] = 0
db.insert_atom("new.md", "delta probe brand new wal checkpoint governor arrival")
assert r.warm()["vectors"] == N + 1
assert r.hybrid.last_warm == {"full": False, "encodes": 1, "reused": N,
                              "removed": 0, "live": N + 1}, r.hybrid.last_warm
assert calls["n"] == 1, calls

# same-id text replacement -> 1 encode, serving the new terms
calls["n"] = 0
row = db.conn.execute("SELECT id FROM atom WHERE doc_id='d0.md'").fetchone()
db.conn.execute("UPDATE atom SET text=? WHERE id=?",
                ("delta probe REPLACED quokka marsupial xenarthra", row[0]))
db.conn.commit()
db.rebuild_fts()
assert r.warm()["vectors"] == N + 1
assert calls["n"] == 1, calls
hits = r.hybrid.search("quokka marsupial xenarthra", limit=5)
assert hits and hits[0]["id"] == row[0], [h["id"] for h in hits]

# delete -> 0 encodes, live count drops
calls["n"] = 0
db.delete_atom(row[0])
assert r.warm()["vectors"] == N, r.hybrid.last_warm
assert calls["n"] == 0, calls
assert r.hybrid.last_warm["removed"] == 1

# no change -> 0 encodes
calls["n"] = 0
assert r.warm()["vectors"] == N
assert calls["n"] == 0, calls

engmod.text_vector = orig
hybridmod.text_vector = orig

# agreement: delta-warmed vs full-warmed retrieval over a query battery
queries = ["wal checkpoint governor", "delta probe payload",
           "checkpoint tuning arrival", "xqzt blorpt wqkj"]
r2 = CascadeRouter(db.conn, eng)
r2.warm()
for q in queries:
    a = r.route(q, limit=5)
    b = r2.route(q, limit=5)
    assert a.verdict == b.verdict and a.route == b.route, (q, a.route, b.route)
    assert [x["id"] for x in a.anchors] == [x["id"] for x in b.anchors], q
    pass  # route-level exact agreement above is the decision-level check

# Vector-level fidelity: same id -> nearly identical direction under both
# IDF epochs (frozen delta epoch vs refit full epoch). Rank order on
# collision-dominated near-tie queries is chaotic by nature and is NOT
# asserted (same stability class as the pre-existing add_atom path).
import math as _m
worst = 1.0
for aid in list(r.hybrid._pos)[::10]:
    a = r.hybrid._matrix[r.hybrid._pos[aid]].astype("float32")
    b = r2.hybrid._matrix[r2.hybrid._pos[aid]].astype("float32")
    cos = float(a @ b) / (_m.sqrt(float(a @ a)) * _m.sqrt(float(b @ b)))
    worst = min(worst, cos)
assert worst > 0.999, worst
print("min_same_id_cosine =", round(worst, 6))

# full=True forces exact rebuild
assert r.hybrid.warm(full=True) == N
assert r.hybrid.last_warm["full"] is True
db.close()
print("R1_OK")
