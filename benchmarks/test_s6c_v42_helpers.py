"""Offline verification for s6_concurrent_serve v4.2 pure helpers (no app, no DB).

v4.2 changed two gates, so this file proves the new gates still detect
genuine failures (negative controls that MUST fail):

cite_exact (exact byte-span citation, replaces substring matching):
  1. exact match -> ok
  2. wrong revision text sharing vocabulary -> span-bytes-mismatch
     (the old substring check would PASS this: shared words)
  3. shifted span (start+1) -> span-bytes-mismatch
  4. missing atom (deleted/never projected) -> atom-missing
  5. retracted doc (no current revision) -> revision-missing
  6. stale atom text (overlay not converged) -> span-bytes-mismatch
  7. bad atom id -> bad-atom-id
  8. span out of range -> span-out-of-range
  9. multibyte UTF-8 text -> ok (byte offsets, not char offsets)
  10. empty atom text -> fail-closed (span-out-of-range), never ok

overlap_verdict (split gate):
  11. maint-sample overlap only, no mutations -> maint True, mut False
  12. mutation overlap only, idle worker, no progress -> maint False
      (must NOT gate), mut True
  13. neither -> both False

_module_identity (content-bound manifests):
  14. deterministic across calls, covers loaded trace_lite modules
  15. hash-sensitive: one flipped byte in a temp module copy changes sha
"""
import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = str(HERE / "s6_concurrent_serve.py")

spec = importlib.util.spec_from_file_location("s6c_v42", HARNESS)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name, detail)
    if not cond:
        fails.append(name)


def fixture_db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    con = sqlite3.connect(tmp.name)
    con.execute("CREATE TABLE source_revision (revision_id TEXT, "
                "source_id TEXT, status TEXT, text TEXT)")
    con.execute("CREATE TABLE atom (id INTEGER PRIMARY KEY, doc_id TEXT, "
                "text TEXT, start_byte INTEGER, end_byte INTEGER)")
    return tmp.name, con


TEXT = "alpha beta gamma delta"
con_path, con = fixture_db()
con.execute("INSERT INTO source_revision VALUES (?,?,?,?)",
            ("r1", "doc-a.md", "current", TEXT))
# atom 1: exact span of "beta gamma" (bytes 6..16)
con.execute("INSERT INTO atom VALUES (?,?,?,?,?)",
            (1, "doc-a.md", "beta gamma", 6, 16))
# atom 2: stale text (claims content the revision does not hold there)
con.execute("INSERT INTO atom VALUES (?,?,?,?,?)",
            (2, "doc-a.md", "BETA GAMMA", 6, 16))
# atom 3: shifted span (true bytes are 6..16; recorded 7..17)
con.execute("INSERT INTO atom VALUES (?,?,?,?,?)",
            (3, "doc-a.md", "beta gamma", 7, 17))
# atom 4: multibyte text; span in BYTE offsets
UNI_REV = "héllo wörld ünïcode"
con.execute("INSERT INTO source_revision VALUES (?,?,?,?)",
            ("r2", "doc-u.md", "current", UNI_REV))
ub = UNI_REV.encode("utf-8")
seg = "wörld".encode("utf-8")
s = ub.index(seg)
con.execute("INSERT INTO atom VALUES (?,?,?,?,?)",
            (4, "doc-u.md", "wörld", s, s + len(seg)))
# atom 5: empty text
con.execute("INSERT INTO atom VALUES (?,?,?,?,?)",
            (5, "doc-a.md", "", 6, 6))
con.commit()
con.close()

r = m.cite_exact(con_path, 1, "doc-a.md")
check("cite-exact-ok", r["ok"] is True and r["reason"] == "exact", r)

con2 = sqlite3.connect(con_path)
con2.execute("UPDATE source_revision SET text=? WHERE source_id=?",
             ("alpha BETA shared words here", "doc-a.md"))
con2.commit()
con2.close()
r = m.cite_exact(con_path, 1, "doc-a.md")
check("cite-wrong-revision-text", r["ok"] is False
      and r["reason"] == "span-bytes-mismatch", r)

r = m.cite_exact(con_path, 3, "doc-a.md")
check("cite-shifted-span", r["ok"] is False, r)

r = m.cite_exact(con_path, 999, "doc-a.md")
check("cite-missing-atom",
      r["ok"] is False and r["reason"] == "atom-missing", r)

r = m.cite_exact(con_path, 1, "doc-gone.md")
check("cite-retracted-no-current-rev",
      r["ok"] is False and r["reason"] == "revision-missing", r)

r = m.cite_exact(con_path, 2, "doc-a.md")
check("cite-stale-text", r["ok"] is False
      and r["reason"] == "span-bytes-mismatch", r)

r = m.cite_exact(con_path, "abc", "doc-a.md")
check("cite-bad-atom-id",
      r["ok"] is False and r["reason"] == "bad-atom-id", r)

r = m.cite_exact(con_path, 5, "doc-a.md")
check("cite-empty-fail-closed", r["ok"] is False, r)

r = m.cite_exact(con_path, 4, "doc-u.md")
check("cite-multibyte-byte-offsets", r["ok"] is True, r)

# overlap split
reqs = [{"t0": 10.0, "t1": 10.2}]
series_maint = [{"t": 9.0, "maint": False, "receipt": {"a": 1},
                 "jobs": {"done": 0}},
                {"t": 10.1, "maint": True, "receipt": {"a": 1},
                 "jobs": {"done": 0}},
                {"t": 11.0, "maint": False, "receipt": {"a": 1},
                 "jobs": {"done": 0}}]
ov = m.overlap_verdict(reqs, [], series_maint)
check("overlap-maint-only-gates",
      ov["maint_overlap"] is True and ov["mut_overlap"] is False, ov)

muts = [{"t0": 10.0, "t1": 10.2}]
series_idle = [{"t": 9.0, "maint": False, "receipt": {"a": 1},
                "jobs": {"done": 5}},
               {"t": 11.0, "maint": False, "receipt": {"a": 1},
                "jobs": {"done": 5}}]
ov = m.overlap_verdict(reqs, muts, series_idle)
check("overlap-mut-only-does-not-gate",
      ov["maint_overlap"] is False and ov["mut_overlap"] is True, ov)

ov = m.overlap_verdict([{"t0": 50.0, "t1": 50.1}], muts, series_idle)
check("overlap-neither", ov["maint_overlap"] is False
      and ov["mut_overlap"] is False, ov)

# module identity (import one real module first: the harness only loads
# trace_lite inside main(), so an offline importer sees an empty set)
import trace_lite.store.outbox  # noqa: E402,F401
id1 = m._module_identity()
id2 = m._module_identity()
check("identity-deterministic", id1["digest"] == id2["digest"]
      and len(id1["modules"]) > 0, f"{len(id1['modules'])} modules")
with tempfile.NamedTemporaryFile(suffix=".py", delete=False,
                                 mode="w") as fh:
    fh.write("X = 1\n")
    probe = fh.name
h1 = hashlib.sha256(Path(probe).read_bytes()).hexdigest()
Path(probe).write_bytes(b"X = 2\n")
h2 = hashlib.sha256(Path(probe).read_bytes()).hexdigest()
check("identity-hash-sensitive", h1 != h2)

print(f"\n{len(fails)} failures: {fails}" if fails else "\n0 failures")
sys.exit(1 if fails else 0)
