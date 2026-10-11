# Lane A checkpoint 02 — repair + verification (cycle 1)

## Changes (owned files only)

1. `src/trace_lite/filing/holon.py` (+~120 lines, no signature changes):
   - `FULL_REFRESH_LIMIT = 64` gate.
   - `add_members`: small holons keep the exact full `_refresh_state`
     (bit-identical); large holons try `_fold_state_for_add` first.
   - `_fold_state_for_add`/`_fold_state_inner`: digest-gated fold — encodes
     only new members O(k*d), folds S/W/count, recomputes digest exactly
     over narrow live rows, appends version snapshot; descriptor/
     representatives carried. Any anomaly (cascade delete, foreign
     insert, missing/dim-shifted sums, count drift, any exception) ->
     False -> exact full recompute (self-healing oracle).
   - `refresh_derived(holon_id)`: public explicit full recompute (remedy
     for carried fields; used by tests; available to worker/coordinator).
   - Module + method docstrings corrected (O(k*d) claim now precise).
2. `src/trace_lite/source/coverage_state.py` (+11 lines, additive):
   - `idx_cov_job_rev ON projection_job(input_revision_id, state)` in the
     drain-path ensure; removes O(done-jobs) scans from job triggers and
     per-revision job lookups. No semantic change.
3. `tests/test_lanea_write_cost.py` (NEW, 8 tests): fold-vs-oracle
   exactness, carry-until-refresh, cascade heal, small-holon exactness,
   concurrent-add safety + heal, index presence/use, bounded-encode
   counting test + harness self-check.
4. Run dir `evidence/overnight/lanea-20260912-1930/` (unique subdir; does
   not collide with Lane C files): profiler scripts + pre/post JSON +
   cProfiles + checkpoints.

Untouched as required: app.py, project.py, service.py, outbox.py,
workers.py, generations.py, router/*, benchmarks/*, existing tests,
schema.sql. No commit/staging/push; no destructive ops.

## Verification (observed this session)

- New tests: 8 passed in 10.3s.
- Full suite: 322 passed, 6 skipped, 1 failed in 72s. The failure
  (`test_s6_envelope.py::test_cutover_serves_persistent_after_background_seed`,
  `assert 2 == 3` seed atoms) reproduces IDENTICALLY with candidate-base
  holon.py + coverage_state.py (3/3 with fix, 2/2 on base) -> pre-existing,
  Lane B/C territory, not a regression. Not touched.
- Insert profile (400 notes, /tmp DB): 61.0s -> 15.7s end-to-end
  (6.56 -> 25.45 notes/s); per-note windows 44->247ms PREFIX vs
  33->53ms POSTFIX; cProfile window at N~190: add_members 3.94s -> 0.058s
  (68x), text_vector calls 7720 -> 120 (64x). Residual: sqlite commits
  45% (25 commits/note, app.py-side batching, not mine), persist_graph
  O(doc), ensure_coverage_schema 1.5ms/call.
- Edit profile (50 edits at N=400): mean 243ms/edit (unchanged by design:
  gate-miss -> full recompute + facet reseed; caller-side fix proposed
  as F2). Spot checks: 400 atoms, 400 current, 50/50 edited grouped,
  0 pending jobs, corpus drift ok.
- Fold-gate scaling (direct API): 2.5ms@500 / 4.2ms@1000 / 9.2ms@2000
  (~4.5us/member) — the documented residual linear term (~45ms at 10k).

## Repair-retest cycles used: 1 of 2. No new failures from this change.

## Limitations (carried to handoff)

- L1 descriptor/representatives on large holons lag between full
  recomputes (nothing served reads them; no committed test pins large-
  holon values; `verify_rebuild_equivalence` on a folded corpus flags
  descriptor until `refresh_derived`; falsifier: run oracle immediately
  after bulk ingest without refresh).
- L2 per-write is O(1)-heavy + O(m)-light (digest gate ~4.5us/member),
  not strict O(k); strict needs app.py-side detach-before-delete (F2).
- L3 edits unchanged (~243ms at N=400): full recompute + reseed (F2).
- L4-L8: findings F1 (invalidation crash gap), F3 (replay-drain w/o
  bump), F4 (bounded restart drain), F5 (claim race, benign), F6
  (retract staleness until next add) need frozen/caller files.
- L9: S6 seed test pre-existing failure (above).
