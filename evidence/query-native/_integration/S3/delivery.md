# S3 independent maintenance — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + STEERING S3 row. Interpreter: `.venv/bin/python`.

## Problem

All projection work drained synchronously inside writer calls
(`ingest_note` / `sync_notes` / startup-resume). A job that failed
(lone-atom deferral), was crash-claimed, or was enqueued while the
server was down sat unfinished until the next user write triggered a
drain — there was no autonomous, supervised lifecycle, no measured
queue lag, and no backpressure signal.

## Design (routine details resolved autonomously)

- `src/trace_lite/filing/workers.py` (new, owned): `MaintenanceWorker`
  supervises the **existing** `source.project.drain_outbox` primitive
  (untouched — the same claim→project→complete/fail with the
  obsolete-revision skip that online `ingest_note` and bulk
  `sync_notes` use, so background maintenance cannot diverge from
  writer correctness). Per pass: bounded batch (`batch_limit`,
  default = shared `DRAIN_LIMIT`), pressure check, drain, then one
  `filing` generation publish **iff** real projections completed.
  `run_until_drained` = finite backfill (stops on empty queue, live
  foreign leases, 3 futile passes, or `max_batches` — never spins).
  `run_forever` / `start_background` = supervised loop with
  per-pass exception isolation, exponential-backoff sleeps on failed
  passes, poll-interval sleeps otherwise. Owned handles use
  `check_same_thread=False` (sole-user background handle); borrowed
  handles are never closed here.
- `store/outbox.py` (additive only): `queue_stats` (depth + oldest-job
  `queue_lag_s` + attempts + errors, portable `SUM(CASE...)`
  aggregates), `job_progress` (writer progress by state), pure
  `retry_delay_s` (exponential, overflow-safe cap test), and
  `pressure_status` (depth >= caller watermark). Backoff delay is
  in-memory by design: restart retries immediately, always safe
  (every stage idempotent). All R01 APIs byte-identical behavior.
- `store/generations.py` (additive only): `guarded_activate`
  (activate iff the caller-computed currency bit allows; stale output
  is skipped, never served; unknown id still `KeyError`).
- `api/app.py` (worker wiring only): `start/stop_maintenance_worker`,
  lifespan hook gated by `TRACE_LITE_WORKER=1` (default off so the
  S1/S2 bars run the exact pinned synchronous behavior; enable for
  autonomous backfill), `TRACE_LITE_WORKER_POLL_S` override,
  additive `/api/status` keys `worker` / `queue_stats` /
  `job_progress` (existing keys untouched).
- S4 tuning explicitly out of scope: batch/watermark/backoff
  constants reuse shared defaults, untuned.

## Before / after

| behavior | before | after |
|---|---|---|
| failed/crashed/offline jobs, no further writes | pending forever (next user write drains) | worker `run_until_drained` finishes in bounded passes; no `/sync`, `/ingest`, or replay call |
| crash-claimed job (live lease) | next drain steals only after expiry + a user write | restarted worker steals after expiry autonomously; live leases never stolen |
| permanent failure (lone atom) | retried on every future write's drain | `run_until_drained` reports `no forward progress` after 3 futile passes (job kept retryable); timed loop keeps retrying with backoff |
| superseded revision's job | skipped without projecting (drain rule) | same, now S3-tested + `guarded_activate(allow=False)` proven to leave the served pointer |
| queue lag / writer progress | unmeasured | `queue_stats` / `job_progress` per pass + on `/api/status` |
| deep queue | unbounded intake, no signal | `pressured` report + stats; batches stay bounded; lexical reads served mid-backlog |

## Verification

- `tests/test_s3_workers.py` (10): autonomous finish (jobs/rev
  == 1, publication emitted), lease live-block + expiry steal (+30 s
  floor preserved), retry backoff 1/2/4→cap then heal-by-arrival,
  futility no-spin bound, restart resume across DB reopen, stale
  skip (no v1 segments, `skipped_superseded` event, served v2 only,
  generation guard both ways + unknown-id `KeyError`), backpressure
  (bounded batch, pressure flag, reads served, full drain after),
  lag/progress lifecycle, live `TestClient` autonomy + restart with
  `TRACE_LITE_WORKER=1` (external `service.publish`, zero HTTP write
  calls), profiling split writer (`profile.json` / `profile.log`).
- Profiling (`profile.log`, this machine): 4 docs
  parse 0.000 s / publish-SQL 0.002 s / drain 0.019 s /
  checkpoint 0.0127 s / publication 0.0031 s; 16 docs
  0.000 / 0.007 / 0.061 / 0.0088 / 0.0031; 48 docs
  0.000 / 0.045 / 0.311 / 0.0117 / 0.0030. Rows touched:
  2.0 spans/doc, 1.0 jobs/revision, 0.5 memberships/span,
  ~58 bytes/doc, churn 1.0; every size drains in 1 batch.
  Drain bundles SQL lookup + grouping + per-job commit (shared
  drain; not separately timed). No encoder runs in lexical
  maintenance (S4 scope); checkpoint cost is flat.
- Full suite: **281 passed, 6 skipped** (271 + 10 new; same
  5 S1 native-run + 1 pre-existing skips), zero regressions.

## Owned files touched

`filing/workers.py` (new), `filing/__init__.py` (exports),
`store/outbox.py` (+measurements/backoff), `store/generations.py`
(+`guarded_activate`), `api/app.py` (worker wiring + additive status
keys only), `tests/test_s3_workers.py` (new),
`evidence/…/S3/**`. `source/project.py`, `store/schema.sql`,
`store/migrate.py`, and all S0/S1/S2/R09 evidence untouched.

## Gates at risk

None. No gate weakened: S1/S2 suites run unmodified and green; the
background thread is env-gated (default off) so qualification bars
see identical behavior. Follow-ups owned elsewhere: producer-side
throttling + constant tuning (S4), large-corpus drain curves (S6).
