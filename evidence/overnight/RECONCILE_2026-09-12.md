# TRACE-LITE coordinator phase 1 — RECONCILE (2026-09-12)

Run dir (persistent, unique): `evidence/overnight/RECONCILE_2026-09-12.md`
(this file). No /tmp globs used. All claims below were inspected in the
candidate working tree this session; supplied history treated as context only.

## 1. Candidate manifest

- HEAD: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` ("docs(query-native): update coordinator guide")
- Branch: `main`. HEAD alone is INSUFFICIENT: tree is dirty (candidate = HEAD + diff + untracked).
- Tracked diff: 17 files, +4630/−316. Largest: `src/trace_lite/api/app.py`
  (+1719), `src/trace_lite/filing/holon.py` (+1007), `engine.py` (+682),
  `store/schema.sql` (+318), `router/cascade.py` (+315), plus docs/benchmarks.
- Relevant untracked (program, not scratch): `benchmarks/s6_envelope.py`,
  `s6_serve_under_drain.py`, `s6_concurrent_serve.py`,
  `run_integrated_qualification.py`, `blackbox_native_check.py`;
  `src/trace_lite/{cordis/dossier.py, filing/workers.py, filing/reconcile.py,
  graph/, obsidian/semantics.py, obsidian/structure.py, router/{encoder,
  index_adapter, learned, projection, vector_store}.py, source/,
  store/{generations, migrate, outbox}.py, experimental/}`;
  25 untracked `tests/test_*.py` + `tests/conftest.py`;
  `docs/builds/query-native/{PRAXIST_MANDATE,STEERING_ASSESSMENT,SUPPLEMENT_S_BINDINGS,
  WINDOWS_COORDINATOR_PROMPT}_2026-09-12.md`; `evidence/query-native/`;
  `run_win.bat`.
- Runtime/platform: Linux/WSL2 (`6.6.87.2-microsoft-standard-WSL2`, x86_64).
  Native Windows run NOT executed this session (delivery.md says so explicitly).
- Dependencies (from `.venv`): pydantic 2.13.4, numpy 2.4.6, fastapi 0.141.1,
  pytest 9.1.1, requires-python >=3.11.
- Feature flags (inspected): `TRACE_LITE_SERVED_PERSISTENT` (default off),
  `TRACE_LITE_SERVED_INDEX_DIR`, `TRACE_LITE_SERVED_DISK_BUDGET_BYTES`,
  `TRACE_LITE_QUEUE_LAG_BOUND_S` (default 60), `TRACE_LITE_WORKER`,
  `TRACE_LITE_WORKER_POLL_S`, `TRACE_LITE_PORT` (default 8420).
- Artifact identities: `evidence/query-native/_integration/{S0_ledger.md,
  candidate_manifest.json, path_matrix.md, INT-1, INT-2, INT-3, S1..S6, WIN}`;
  S6: `delivery.md`, `envelope.json`, `envelope_prior_partial.json`,
  `envelope_run.log`, `s6_online_mutation_2k.json`, `s6_2k_console.log`,
  `s6_full_suite.log` (315 passed, 6 skipped — claimed, not re-run here).

## 2. Interpreter verification (both prior claims resolved)

- `.venv/bin/python` -> `/home/anshul/.local/share/uv/python/
  cpython-3.11-linux-x86_64-gnu/bin/python3.11` — symlink VALID, works.
- `.venv` version: **3.11.15**. System `python3`: 3.12.3 (`/usr/bin/python3`).
- `pytest` exists ONLY via `.venv` (`pytest 9.1.1`); no system pytest.
- Verdicts: "claimed .venv Python 3.12" CONTRADICTED (it is 3.11.15);
  "old critique broken 3.11 symlink" CONTRADICTED (symlink resolves, runs).
  No repair needed; existing envs preserved; no new env created.

## 3. Claim ledger (claim | source location | runtime path | verdict)

Verdicts: implemented / tested / measured / qualified / deferred / contradicted.

1. `app.py` mutation paths call bounded synchronous drain — `app.py:33`
   (`from ..source import project as projection`), calls at :1124 (replay),
   :1135 (ingest), :1162 (retract), :1220 (sync batch), :1464 (startup-resume)
   -> `source/project.py:494 drain_outbox` (claim->project->complete/fail).
   CORRECTION: module is `source.project`, NOT `router/projection.py`
   (which has no `drain_outbox`). Reachable via ingest/sync/watcher/startup.
   Verdict: **implemented, tested** (INT-1 + S3 suites; module-name caveat).
2. `_build_reader`/`_refresh_reader` call `_warm_reader` which calls
   `router.hybrid.warm()` — `app.py:778-827`, `app.py:743-775` (:771).
   `FlatHybrid.warm` (`router/hybrid.py:39`) is SELECTs + numpy (read-only).
   Runs on EVERY reader build/refresh in both legacy and persistent modes;
   persistent mode only ADDS the ANN sidecar pin. Verdict: **implemented**.
3. `_ensure_served_persistent_view` can detach vectors during maintenance or
   snapshot-lock contention — `app.py:887-933`: detach on
   `maintenance_running()` (:910), non-blocking lock failure (:914), unservable
   base (:926), exceptions (:930). Docstring matches runtime exactly.
   Verdict: **implemented** (honest-detach guarantee, not availability guarantee).
4. `s6_envelope.py` calls `worker.run_until_drained()` per rung — line :153,
   on a direct DB handle BEFORE app creation; generation published after.
   So envelope rungs measure a DRAINED corpus + bulk import, not active backlog.
   Verdict: **implemented, measured** (bulk/drained operating point only).
5. `s6_serve_under_drain.py` alternates `run_once()` with queries — lines
   :93-112: SEQUENTIAL same-thread loop (1 batch, then 6 queries). App ALSO
   runs its own background served seed/catch-up daemon (`ServedRuntime`,
   `served_after_write` :597). Worker ownership is split (external
   `MaintenanceWorker` on a separate `Database` handle + app-owned served
   thread). Zero concurrency NOT established; harness proves neither required
   overlap nor worker ownership. Verdict: **implemented** as sequential probe;
   any "non-blocking/concurrency" reading is **contradicted** by the code.
6. Probe records status `atoms` as sources; `ok` = end servability + disk
   budget + nonempty latency — line :83 (`n_sources = status["atoms"]`,
   live lexical rows, not jobs/vectors); :139-140 ok predicate. Enforces
   nothing about concurrency, latency bounds, retrieval correctness, or
   backlog progress. Verdict: **implemented** (narrow instrument, as written).
7. Praxist mandate active — `PRAXIST_MANDATE_2026-09-12.md` header: "Pre-Spec
   (draft, coordinator-owned)", full init gated on post-S4 commit+push, which
   has NOT happened (tree dirty, no push). Resume-history "post-S6 activation"
   matches neither the mandate's gate nor the tree state.
   Verdict: **deferred** (draft; prerequisites unmet).
8. O(N^1.85) scaling law — `delivery.md:108-110` fits 2 points (200, 2k
   online-mutation timings) and ADMITS "two points make a trend, not a law".
   Verdict: **measured** trend (2 points), NOT a law; 10k/50k extrapolations
   (85 min / 28 h) unvalidated.
9. R09 qualification — STATE.md: NEEDS_REPAIR, "B3 FAIL (qualification not
   achieved...)". Steering: 2k CONDITIONAL with caveats; 10k record
   invalidated by operator error; 100k+ unrun. Verdict: **deferred**
   (diagnostic evidence only). 10k full-drain REFUSED per steering line 89
   until per-write cost is bounded.

## 4. Frozen acceptance contract (S0-S6)

Do NOT adopt "fully qualified" or "2k qualified" from narratives. Frozen terms:

- Bulk publication vs online HTTP mutation are SEPARATE operating points with
  separate runs: bulk app-path import (~1040/s linear at 2k/10k) vs
  per-note `ingest_note` (R01 publish + sync drain + generation each;
  54/s at 200 docs -> 7.7/s at 2k). Never mix into one column.
- Count nouns: documents (input files) vs active revisions vs atoms (live
  lexical rows) vs vectors (served ANN entries) vs queued jobs (outbox depth).
  Drain estimates recompute from MEASURED pending-job counts, not doc counts.
- Persistent-component perf (seed/open/footprint) vs full API request perf
  (HTTP query incl. warm paths) are separate numbers.
- Lexical fallback serving vs persistent vector availability: detach is the
  honest outcome; a fast query with `vector_generation_id None` is a
  fallback data point, not a vector-serving data point. Pin rate
  (`vector_generation_id == generation_id`) must be reported alongside latency.
- Drained corpus (post-`run_until_drained`) vs active backlog (nonzero depth
  during serving) are separate regimes; the envelope covers the former only.
- Extractor implementation vs yield at fixed precision: S5 extractor exists
  (17 tests + trial per STATE.md); yield-vs-regex-baseline at fixed precision
  is the unmet Praxist-target-B measurement, not yet a result.
- Delivery table (`delivery.md` operating-point table) mixes sibling 2k bulk
  numbers into the 10k column: REQUIRE separately identified runs per cell
  (driver, commit+diff manifest, DB path, gate flags) before any comparison.
- Two corpus sizes do not establish O(N^1.85): need >=3 sizes + job-count-based
  model + profiling (current suspects — per-ingest facet reseed/centroid
  refresh, coverage maintenance — are code-reading, NOT profiled).
- Preserved: R09 NEEDS_REPAIR; refusal of unbounded 10k full-drain and of
  lifecycle rungs above 2k on the online path until per-write cost is bounded.

## 5. Shared interfaces + STRICT lane ownership

Interfaces (read-only for all lanes; changed only by coordinator):
`source/service.py` (publish/retract), `source/project.py::drain_outbox`,
`store/outbox.py` (queue_stats/job_progress), `store/generations.py`,
`api/app.py::route_pinned` signature + `/api/status` existing keys,
`router/__init__.py::ensure_served_persistent`.

- Lane A (write-path cost): `src/trace_lite/filing/engine.py`,
  `src/trace_lite/source/coverage_state.py`, `src/trace_lite/filing/holon.py`,
  `benchmarks/run_trace_lite_benchmark.py`. Owns profiling + bounded per-write
  cost. Must NOT touch app.py query path, router/*, or harnesses.
- Lane B (served/query persistent path): `src/trace_lite/router/cascade.py`,
  `src/trace_lite/router/vector_store.py`, `src/trace_lite/router/hybrid.py`,
  `src/trace_lite/router/index_adapter.py`, `src/trace_lite/router/learned.py`,
  `src/trace_lite/router/encoder.py`. Owns ANN attach/detach + pin semantics.
  Must NOT touch app.py, filing/*, source/*, or harnesses.
- Lane C (harness/evidence/contracts): `benchmarks/s6_envelope.py`,
  `benchmarks/s6_serve_under_drain.py`, `benchmarks/s6_concurrent_serve.py`,
  `benchmarks/run_integrated_qualification.py`,
  `benchmarks/blackbox_native_check.py`, `tests/test_s6_envelope.py`,
  `evidence/overnight/*`. Owns run identification + ok-predicate hardening
  (latency bound, pin rate, backlog-progress assertions). Must NOT touch src/*.

FORBIDDEN for all lanes: `git add -A`, broad staging, commit, push,
destructive cleanup (`rm`, `git clean`, bury scripts), edits outside owned
files, interface-signature changes. Coordinator integrates.

## 6. Next actions

1. Lane C: harden `s6_serve_under_drain.py` ok predicate (latency bound, pin
   rate, depth-delta assertion) + per-cell run manifests; re-run 2k.
2. Lane A: profile per-ingest cost at 200/2k (facet reseed vs coverage vs
   drain); propose bounded write path; no 10k online ladder before this.
3. Lane B: close pin-rate gap (served `holon_guided` parity for probes);
   document detach-vs-serve accounting for every latency sample.
4. Coordinator: commit/push gate decision (Praxist post-S4 prerequisite still
   open); keep R09 NEEDS_REPAIR until C9-3/C9-4 + dossier wording fixed.
