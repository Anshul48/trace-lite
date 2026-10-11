# S6 release envelope — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + STEERING S6 row (line 77) + line-89
methodology. Interpreter: `.venv/bin/python`.

S6 closes the two follow-ups S3/S4 deferred here (large-corpus drain
curves; app query-path cutover to persistent serving) and qualifies the
release envelope: cutover correctness, single-thread shutdown/drain,
lag bound, and the old/new-generation + WAL disk check — all on the
same candidate/configuration, legacy path byte-unchanged when gated off.

## Frozen envelope budgets (proposed from dev runs, frozen before confirmation)

| Budget | Constant | Value |
|---|---|---|
| Queue-lag bound | `S6_QUEUE_LAG_BOUND_S` / `TRACE_LITE_QUEUE_LAG_BOUND_S` | 60.0 s |
| Disk headroom | `S6_DISK_HEADROOM_MIN_RATIO` | 10.0× footprint |
| Query-path freshness (S2 re-enforced) | `S6_FRESHNESS_BUDGET_S` | 1.0 s |
| Shutdown join | `SERVED_SHUTDOWN_JOIN_S` | 30.0 s |

## What was delivered

- `src/trace_lite/api/app.py` (S6 cutover, all gated by
  `TRACE_LITE_SERVED_PERSISTENT=1` at startup):
  - `ServedRuntime`: process-wide S4 snapshot + ONE background
    maintenance thread (seed / catch-up, coalesced, daemon).
    Request paths only take `lock` briefly to read/swap the pointer —
    they NEVER join the worker or wait on its progress.
  - `route_pinned` cutover: with the gate on, the ANN branch pins from
    the published base + bounded delta via
    `_ensure_served_persistent_view` (open-only, never builds, never
    encodes corpus, never waits); with the gate off the legacy
    query-triggered build runs unchanged.
  - `served_after_write`: post-commit hook schedules background
    catch-up; non-blocking and exception-free (writers only enqueue).
  - `served_status_block`: additive `/api/status` block (snapshot
    state, seed/catch-up receipts, disk verdict, lag vs bound).
    Read-only: never starts maintenance, never waits on it.
  - `check_disk_budget`: old/new per-generation bytes + DB/WAL files
    against the byte budget AND host headroom ratio.
  - Lifespan: startup opens (open-only) + schedules the first seed;
    shutdown stops scheduling, bounded-joins the maintenance thread
    (a stuck build delays, never hangs), then drains residual
    projection jobs under the writer lock.
- `tests/test_s6_envelope.py` (10): env-helper defaults/overrides,
  legacy path unchanged when gated off (no status key, legacy pin
  contract holds), cutover serves the persistent view after the
  background seed (receipt atoms == 3, CURRENT + cache on disk,
  steady-state query inside the 1 s freshness budget with
  `vector_generation_id == generation_id`), first query under a stuck
  seed stays fast with an honest detach (`vector_generation_id None`)
  then recovers, single maintenance thread with no duplicates and no
  leak after teardown, shutdown drains out-of-band projection jobs to
  zero, lag measured against the frozen 60 s bound (loaded and
  drained), disk budget over two co-resident generations + WAL (tiny
  budget and starved headroom both fail honestly), disk verdict
  without a published base.
- `run.log`: 10 passed. Full suite: **315 passed, 6 skipped**
  (305 + 10 new; same 5 S1 native-run + 1 pre-existing skips), zero
  regressions.

## Native 2k confirmation (cutover gate on)

`benchmarks/run_integrated_qualification.py --scale 2k
--lifecycle-full` with `TRACE_LITE_SERVED_PERSISTENT=1` inherited by
the server child — the same rung shape as R09 attempt 2, now served
through the persistent view. Console: `s6_2k_console.log`; artifacts
(`scale_ladder.json`, `unified_eval.json`,
`qualification_dossier.json`, `lifecycle_pass.log`) land in this
directory. Status: RUNNING at delivery write time; verdict appended
below on completion — this section stays open until the ladder file
lands.

## Gates at risk

None. No gate weakened: R00–R08/INT-1/2/S1–S5 suites run unmodified
and green; the gate defaults off so every qualification bar sees
identical behavior unless the envelope is explicitly enabled.
Follow-ups owned elsewhere: 10k/50k rungs only after the 2k curves
and host headroom admit them (steering line 89); native Windows run;
post-S4 commit/push then Praxist task-init (mandate
`PRAXIST_MANDATE_2026-09-12.md`).
Full suite independently re-verified for this delivery:
**315 passed, 6 skipped** (`s6_full_suite.log`, 101.9 s).

## Online-mutation operating point + admission verdict (S6 envelope agent)

Two operating points are distinct (steering line 89): bulk import vs
online per-note mutation. The app-path rungs above (`envelope.json`,
sibling driver) measure bulk import through the real served app with
the cutover gate on; this section measures online mutation
(`ingest_note` per doc: R01 publish + synchronous drain + generation
publish each) plus the serving curve on the same host. Raw record:
`s6_online_mutation_2k.json` (recovered verbatim from the shared
`envelope.json` before the sibling driver's rotation; the sibling
driver also preserved it as `envelope_prior_partial.json`).

### A. Online per-note mutation (separate run, `s6_online_mutation_2k.json`)

| Operating point | 200 docs | 2k docs |
|---|---|---|
| Online mutation ingest | 3.7 s (54/s) | 260.4 s (7.7/s) |
| Seed (background-equivalent) | 0.16 s | 0.51 s (1998 encodes) |
| Cold open / first query | 3.9 ms | 4.1 ms / 51.7 ms, route=vector, 0 open encodes |
| Warm query p50 / p95 | 0.5 ms | 3.2 ms / 11.8 ms |
| Freshness | — | 1.2 ms, pending 0, lag null |
| Footprint (index + DB + WAL) | 6.1 MB | 17.9 MB, headroom 53,783× |

### B. Bulk import through the served app (separate run, `envelope.json` rung 2000)

| Operating point | 2k bulk (app path) |
|---|---|
| Bulk import ingest | 1.9 s (1067/s) |
| Seed encodes | 1999 encodes |
| Warm query p50 / p95 | 8.1 / 37.3 ms |
| Drained | drained=True |
| Footprint | 14.0 MB |

### C. 10k

10k: never ran post-reboot on this candidate — REFUSED (see admission
math below). No 10k numbers are claimed anywhere in this file. (The
prior revision's `10k (sibling, bulk)` column held 2k app-path numbers
under a 10k header, including a computed-not-measured `9.6 s
(~1040/s, linear)` cell which is REMOVED here, not relabeled.
Corrected 2026-09-12 cycle 2 per reconcile row 8.)

Admission math (online mutation): 3.7 s at 200 → 260 s at 2k fits
total ≈ O(N^1.85) (per-doc 18.5 ms → 130 ms). Extrapolated: 10k ≈
85 min, 50k ≈ 28 h of pure ingest. Two points make a trend, not a
law — but under steering's stopping rule ("do not run another
expensive ladder merely to reconfirm already visible scaling
defects") the lifecycle rungs above 2k are REFUSED on the online
path until per-write cost is bounded. Suspects from code reading
(not profiled — profiling is the follow-up): per-ingest facet
reseed/centroid refresh over growing membership and coverage
maintenance scaling with corpus, not with the edit. Serving itself
scales: seed ≈ 3900 atoms/s (50k ≈ 13 s + IDF fit), cold open 4 ms,
footprint ≈ 9 KB/doc (50k ≈ 450 MB + WAL vs 942 GB free). The
blocker is write-path cost, not vector serving.

Instrument notes (honest, no quality claims here): the 2k
`planted_hit_rate` 0.3 vs the app-path 1.0 is an instrument
difference — the library-direct probe did not enable
`holon_guided` the way the served app path does; quality verdicts
belong to S5/BEIR, not this smoke. `edit_overlay_encodes: 0` in the
2k record is a probe bug (seed cache vs opener cache compared
across two `EmbeddingCache` objects); the 1-encode steady state is
covered by S4 unit tests and the S6 chained-convergence test.

## Native Windows run — operator procedure (NOT executed this session)

No native Windows run was executed or is claimed here: this host is
Linux/WSL (DBs on ext4 `/tmp`, 942 GB free; repo on 9p). `run_win.bat`
was not modified by this delivery. The controlling checklist is
`evidence/query-native/_integration/WIN/VALIDATION-CHECKLIST.md`.
Operator, from the repo root in Windows Terminal:

```cmd
run_win.bat gate      &  echo GATE_EXIT=%ERRORLEVEL%
run_win.bat smoke     &  echo SMOKE_EXIT=%ERRORLEVEL%
run_win.bat failproof &  echo FAILPROOF_EXIT=%ERRORLEVEL%
run_win.bat ladder    &  echo LADDER_EXIT=%ERRORLEVEL%
```

Expect: `GATE_EXIT=0` with `[GATE-PASS]` and the `[GATE] 2/3 disk:`
free-bytes line (20 GB minimum); `SMOKE_EXIT=0` with a new
timestamped `win_smoke_<TS>` dir; `FAILPROOF_EXIT=0` (meaning
induced failures fail nonzero with no advancement); `LADDER_EXIT=0`
only if all five rungs pass, reports under
`evidence\query-native\scale_ladder_<TS>\`. Report back the four
`*_EXIT` values, the disk line, and created dir names. With the S6
cutover under test on Windows, set `TRACE_LITE_SERVED_PERSISTENT=1`
in the operator shell before the ladder so the child server inherits
it (same as the 2k confirmation above); `TRACE_LITE_SERVED_INDEX_DIR`
must point at local fast storage (default is a `<db-stem>.served`
sibling of the DB, which already satisfies this when the DB root is
`%LOCALAPPDATA%`).

## Coordination note (two S6 drivers, one directory)

During this session a second S6 driver (`benchmarks/s6_envelope.py`,
app-path rungs) wrote the same `envelope.json`/`envelope_run.log`
as the online-mutation probe (`/tmp/s6envelope.py`). No data was
lost: rotations preserved priors (`envelope_prior_partial.json`),
and each driver's numbers are labeled above by operating point.
Action for the coordinator: the online-mutation 10k/50k background
job (session 1368, `/tmp/s6envelope.py`, scratch `/tmp/s6env-*`) is
believed STILL ALIVE — stop was attempted four times from inside
the sandbox (self-excluding `pkill`, three `bash_input` terminates)
and none took effect (PID-namespace isolation; terminate calls timed
out; the 10k scratch DB was still growing afterward). Please
terminate it runtime-side. If it completes its 10k rung it will
overwrite `envelope.json` once (identifiable: `gen-env`/`unq`
markers) and then start a refused 50k rung. Its 10k/50k online
numbers are not needed: the admission refusal above stands on the
measured 200→2k trend plus the sibling's linear bulk points.

## Lane C cycle 2 — concurrent-serve falsifier v3 smokes + v4 repairs (2026-09-12)

Harness: `benchmarks/s6_concurrent_serve.py` (Lane C owned).
`s6_envelope.py` and `s6_serve_under_drain.py` remain SUPERSEDED for
qualification (reconcile rows 4–5; kept for forensics, never run as
qualification). No `s6_envelope.py --rungs 2000,10000` was run this
cycle. No bounded 2k concurrent run was attempted: the v3 smokes
below exposed two verdict escapes that v4 (delivered here,
unexecuted live) repairs; the 2k waits for a v4 smoke re-run on a
quiesced tree.

### V3 smoke operating points (each run separately identified)

Corpus nouns are exact: documents / revisions_total / revisions_current
/ atoms from SQL counts; queue depth counts JOBS (pending+claimed),
never docs. All three runs: candidate HEAD `0df044b`, TestClient +
sqlite, `TRACE_LITE_WORKER` unset (app-owned `ServedRuntime` only).

| Run (`runs/`) | Corpus (base/backlog/planted) | Window | Queries ok/err | Query p50/p95/max | Mut ok/timeouts | Mut max | Overlap signals a/b/c | Predicates failed | v3 verdict |
|---|---|---|---|---|---|---|---|---|---|
| `s6c_20260912-185654` | 30/80/4, 3×120 threads | 40 s | 360/0 | 46.7/64.3/2464 ms | 8/0 | 7118 ms | 1 maint sample, 0 intersecting (v3.0 logic) | overlap_proven, retrieval_ok (anchors 0/4) | FALSE |
| `s6c_20260912-190049` | 30/80/4, 3×120 threads | 40 s | 360/0 | 37.7/55.9/182 ms | 8/0 | 5953 ms | 0/360/357 | none | TRUE |
| `s6c_20260912-190447` | 40/120/6, 4×200 threads | 45 s | 800/0 | 61.0/175.6/4365 ms | 9/1 | 10447 ms | 0/800/787 | none | TRUE |

Pre-timing backlog verified before each window (depth 80/80/120 jobs
≥ backlog docs; `documents==revisions==atoms`, jobs pending 80/80/120).
Backlog progress real in all runs (jobs.done +88/+88/+129; served
catch-up receipts show overlay `new=81/121, encoded=81/121` then
steady `new=1/encoded=1` per online mutation; depth_last 0 at smoke
scale — full drain is an outcome here, never forced). Served
generation stays frozen at the seed gen all window (no compaction:
`compacted:false`); reader gen advances 9/10 times via online-mutation
publications. Mut latency decays across the window (5953→167 ms,
10447→181 ms): the first online mutation synchronously drains the
whole backlog inside `POST /api/ingest` (reconcile row 1, live
demonstration — each mut's depth bracket is recorded in v4).

### Defects demonstrated (harness escapes, with evidence)

- F1 — mutation timeout not gated (FALSE GREEN): run `...-190447`
  mutation #0 took **10447 ms** (`timeout:true` in `events.jsonl`)
  against the frozen 10 s per-request bound, yet `errors_zero=True`
  and verdict TRUE — v3 counted only `ok:false` as mutation failure.
  v4 adds `timeout_ok` (zero query AND mutation timeouts).
- F2 — burst pacing serves a static snapshot: run `...-190049`
  issued all 360 queries in **5.5 s** of the 40 s window (75% by
  4.2 s), entirely inside the first mutation's sync drain and before
  any publication — all 360 responses pinned to ONE frozen
  generation (`distinct_gens=1`). Overlap signals b/c are genuine
  (real write-lock contention with the sync drain) but
  burst-confined; cross-generation serving was never exercised
  (run `...-190447` reached 2 gens only by accident of a slower
  10.4 s first mutation). v4 paces issue evenly (`--pace even`,
  `--pace burst` preserves v3 behavior, recorded in manifest) and
  adds `span_ok` (query span ≥80% of window) and `gens_observed`
  (≥2 distinct response generations) predicates.
- F3 — artifact identity: runs `...-185654`/`...-190049` manifests
  record harness shas `2afab874`/`08f93dbd` whose bytes are
  UNRECOVERABLE (the recorded worktree file was overwritten with v3,
  sha `2128be82`) and `app_repo:null` — both runs are FORENSICS
  ONLY, not citable. Run `...-190447`'s instrument bytes match the
  current file (`2128be82`) BUT the app tree has since drifted
  (main-tree `app.py`/`engine.py` edits landed after the run —
  coordinator correction: lanes A/B never touched those frozen
  files; Lane A owns holon.py/coverage_state.py, Lane B owns
  router/*; diff sha `5105cafc`→`45a40850`): it binds to a
  SUPERSEDED tree
  state — a valid v3-mechanics smoke and pre-repair baseline, not
  qualification of the current tree. v4 absolutizes all manifest
  paths and records harness version + CWD. The run-1→run-2 anchor
  flip (0/4→4/4, identical corpus args) is UNRESOLVED root cause
  (instrument bytes gone; harness sha differs between the runs, so
  a harness-side change is the lead hypothesis, not app
  nondeterminism — but unproven).
- F4 — measurement gaps (fixed in v4, no verdict impact claimed):
  no mutation latency summary (added `mut_p50/p95/max_ms`); no
  per-mutation depth brackets (added); visibility probe omitted
  route/vgen/gen/served_gen (added); stop rule double-appended
  errors (evaluator now pure `stop_fired`, callers append once);
  no served-vs-reader gen divergence count (added informational
  `vgen_differs_from_served`: run-2 0/360, run-3 283/794 —
  consistent with vgen labeling the reader gen while vectors come
  from served base+overlay; Lane B owns the semantics).

Under v4 predicates, v3 runs `...-190049` (span_ok, gens_observed)
and `...-190447` (timeout_ok, span_ok) would both read FALSE —
derived from their recorded series, not re-executed.

### V4 verification performed (no live runs this cycle)

- `python3 -m py_compile benchmarks/s6_concurrent_serve.py` → OK.
- `benchmarks/test_s6c_v4_helpers.py` (14 checks, stdlib only):
  replays the recorded v3 series through the v4 pure helpers
  (`mut_stats`, `query_coverage`, `stop_fired`,
  `pace_interval_s`) — all pass, including the two escape
  reproductions (run-3 mut timeout → timeout fails; run-2 burst →
  span/gens fail) and a synthetic paced good run (pass). With
  `S6C_V3_RUNS=<runs-dir>` it replays recorded runs; without, the
  synthetic subset runs. Coordinator rerun: `python3
  benchmarks/test_s6c_v4_helpers.py` from the repo root (0
  failures observed 2026-09-12).
- `--help` shows `--pace/--min-gens/--span-frac`; the smoke-bound
  refusal (`--baseline 2000`, exit 2) still fires unchanged.
- Deliberately NOT run live: main-tree `app.py`/`engine.py` edits
  landed mid-cycle (coordinator correction: drift source was main,
  not lane edits — lanes stayed in owned files), and
  DB-writing benchmarks must serialize — a v4 smoke now would
  measure a moving target under CPU contention. The paced-threading
  integration (only the pure helpers are offline-verified) and the
  full predicate stack therefore await a coordinator-run v4 smoke
  on a quiesced tree.

### Limitations (explicit, preserved)

- No concurrent-serve numbers above smoke scale (≤175 docs); the
  2k/10k ladder for this harness is unrun. R09 NEEDS_REPAIR and the
  10k-full-drain refusal stand unchanged.
- No native Windows run; sqlite-on-`/tmp`-style paths only via
  explicit run dirs (never repo, never bare `/tmp` globs).
- No HTTP retract endpoint exists (`remove_note` is library-only):
  retract visibility is untestable over the served path (Lane A
  interface request, unchanged).
- No per-query served-view-freshness field exists in `/api/query`
  responses (Lane B request: confirm vgen labels the reader gen;
  consider overlay-atom count at query time). `catchup_seq`
  monotonic counter still requested for sub-second catch-up
  attribution (1 Hz poller vs short catch-ups).
- Shared-TestClient-across-threads is validated by observation only
  (1160 queries + 25 mutations, 0 errors over 3 runs), not by
  framework contract.
- Budgets unchanged from frozen S6 values (lag 60 s, headroom 10×,
  freshness 1.0 s, shutdown join 30 s, per-request 10 s,
  latency p95 ≤ 1000 ms). No gate weakened.

### Repro commands (coordinator, main checkout, quiesced tree)

```sh
# 0. Apply: copy these 4 files into the main checkout
#      benchmarks/s6_concurrent_serve.py  (v4, replaces v3 same path)
#      benchmarks/test_s6c_v4_helpers.py  (new)
#      evidence/query-native/_integration/S6/delivery.md (this file)
#      evidence/query-native/_integration/S6/s6_concurrent_serve.spec.md (v2)
# 1. Offline self-test (no DB writes):
python3 benchmarks/test_s6c_v4_helpers.py
# 2. V4 smoke (bounded; DB writes serialized, lanes quiesced):
.venv/bin/python benchmarks/s6_concurrent_serve.py \
    --baseline 40 --backlog 120 --window-s 45 --queries-per-thread 200
# 3. Bounded 2k ONLY after a v4 smoke shows span_ok + gens_observed
#    + timeout_ok with overlap proven (coordinator approval required):
S6C_ALLOW_LARGE_SMOKE=1 .venv/bin/python benchmarks/s6_concurrent_serve.py \
    --baseline 200 --backlog 1800 --planted 10 --window-s 300 \
    --query-threads 4 --queries-per-thread 300 --mut-budget 20
```

### Explicit verdict (Lane C cycle 2)

Harness v4 is DELIVERED, offline-verified, and NOT YET live-qualified:
no release claim, no "2k qualified" claim, no calibration of the 2k
operating point. The next evidentiary step is exactly one coordinator
v4 smoke on a quiesced tree; the bounded 2k is gated on that smoke
showing paced span + cross-generation + overlap with zero timeouts.
Old probes stay superseded; R09 NEEDS_REPAIR preserved.

## Coordinator night-close (2026-09-12 ~21:00 UTC)

### Bounded 2k falsifier runs (separately identified)
| Run (`runs/`) | Harness | Corpus | Window | Queries ok/err | Mut ok/timeouts | Depth first→last | Overlap | Failed predicates | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| `s6c_20260912-201811` | v4 (`04d87c03…`) | base 400/backlog 1600 | 300 s | 800/0 | 15/0 (max 6608 ms) | 1942→997 | proven, 16 gens | visibility_ok | FALSE |
| `s6c_20260912-203355` | v4.1 (`1c2c2d2d…`) | base 400/backlog 1600 | 300 s | 800/0 | 15/0 (max 6544 ms) | 1942→997 | proven, 16 gens | none (13/13) | TRUE |

Falsification anatomy (v4 run): last-mutation probe found the unique-token
doc in top-5 but top-1 was another online doc (vector route, half-drained
backlog); v4 checked top-1 content, conflating recall with citation
integrity. DB forensics on the run artifact: 1 current revision, 1 atom
(bytes 0–77), atom-in-revision True — stored bytes clean. v4.1 checks the
matching item (rank recorded: rank=1, top1=`online-c-0005.md` in the 2k
pass; rank=0 in smoke `s6c_20260912-203227`). Original FALSE verdict stands
as-measured; v4.1 re-run qualifies the corrected predicate. Both runs
candidate-bound (HEAD `0df044b` + dirty diff manifest, no /tmp globs).

### Post-lane online-mutation profile (re-bound in main tree)
Lane A worktree: 400 notes 15.7 s (25.45/s, 33→53 ms flat). Coordinator
re-run on the integrated candidate
(`evidence/overnight/coordinator-verify/profile_baseline.json`): 400 notes
19.35 s (20.67/s, 44→57 ms flat across all 8 windows). Same flat shape;
delta is machine variance + main-side extras. The pre-lane 44→247 ms O(N)
blowup is gone in both trees. Table A 260.4 s cell is PRE-lane; post-lane
2k online rate is extrapolated ≈ 97 s (unmeasured — needs a dedicated run).

### Seed/cutover fix + B4/B5 (this session)
Pre-existing `test_cutover_serves_persistent_after_background_seed` (2≠3,
reproduced on pristine base): root cause was the test asserting one-shot
seed receipt == final corpus while seed-vs-third-ingest races. Fix: additive
`served_atom_count()` + `served_atoms`/`content_version` status keys
(B5), test waits converged coverage == 3 (stronger assertion, same gate).
B4: `vector_base_generation_id` threaded through QueryResult + both HTTP
response dicts (pin-vs-base skew now observable per response). Suite:
324 passed, 6 skipped, 0 failed (twice consecutive; was 323/1).

### Updated verdict
Bounded 2k concurrent-serve under ACTIVE backlog: QUALIFIED (v4.1 13/13,
run `s6c_20260912-203355`). This qualifies serving-while-draining at 2k
only — not online per-note ingest rate at 2k (profiled at 400, ≈21/s),
not 10k (still REFUSED), not Target A/B research objectives (still
experimental). R09 NEEDS_REPAIR stands (envelope ≠ R09 gate). No commit,
no push (awaiting independent authorization).

## Corrective pass (morning review response, 2026-09-13 ~03:00 UTC)

### Mutation latency leads (not trails)
2k v4.1 run: 800 queries p50/p95/p99/max 30/145/347/1301 ms — bounded
query serving under load: SUPPORTED. 15 mutations p50/p95/max
6116/6544/6544 ms with 0 timeouts — a responsive decoupled mutation path:
NOT established (and not claimed here). Direct profile on the 2k artifact
(`coordinator-verify/mutprobe_2k/direct_profile.py`, cProfile in-process):
per-note ≈250 ms at shallow backlog = 73% synchronous `drain_outbox`
(`project_revision` + grouping), 15% R01 publish txn, rest facets/graph/
publication. HTTP probes: ~10 ms drained vs ~6.4 s at 677-deep backlog.
Backlog-depth ladder, corpus fixed at 200
(`coordinator-verify/mutprobe_2k/depth_ladder.py`): depth 0 → 60 ms;
depth 100 → 3376 ms; depth 300 → 511 ms (single samples, non-monotonic —
rate NOT modeled). Proven: latency couples to backlog depth via
in-request sync drain (bounded ~300–380 jobs/request); per-job cost
scales with corpus (holon membership term). The "flat at 400" write-path
claim is a 400-note operating point, not a general law; the 97 s 2k
extrapolation is WITHDRAWN. Next bounded run (designed, unrun):
replicated depth ladder with warmup control to model per-job(corpus).

### Candidate identity, corrected
Pre-v4.2 run manifests (head + diff-stat hash + untracked NAMES) IDENTIFY
but do not bind executed source: marking all pre-v4.2 run identities
content-UNVERIFIED (evidence preserved as historical). v4.2 manifests hash
every loaded `trace_lite` module (42 modules, digest `95779ad5…` identical
across smoke + 2k runs) + dependency versions + config resolved from the
RUNNING app (`config_resolved`: cutover effective=1, enabled=true).
2k v4.2 re-qualification (`runs/s6c_20260913-025022`, ok TRUE 13/13):
same corpus/window as v4 runs; anchors 6/6 exact at rank 0; visibility
cite exact at rank 1; overlap split maint TRUE (3 via flag + 314 via
progress bins, 88 bins, 1 pass edge) / mut TRUE reported.
Post-run drift check (binding working as designed): current tree vs run
digest differs in EXACTLY one module — `router.hybrid` (B2 anomaly
fallback, added after the run; exception-path-only, suite re-verified
330 green including the new fallback test). All other 41 modules identical.

### Predicate audit outcomes
- generation_ok (pin==filing labels) kept; base coherence observed via
  `vgen_differs_from_served` (506/533 vector queries served under
  pin≠base skew — overlay-by-design) + per-response `base_generation_id`.
  The coherence GATE is exact-span citation, which catches stale/wrong
  bytes regardless of labels.
- Citation now exact byte-span on matching items (anchor + visibility),
  fail-closed with reasons; 14 offline negative controls green
  (`benchmarks/test_s6c_v42_helpers.py`): wrong-revision, shifted span,
  missing atom, retracted, stale, bad id, out-of-range, multibyte,
  overlap split (mut-only must not gate), identity determinism/sensitivity.
- Overlap gate = background-worker signals only; mutation-handler overlap
  reported separately (264–269 queries of sync-drain time in the 2k runs).

### Architectural verdicts (F1–F7, reclamation)
- Sync drains (ingest/remove/sync-batch/replay/startup paths): STILL
  SYNCHRONOUS — decoupling not achieved; cost reduced only. Owner: morning.
- Detachment policy: unchanged by design; vectors served throughout 2k
  maintenance observed (533 vector + 267 hybrid, 0 errors).
- Delta-warm O(N) texts: bounded through 2k (0.008 s); documented limit.
  B2 closed: anomaly fallback to exact full rebuild + regression test.
- F1 invalidation crash gap: CONFIRMED UNCOVERED (fault hooks cover
  pre-commit only). DEFERRED: pending-marker + startup-replay design, owner
  morning. Falsifier: kill -9 between publish commit and invalidate.
- F2/F3 (reseed-on-edit, replay w/o bump): DEFERRED, app.py-side, owner
  morning. F4 (bounded restart drain): ACCEPTED as designed. F5 (claim
  race): ACCEPTED benign/self-healing. F6 (retract staleness): ACCEPTED
  bounded (descriptors unread by served path; remedy exists). F7 (worker
  ownership): VERIFIED. Reclamation: DEFERRED (no live callers, no live
  hazard; grace rule + falsification test before any call site).
- Lane B R2 live-cache stays probe-only (fixture need documented); R1/R3
  ported to `tests/test_served_delta_regression.py` (6/6 green).

### Suite and skips
330 passed (324 + 6 regression), 6 skipped, 0 failed. Skips: S1 blackbox
(needs live native server URL) + learned retrieval (needs INT3_LIVE=1 +
cached bge snapshot) — both env-gated, reasons in situ. Stress flake:
thread-pool, zero-retry, zero-error assertion under load; failed 1/4
full-suite runs, passes in isolation; mechanism (transient under CPU
contention) unconfirmed to a named exception — OPEN, owner unassigned,
status retained honestly (not quarantined).
Seed change: corrected async test expectation (waits converged coverage;
bounded 30 s, fails if coverage never arrives) — production seed logic
untouched; seed receipt stays a truthful one-shot.
