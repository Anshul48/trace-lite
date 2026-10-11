# PREPARED commit message — NOT committed, NOT pushed (needs independent authorization)

## Proposed subject

overnight: integrate lanes A (write cost), B (served path), C (v4 harness)

## Proposed body

Lane A (filing/holon.py, source/coverage_state.py, tests/test_lanea_write_cost.py):
size-gated incremental holon maintenance (FULL_REFRESH_LIMIT=64; digest-gated
O(k*d) fold for large-holon pure adds, exact full recompute otherwise +
self-healing fallback + refresh_derived remedy); additive idx_cov_job_rev.
Insert profile 61.0s -> 15.7s / 400 notes (lane-measured); 8 new tests.

Lane B (router/cascade.py, hybrid.py, vector_store.py): delta-aware warm
(O(delta) encodes, frozen IDF, full=True rebuild, last_warm accounting);
pin-keyed live-id cache + _vector_base_generation introspection; versioned
materialize cache (pointer-speed repeat attach) + release()/describe() +
additive ensure keys (base_generation_id/delta_size/materialized_cached).
Pin label frozen by contract; skew now observable.

Lane C (benchmarks/s6_concurrent_serve.py v4, test_s6c_v4_helpers.py,
S6 delivery.md + spec v2): timeout_ok/span_ok/gens_observed predicates, even
pacing, mut stats + depth brackets, enriched visibility probe, absolute
manifest paths; delivery.md mixed 10k column split into separately-identified
tables (computed 9.6s cell removed), cycle-2 appendix with per-run forensics,
repro, and verdict (v4 delivered, smoke-qualified only).

Coordinator verification: full suite 323 passed/6 skipped/1 pre-existing
failure (cutover seed 2==3, reproduced on pre-lane base); v4 smoke
s6c_20260912-195425 all 13 predicates true (800q, 11 gens, overlap proven).
R09 NEEDS_REPAIR + 10k-drain refusal preserved. No 2k-qualified claim.

## Proposed contents

M  src/trace_lite/filing/holon.py
M  src/trace_lite/source/coverage_state.py   (untracked in HEAD; add)
M  src/trace_lite/router/cascade.py
M  src/trace_lite/router/hybrid.py
M  src/trace_lite/router/vector_store.py     (untracked in HEAD; add)
A  tests/test_lanea_write_cost.py
A  benchmarks/s6_concurrent_serve.py
A  benchmarks/test_s6c_v4_helpers.py
A  evidence/overnight/{INTEGRATE,lanea-*,lane-b-*} (reports + lane artifacts)
M  evidence/query-native/_integration/S6/{delivery.md,s6_concurrent_serve.spec.md}
A  evidence/query-native/_integration/S6/runs/s6c_20260912-195425/
