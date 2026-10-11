# S6 resumed-session reconciliation (coordinator, 2026-09-12)

Supplied resume history and old critique are context only. This file records
the actually observed candidate. HEAD alone is insufficient: the tree is dirty.

## 1. Candidate identity

- HEAD: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8`, branch `main`.
- Tracked dirty (17): `COORDINATOR_LAUNCH_PROMPT.md`,
  `benchmarks/run_trace_lite_benchmark.py`,
  `docs/builds/query-native/{COORDINATOR,STATE}.md`,
  `src/trace_lite/api/app.py`, `cordis/{__init__,compiler,models}.py`,
  `filing/{__init__,engine,holon}.py`, `obsidian/{__init__,parser}.py`,
  `router/{__init__,cascade}.py`, `store/{__init__,schema.sql}`.
- Untracked (relevant): `benchmarks/{s6_envelope,s6_serve_under_drain}.py`,
  `src/trace_lite/{source,filing/workers,filing/reconcile,router/*,store/outbox,
  store/generations,graph,obsidian/structure,obsidian/semantics}/`,
  `tests/test_s6_envelope.py` + ~20 more test files,
  `evidence/query-native/`, `PRAXIST_MANDATE_2026-09-12.md`,
  `STEERING_ASSESSMENT_2026-09-12.md`, `run_win.bat`.
- Runtime/platform: Linux WSL2 (ext4 `/tmp`, 926 GB free), repo on 9p mount.
- Interpreter: **`.venv/bin/python` = CPython 3.11.15** (uv-managed,
  `.../cpython-3.11-linux-x86_64-gnu/bin/python3.11`, target exists and runs).
  The resume narrative's "Python 3.12" is **contradicted**; the old critique's
  "broken 3.11 symlink" is **contradicted** (symlink resolves, interpreter
  works). No repair needed; `.venv` preserved as-is (no `pip` module; `uv`
  0.12.9 available). Deps observed: numpy 2.4.6, fastapi 0.141.1,
  pydantic 2.13.4, pytest 9.1.1.
- Feature flags: `TRACE_LITE_SERVED_PERSISTENT=1` (persistent cutover),
  `TRACE_LITE_SERVED_INDEX_DIR`, `TRACE_LITE_WORKER=1` (S3 worker),
  `TRACE_LITE_QUEUE_LAG_BOUND_S` (default 60 s).
- Frozen S6 budgets (app.py): lag 60 s, headroom 10x, freshness 1.0 s,
  shutdown join 30 s.

## 2. Claim ledger (revalidated against this checkout)

| # | Claim | Source location | Reachable runtime path | Existing evidence | Missing evidence | Verdict |
|---|---|---|---|---|---|---|
| 1 | Sync projection drain in mutation paths | app.py:1114 (replay), 1125 (`ingest_note`), 1152 (`remove_note`), 1210 (`sync_notes`), 1454 (startup), 1526 (shutdown) | `POST /api/ingest` -> `ingest_note` -> `drain_outbox` under `write_lock`; same for sync/remove | delivery.md 200->2k timing (3.7 s -> 260 s) | profile of drain internals; per-write cost after repair | IMPLEMENTED, MEASURED (defect demonstrated) |
| 2 | Reader warm re-encodes corpus on query path | app.py:743 `_warm_reader` -> 771 `router.hybrid.warm()`; callers 786 `_build_reader`, 821 `_refresh_reader` <- 830 `get_reader` <- 959 `route_pinned` <- `POST /api/query` | Every generation advance on any query thread runs `SELECT id,text FROM atom` + full matrix rebuild (hybrid.py:47-48) | code read; S6 test "0 open encodes" covers only `ServedVectors.open`, not this path | measured warm cost at 2k on query thread | IMPLEMENTED (defect demonstrated by reading; NOT legacy-only) |
| 3 | Persistent view detaches under maintenance/lock contention | app.py:910-917 `_ensure_served_persistent_view` | `route_pinned` with gate on, whenever `maintenance_running()` or lock busy | S6 test asserts detach-then-recover | availability measurement under real overlap | IMPLEMENTED, TESTED (unit); availability under load UNMEASURED |
| 4 | Envelope runner drains per rung, ingests library-direct | s6_envelope.py:125 `svc.publish` loop, 153 `run_until_drained`, 168 boots app only after | bulk `svc.publish` != HTTP mutation; app started post-drain | envelope.json rungs | none (runner semantics confirmed by reading) | IMPLEMENTED (harness flaw: "bulk import (app path)" label contradicted) |
| 5 | Under-drain probe is sequential, adds 2nd writer, mislabels counts, weak ok | s6_serve_under_drain.py:83 `n_sources=status["atoms"]`, 93-94 sequential `run_once`+queries, 87 own `MaintenanceWorker` beside app `ServedRuntime` thread, 139-140 ok | single thread, no overlap timestamps; two projection writers + sync drains contend on WAL | none | overlap proof, latency/correctness/backlog predicates | IMPLEMENTED (falsifier inadequate; do not run as qualification) |
| 6 | "Decoupled durable ingest / single worker / never re-encodes" | app.py docstrings + ServedRuntime | contradicted by rows 1-2 on the exact gated paths | unit tests (10 S6) | end-to-end proof | CONTRADICTED as runtime guarantee; docstring only |
| 7 | O(N^1.85) scaling law | delivery.md:108-110 (admits "two points make a trend, not a law") | 200-doc + 2k-doc online-mutation timings | two operating points | >=3 sizes, job-count-based model | MEASURED trend, NOT a law; 10k extrapolation unvalidated |
| 8 | 10k column in delivery table | delivery.md:98-106 | mixes 2k app-path numbers under "10k (sibling, bulk)" header | envelope.json (2k only; 10k never ran post-reboot) | any 10k run on this candidate | CONTRADICTED presentation; must be split per-run |
| 9 | R09 NEEDS_REPAIR; 10k full-drain refused | STATE.md:21, delivery.md:108-120, steering stopping rules | n/a (status) | prior R09 review | n/a | PRESERVED |
| 10 | Praxist mandate ready post-S6 | `PRAXIST_MANDATE_2026-09-12.md:3,10`: "AUTHORING IN PARALLEL (S2/S3)", "Full init runs only post-S4 commit+push", draft | n/a | draft text only | reconciled prerequisites; Target A/B status | DEFERRED (draft; activation not authorized by this file) |
| 11 | Full suite 315 passed / S6 10 passed | tests/test_s6_envelope.py, s6_full_suite.log | pinned to prior tree state, not this dirty tree | log files (stale vs current tree) | rerun on current candidate | TESTED (stale binding; must rerun) |

## 3. Frozen acceptance contract (what evidence actually establishes)

- Established: R00-R08 per STATE.md (statuses kept); S1-S5 component tests;
  S6 unit envelope (10 tests, binding stale per row 11); bulk `svc.publish`
  throughput shape (linear-ish at 2 points); online-mutation cost defect
  (2 points, trend only); R09 NEEDS_REPAIR; 10k-full-drain refusal.
- NOT established: any "fully qualified" or "2k qualified" release claim;
  persistent-path request latency under backlog; worker ownership across
  lifecycle; snapshot lifetime safety; Target A/B readiness.
- Mandatory distinctions for all future evidence: bulk `svc.publish` vs HTTP
  `POST /api/ingest`; documents vs active revisions vs atoms vs vectors vs
  queued jobs; component timings vs full request timings; lexical fallback vs
  vector availability; drained corpus vs active backlog; extractor
  implementation vs yield at fixed precision.
- Drain estimates must use measured pending-job counts, never doc counts.

## 4. Repair + qualification order (lanes)

- A (mutation/worker): app.py ingest/replay/retract/sync/startup/shutdown
  drain paths; outbox lifecycle; worker ownership. Owns: `src/trace_lite/api/
  app.py` mutation paths, `src/trace_lite/filing/workers.py`,
  `src/trace_lite/store/outbox.py`, `src/trace_lite/source/service.py`
  (coordinate service.py via coordinator).
- B (serving): `_warm_reader` corpus encode on query path; snapshot
  publication/lifetime; detach-vs-stale policy; generation labeling;
  visibility filtering. Owns: `route_pinned`/`_ensure_served_*`/`get_reader`
  regions of app.py (serialized with A through coordinator),
  `src/trace_lite/router/`, `src/trace_lite/store/generations.py`.
- C (harness/evidence): new bounded falsifier replacing both probes;
  run-dir manifests; count semantics; budgets/stop rules; delivery.md
  per-run operating points. Owns: `benchmarks/`, `evidence/.../S6/`.
  Must not edit A/B files.
- D (review/research): independent patch review; bounded external research
  (<=US$5, <=2 concurrent, generic descriptions only, no repo contents).
- No `git add -A`, no commit, no push without fresh session authorization.
  No `s6_envelope.py --rungs 2000,10000` unchanged. No unbounded 10k drain.
