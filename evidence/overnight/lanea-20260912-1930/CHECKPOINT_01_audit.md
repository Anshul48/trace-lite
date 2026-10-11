# Lane A checkpoint 01 — audit complete (2026-09-12 19:30 UTC run dir)

Worktree: `.../subagent-v2-01a09709-...-2f09-...` (candidate state copied from
`/tmp/tl-cand`; HEAD 0df044b + dirty diff + untracked program files).
Interpreter: main-repo `.venv` Python 3.11.15, `PYTHONPATH=<worktree>/src`
(verified: imports resolve to the worktree, not the main checkout).

## Ownership decision (binding for this lane)

Task text orders "mutation and durable-worker integration" repair but also
"ONLY edit your owned files" per "reconcile report via ref". Two reconcile
docs exist with different lane splits:

- `evidence/overnight/RECONCILE_2026-09-12.md` (= prior result 1, Phase-1
  output): Lane A = `filing/engine.py`, `source/coverage_state.py`,
  `filing/holon.py`, `benchmarks/run_trace_lite_benchmark.py`.
- `evidence/query-native/_integration/S6/RECONCILIATION.md` (older): Lane A =
  app.py mutation regions, workers.py, outbox.py, service.py.

"Reconcile ref prior result 1" resolves to the overnight doc. To satisfy
BOTH docs' prohibitions, this lane edits ONLY intersecting-safe files:

- EDIT: `src/trace_lite/filing/holon.py`, `src/trace_lite/source/coverage_state.py`
  (+ new additive test file + this run dir). `filing/engine.py`: audit-only
  unless measurement forces it (its oracles must stay exact).
- READ-ONLY: app.py, project.py, service.py, outbox.py, workers.py,
  generations.py, router/*, benchmarks/*, existing tests, schema.sql.

Consequence: caller-sequencing repairs (detach-before-delete, drain/bump
ordering, claim atomicity) are FINDINGS for the coordinator, not edits.

## Measured defect D1 (the repair target)

Per-note `ingest_note` cost grows linearly with corpus N (400-note run,
`profile_baseline.json`): 44ms (notes 0-49) -> 247ms (notes 350-399).
cProfile window at N~190 (20 notes, 5.08s): `drain_outbox` 88%,
`ensure_grouped` 78%, `HolonStore.add_members -> _refresh_state` 76%
(`_exact_accumulators` 33%, `_representatives` 35%, `_descriptor` 7%;
7720 `text_vector` calls = ~2 full-corpus re-encodes per ingest).
Reachable path: `POST /api/ingest -> ingest_note -> drain_outbox ->
project_revision -> ensure_grouped -> add_members -> _refresh_state`.
Contract conflict: module docstring promises "update incrementally in
O(k*d)"; implementation recomputes from all member texts per add.
Sync grouping is REQUIRED (tests: 2nd ingest -> coverage all-true,
`corpus_holds_new_atom` after edit, `index_trusted` True), so the fix
keeps sync membership and makes state maintenance incremental.

## Audit findings (no edit; coordinator follow-ups)

- F1 invalidation crash gap: `publish` commits, `_invalidate_for_edit`
  runs later in a separate txn; a crash between leaves dependent
  syntheses wrongly `active` with no pending signal. Fix needs
  service.py/app.py (frozen) -> finding only.
- F2 reseed-on-edit: app `_repair_facet_state` = 2 full member scans per
  old facet per edit. Exact O(k*d) alternative (`detach_atom` before
  publish) needs app.py plumbing -> finding only (+ edit-phase profile
  to bound it).
- F3 replay-drain without bump: replay runs a bounded drain that can
  complete backlog without a `filing` generation bump (cf.
  worker `_maybe_publish` precedent); projections stay invisible until
  the next bumping write. app.py-side -> finding only.
- F4 startup/shutdown drains are capacity-bounded (64 / 4x64); large
  crash backlogs with worker off + no further writes stay pending
  (honest via `/api/status`, not auto-driven). app.py-side -> finding.
- F5 `claim()` SELECT-then-UPDATE is non-atomic across conns; same-job
  double projection is wasteful but self-healing (idempotent stages +
  PK guard + fail/retry). outbox.py frozen -> finding only.
- F6 retract leaves corpus derived state stale until the next add
  recomputes (membership rows cascade-clean; state lags). Pre-existing;
  preserved (fold gate falls back to full recompute and heals).
- F7 worker ownership model (established by reading): per-job leases
  (30s floor) + idempotent stages + lifecycle order
  (resume-drain -> serve -> worker-stop -> shutdown-drain). Concurrent
  adds are membership-safe (PK) and derived-self-healing (digest gate);
  proven by new test, not by thread counting.

## Repair plan (cycle 1)

- R1 `filing/holon.py`: size-gated incremental `add_members`: small
  holons keep exact full `_refresh_state` (bit-identical); large holons
  take a digest-gated fold (S/W/count exact to fp-assoc, digest exact,
  version snapshot exact; descriptor/representatives carried, explicit
  `refresh_derived()` remedy). Any gate anomaly -> full recompute
  (the oracle; self-healing). No signature changes; no caller edits.
- R2 `source/coverage_state.py`: add `projection_job(input_revision_id)`
  index to the drain-path ensure (trigger `_job_flags_sql` + lookup
  scans are otherwise O(done-jobs) per write). Additive, no semantics.
- R3 new `tests/test_lanea_write_cost.py`: fold exactness (drift, digest,
  count, versions vs full-rebuild oracle), gate-miss healing, threshold
  boundary, concurrent-add membership safety, index presence, 200-note
  scaling bound (flat-ish per-note after warmup).
- R4 re-run: full suite + profiler (insert + edit phases) in this run dir.
