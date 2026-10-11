# S6 concurrent-serve falsifier — design spec (lane C, v2)

Harness: `benchmarks/s6_concurrent_serve.py` v4
(`HARNESS_VERSION="4"`; existing probes' logic untouched —
supersession is declared here and in the harness docstring, not by
editing them). v1 spec described v3; this v2 adds the cycle-2
findings (two verdict escapes + artifact gaps, all evidenced from
the v3 smoke runs) and the v4 repairs. Frozen S6 budgets unchanged.

## Why the old probes cannot qualify

* `s6_envelope.py` (row 4): bulk `svc.publish` loop + `run_until_drained`
  with a manual `MaintenanceWorker`, app booted only after the drain.
  No backlog under serving, no concurrency, no HTTP mutation path.
* `s6_serve_under_drain.py` (row 5): sequential `run_once` + queries on
  one thread (no overlap timestamps), second writer beside the app
  `ServedRuntime`, `n_sources = status["atoms"]` mislabel, ok = end
  servability + disk + nonempty samples.

## Phases (separated, separately timed)

0. Setup (<=300 s): explicit run dir `runs/s6c_<stamp>/` + manifest
   first; baseline corpus via library-direct `svc.publish` (no drain).
1. Cold start (<=600 s): boot served app (`TRACE_LITE_SERVED_PERSISTENT=1`,
   `TRACE_LITE_WORKER` unset so the ONLY maintenance is the app-owned
   `ServedRuntime`); lifespan startup drain is app-owned and allowed;
   wait seed done + servable; record time-to-servable + seed encodes.
2. Backlog (no drain): publish K docs library-direct on a separate DB
   handle WHILE the app runs; assert `queue_stats.depth >= K` (jobs,
   not docs) before timing; record documents / revisions_total /
   revisions_current / atoms / jobs by state / coverage / lag.
3. Steady state (fixed `--window-s`, never extended): N query threads
   sharing one TestClient (per-request timeout 10 s) + 1 mutation
   thread (`POST /api/ingest`, which triggers the real
   `served_after_write -> request_maintenance` path) + 1 Hz status
   poller recording `maintenance_running`, seed/catchup states +
   receipts (encode counters), served/reader generations, depth, lag,
   job_progress. Rolling error rate > 10% / 20 stops clients early.
4. Shutdown: close client (bounded 30 s join = frozen budget), then
   final counts + revision bytes read directly.

## ok predicate (all must hold; each reported individually)

overlap_proven (any of three timestamp signals: query ∩
maintenance-running sample ±0.5 s; query ∩ mutation span; query ∩
worker-progress bin) ·
backlog_progress (`jobs.done` increased OR depth decreased — progress,
never full drain) · latency_ok (p95 <= 1000 ms frozen freshness) ·
retrieval_ok (every planted anchor in top-5 for its token query AND
top-1 content is a substring of the immutable `source_revision` text) ·
generation_ok (no vector-route response with `vector_generation_id !=
generation_id`) · visibility_ok (last online mutation found + cited;
v4 also records route/vgen/gen/served_gen at probe time) ·
servable_at_end · disk_ok (headroom 10x) · shutdown_ok (<= 30 s) ·
errors_zero (no query/mutation errors, no hung threads) ·
timeout_ok — v4 (zero query AND mutation per-request timeouts; v3
green-lit run `s6c_20260912-190447` despite a 10447 ms mutation) ·
span_ok — v4 (ok-query issue span ≥ `--span-frac`, default 0.8, of
the window; v3 run `s6c_20260912-190049` burst 360 queries into
5.5 s of 40 s) ·
gens_observed — v4 (≥ `--min-gens`, default 2, distinct response
generations; v3 run `...-190049` served all queries at one frozen
gen, never testing cross-generation serving).

Query issue is paced evenly across the window by default (`--pace
even`: per-thread interval = window/queries_per_thread, staggered
starts); `--pace burst` preserves v3 closed-loop issue and is
recorded in the manifest. Pure helpers (`mut_stats`,
`query_coverage`, `stop_fired`, `pace_interval_s`) are covered by
`benchmarks/test_s6c_v4_helpers.py` (14 checks, stdlib only,
replays recorded v3 series + synthetic cases).

## Supersession + delivery.md fix (for coordinator)

* Both old probes are SUPERSEDED for qualification (kept for forensics).
  One-line comment patches for the coordinator to apply in the main
  checkout (this worktree cannot touch those untracked files):
  * `benchmarks/s6_envelope.py` docstring +=
    `SUPERSEDED for qualification by s6_concurrent_serve.py (row 4: drains
    before serving; bulk != HTTP mutation).`
  * `benchmarks/s6_serve_under_drain.py` docstring +=
    `SUPERSEDED for qualification by s6_concurrent_serve.py (row 5:
    sequential; second writer; weak ok). Do not run as qualification.`
* `delivery.md` mixed-column fix: APPLIED in the delivered
  `delivery.md` (cycle 2) — the "Online-mutation operating point"
  table is split into table A (online mutation 200/2k), table B (2k
  bulk import app path, `envelope.json` rung 2000), and an explicit
  10k NEVER-RAN/REFUSED statement; the computed-not-measured `9.6 s`
  cell is removed, not relabeled. R09 NEEDS_REPAIR + 10k-drain
  refusal text preserved. Coordinator: apply by replacing the file.

## Interface requests (lanes A/B, via coordinator — NOT assumed)

1. Cycle-2 evidence: `maintenance_running=true` was sampled only
   1–2× per 40–45 s window (catch-ups are quick overlays) and zero
   burst queries intersected those samples — signal (a) is
   structurally weak at smoke scale even with pacing. Request stands:
   lane A, add a monotonically increasing `catchup_seq` counter to
   the catch-up receipt for sub-second pass attribution. (Receipt
   shapes verified in `app.py:_run_catchup`: empty / seed-escalated /
   overlay `{new,dead,encoded,delta_size,compacted}` — no sequence
   number today.)
2. No HTTP retract endpoint exists (`remove_note` is library-only), so
   retract visibility is untestable over the served path — lane A:
   confirm out of scope or add `POST /api/retract`.
3. Observed: response `generation_id`/`vector_generation_id` label the
   thread-local READER view gen (`get_reader` + `ensure_served_persistent`
   re-pin), while served base gen stays frozen (no compaction) and
   fresh atoms arrive via the in-memory overlay delta. Lane B: confirm
   this labeling is intended, and consider a per-query
   served-view-freshness field (e.g. overlay atom count at query time)
   so the harness can gate freshness instead of reporting the
   informational `vgen_differs_from_served` count.
4. Bounded 2k run (`--baseline 200 --backlog 1800 --planted 10
   --window-s 300 --query-threads 4 --queries-per-thread 300
   --mut-budget 20`, `S6C_ALLOW_LARGE_SMOKE=1`) waits for coordinator
   approval AFTER one v4 smoke on a quiesced tree shows span_ok +
   gens_observed + timeout_ok with overlap proven. v4 live smoke
   itself is coordinator-run (cycle 2 deliberately ran zero live
   benchmarks: lanes A/B editing the tree concurrently).
