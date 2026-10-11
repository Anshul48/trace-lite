"""Offline verification for s6_concurrent_serve v4 pure helpers (no app, no DB).

Replays evidence shapes from the v3 smoke runs and asserts the v4
predicates now catch the two demonstrated v3 escapes:
  1. run s6c_...-190447 mutation #0 took 10447 ms (timeout=true) yet ok=True
  2. run s6c_...-190049 served all 360 queries in 5.5 s at one frozen gen
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = str(HERE / "s6_concurrent_serve.py")
REPO = HERE.parent
RUNS = os.environ.get(
    "S6C_V3_RUNS",
    str(REPO / "evidence" / "query-native" / "_integration" / "S6" / "runs"))
HAVE_RUNS = os.path.isdir(RUNS)

spec = importlib.util.spec_from_file_location("s6c_v4", HARNESS)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name, detail)
    if not cond:
        fails.append(name)


if HAVE_RUNS:
    # --- 1. v4 escape fix: run3 mut series (10.4 s) must fail timeout ---
    ms3 = [json.loads(l)
           for l in open(f"{RUNS}/s6c_20260912-190447/events.jsonl")
           if '"kind": "mutate"' in l]
    st3 = m.mut_stats(ms3)
    check("run3 mut_timeouts==1", st3["mut_timeouts"] == 1, st3)
    check("run3 mut_max_ms>10000", st3["mut_max_ms"] > 10000)
    check("timeout_ok would be False",
          not (0 == 0 and st3["mut_timeouts"] == 0))

    # --- 2. v4 escape fix: run2 burst span + single gen must fail ---
    def load(d):
        qs = [json.loads(l) for l in open(f"{RUNS}/{d}/events.jsonl")
              if '"kind": "query"' in l]
        r = json.load(open(f"{RUNS}/{d}/result.json"))
        return qs, r["status_series"]

    q2, ss2 = load("s6c_20260912-190049")
    c2 = m.query_coverage(q2, ss2, 40.0, 0.8, 2)
    check("run2 span_ok False (burst)", c2["span_ok"] is False, c2)
    check("run2 gens_observed False (1 gen)",
          c2["gens_observed"] is False and c2["distinct_gens"] == 1, c2)

    q3, ss3 = load("s6c_20260912-190447")
    c3 = m.query_coverage(q3, ss3, 45.0, 0.8, 2)
    # crude whole-log count was 289/800; the helper correctly excludes
    # the 6 hybrid-route queries (vgen None) -> 283/794 vector-only.
    check("run3 vgen_differs 283/794",
          c3["vgen_differs_from_served"] == 283
          and c3["vgen_compared"] == 794, c3)
else:
    print("SKIP run-replay checks (no v3 runs at " + RUNS + ")")

# --- 3. synthetic paced good run passes ---
qs = [{"ok": True, "t0": float(t), "t1": float(t) + 0.05, "route": "vec",
       "vgen": "g1", "gen": "g1" if t < 20 else "g2"} for t in range(0, 40)]
ss = [{"t": float(t), "served_gen": "g1"} for t in range(0, 41)]
cg = m.query_coverage(qs, ss, 40.0, 0.8, 2)
check("paced good run span_ok", cg["span_ok"] is True, cg)
check("paced good run gens_observed", cg["gens_observed"] is True, cg)

# --- 4. stop rule: no double count; fires only on real rate ---
check("stop quiet on 19 errs", m.stop_fired([True] * 19) is False)
check("stop quiet 2/20", m.stop_fired([False] * 18 + [True] * 2) is False)
check("stop fires 3/20", m.stop_fired([False] * 17 + [True] * 3) is True)

# --- 5. pacing interval math ---
check("interval 45/200", abs(m.pace_interval_s(45.0, 200) - 0.225) < 1e-9)
check("interval guards 0", m.pace_interval_s(40.0, 0) == 40.0)

# --- 6. mut_stats on clean series ---
stc = m.mut_stats([{"ok": True, "ms": 100.0, "timeout": False}] * 5)
check("clean muts zero timeouts",
      stc["mut_timeouts"] == 0 and stc["mut_max_ms"] == 100.0, stc)

print(f"\n{len(fails)} failures")
sys.exit(1 if fails else 0)
