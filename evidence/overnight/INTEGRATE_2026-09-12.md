# TRACE-LITE overnight lanes — INTEGRATE (2026-09-12 ~19:55 UTC)

Candidate: HEAD `0df044b` (main) + dirty diff + untracked program files.
Interpreter: `.venv` Python 3.11.15, pytest 9.1.1. No commit/push/stash/reset;
no broad staging; no destructive ops. Commit contents + message prepared only
(see `COMMIT_MSG_PREPARED_2026-09-12.md` — needs independent authorization).

## Integrated changes (3 batches, checks after each)

Batch 1 — Lane B (router): `cascade.py`, `hybrid.py`, `vector_store.py`
(byte-identical to lane worktree; sha256 match post checksums
bf057f3b/c017efdb/80b2d042). Base checksums verified equal to live main
before copy. R1 delta warm, R2 pin-keyed live-id cache + `_vector_base_generation`,
R3 versioned materialize cache + `release()`/`describe()` + additive ensure keys.
Affected suites: test_router_hybrid/router/learned/index_adapter 38 passed
1 skipped; served_path_integration/s4_vectors 18 passed.

Batch 2 — Lane A (write path): `filing/holon.py` (FULL_REFRESH_LIMIT=64
digest-gated fold + `refresh_derived`, atomic try/commit/rollback verified
by read), `source/coverage_state.py` (additive idx_cov_job_rev),
`tests/test_lanea_write_cost.py` (new, 8 tests). Lane worktree app.py/engine.py
copies were STALE (missing main-side dimension-root + remove_members fixes);
main versions preserved, only owned files copied.
Affected suites: new 8/8; holon/write_path/filing/cross_source/lifecycle 53 passed.

Batch 3 — Lane C (harness/evidence): `benchmarks/s6_concurrent_serve.py` v4 +
`test_s6c_v4_helpers.py` were already in main (byte-identical, verified);
integrated `delivery.md` (mixed 10k column split into tables A/B/C, computed
9.6 s cell REMOVED, cycle-2 appendix) + `s6_concurrent_serve.spec.md` v2.
Two attribution slips corrected with coordinator notes (main-tree drift, not
lane edits — lanes never touched frozen app.py/engine.py). Lane run dirs
(lanea-20260912-1930, lane-b-4321-20260912 text artifacts) preserved under
`evidence/overnight/`; bulky lane scratch `*.db`/`served/` stay in lane
worktrees. V3 run records, RECONCILIATION.md, R09 NEEDS_REPAIR, and the
unbounded-10k-full-drain refusal all preserved.

## Verification (observed this session, integrated tree)

- Full suite: **323 passed, 6 skipped, 1 failed** in 62 s. The failure
  (`test_cutover_serves_persistent_after_background_seed`, seed atoms 2==3)
  reproduces on pristine pre-lane `/tmp/tl-cand` (no lane files) → pre-existing,
  app.py seed timing + test expectation; out of lane scope. Untouched.
- V4 helper offline self-test: 0 failures.
- Coordinator v4 smoke `runs/s6c_20260912-195425` (quiesced tree, serialized):
  harness v4, absolute manifest paths, app_repo non-null, candidate 0df044b +
  diff sha; 800 queries ok / 0 err, 10 muts ok / 0 timeouts (mut_max 4465 ms,
  first-mut sync drain down from v3's 10447 ms escape); span 44.95/45 s
  (span_ok), 11 distinct gens (gens_observed), overlap a/b/c = 50/198/368
  timestamp-proven, jobs done +130 (120→0 depth), all 13 predicates true,
  `ok: true`. Count nouns separated (166→176 docs/revs/atoms; depth in jobs).
- Lane B probes re-run integrated: R1_OK (1-encode delta, 0.999999 fidelity,
  route-exact vs full); R2_OK; R3_OK (cold 5.5 ms → hot 0.003 ms); R3_ENSURE_OK.
- Independent probe (`/tmp/tl-integrate-verify/my_probe.py`, kept in /tmp):
  ingest 10.9→7.7 ms/note flat over 150 notes; warm 150 full → 1-encode delta;
  content_version 1→2 on overlay ingest; shells share immutable mat, own
  tombstones. INTEGRATE_PROBE_OK.
- Interfaces: `route_pinned`, `drain_outbox`, `publish`/`retract`, outbox,
  generations, `ensure_served_persistent(router, served, ...)` signatures
  unchanged (vector_store return keys purely additive). Lifecycle suites green.
- Search purity re-verified by read (`index_adapter.search*` pure; `live_ids`
  fresh set minus tombstones; shells safe). `remove_atom` keeps _ids/_digests
  holes parallel → delta gate sound. Cascade cache ghost window needs
  out-of-band atom delete without tombstone (narrow; documented).

## Praxist reconcile (mandate still draft, post-S4 commit+push gate unmet)

- Target A (non-blocking queue + lock-free swap): IMPLEMENTED as mechanism;
  non-blocking evidence upgraded to smoke-scale timestamp overlap (v4 run
  above) but 2k concurrent run ungated-yet-unrun → EXPERIMENTAL, not qualified.
  Unfinished Target A is not completed architecture.
- Target B (syntactic extractor): PARTIALLY IMPLEMENTED (S5 structure extractor
  + rules arm, not high-yield syntactic parsing); yield-vs-regex at fixed
  precision unmeasured → EXPERIMENTAL. S5 tests alone do not establish it.
  Untouched by overnight lanes.
- S4 non-goal respected (no tuning; `S4_DELTA_BOUND` untouched). No task-init.

## Failures / deferred / next action

- Pre-existing cutover seed failure (above) — needs app.py seed-timing owner.
- Bounded 2k v4 run NOT attempted (gated on this smoke — now unblocked, needs
  coordinator approval + quiesced tree + serialized DB access).
- Follow-ups: B4/B5 app.py surfacing of pin-vs-base + content_version (needs
  coordinator app.py edit); Lane D reclamation grace rule (no call sites until
  falsification test passes); Lane A F1–F6 caller-side findings; fold
  Exception-swallow counter/log (A1, non-blocking).
- Release boundary: NO release claim; no "2k qualified"; R09 NEEDS_REPAIR stands.
- Approvals needed: (1) independent commit/push authorization (message prepared);
  (2) bounded-2k v4 run approval; (3) B4/B5 app.py wiring scope.
