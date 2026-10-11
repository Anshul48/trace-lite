# R09 contract amendments (attempt 2)

Contract: TL-QN-2026-09-12.1. This file amends interpretation ONLY.
EVALUATION.md, gates.json, and packet files are NOT edited (steering rule).

## Verdict levels (steering §5+§8 overlay: four levels)

- PASS: criterion met within budget with bounded evidence.
- CONDITIONAL: criterion met with a recorded limitation, or met on a
  bounded sample with a measured projection (applies to scale rungs where
  full in-session measurement is infeasible; projection method recorded).
- INCONCLUSIVE: evidence missing or methodologically blocked; no claim made.
- FAIL: criterion not met; blocks qualification at and above that rung.

Halting rule: the ladder halts at the first failing EXACT gate
(no_oom, integrity_ok, counts_stable_post_crash, replay_ok,
parity_agree, queue_healthy). Budget verdicts (quality floors, latency
targets, publication lag) NEVER halt the ladder; they are recorded per
rung as four-level verdicts. Rationale: EVALUATION §3 states floors are
"historical references" and R00 margins are "proposed ... engineering
decisions rather than user mandates"; §5 refuses 1M only on
cost/space projections, unstable queue, or correctness failure.

## §Q — queue-gate correction (attempt-1 harness bug)

Attempt 1 failed rung 2k on `queue_drained` (pending_jobs==0) and
`counts_stable_post_crash`. Both were harness bugs, not system failures:

1. `queue_drained`: the served write path drains at most DRAIN_LIMIT=64
   projection jobs per call BY ARCHITECTURE
   (src/trace_lite/source/project.py:71; sync_notes shares ONE bounded
   drain per batch). Any bulk ingest >~64 sources therefore leaves a
   pending queue; semantic_status "pending" with generation-pinned reads
   is the honest ARCH §3 steady state. Attempt 1 measured the queue once
   and failed the rung without ever driving the served drain.
   Correction: the harness now drives a bounded served-drain sample via
   replay ingests only (no new writes; replay asserted every iteration —
   a new revision aborts with `mutated_corpus`), and gates on
   `queue_healthy` = mutation-free monotonic progress at a measured rate.
2. `counts_stable_post_crash`: the comparison included `oldest_pending_s`
   = MAX(now - created_at), a wall-clock field that differs between ANY
   two reads, failing every restart by construction. Correction: compare
   content counts only; both clock values still recorded for age analysis.

## §D — measured drain calibration (bounded numbers)

- Served projection throughput, 2k-rung copy, isolated server, one timed
  replay (64 jobs in 65.5s): ~1.0 job/s all-in (HTTP + replay + drain).
- No persistent failures observed: remaining pending jobs sit at
  attempt=0 with NULL error (fresh work, not poison).
- Full-drain projections at measured rate: 2k ~35min; 10k ~2.8h;
  100k ~28h; 250k ~70h; 1M ~11d. Full in-session convergence is
  therefore infeasible at every rung; per-rung verdicts use the sampled
  rate + projection (CONDITIONAL at best for publication lag).

## §B — budgets (from bounded numbers; rationale inline)

- Ingest (served /api/sync, 2000-file chunks): 218 docs/s (chunk 1) ->
  39 docs/s (chunk 5) at the 10k rung. Per-chunk decay shows the write
  path slowing as the DB grows; 100k ingest extrapolates to 1-2h.
  Budget: report per-chunk docs/s; no single-number claim.
- Served projection: ~1.0-2.3 jobs/s (2k copy: 64 jobs/65.5s = 1.0/s;
  10k live sample: 256 jobs/110s = 2.3/s). Full-drain projections:
  2k ~35min; 10k ~1.2-2.8h; 100k ~12-28h; 1M ~5-11d.
- Read path (DIAGNOSED CEILING): every /api/query pays freshness() ->
  projection.pending_coverage(conn), a per-uncovered-revision checklist.
  Measured e2e-vs-server gap: ~0.33s at 1936 pending (2k) vs ~15s at
  9424 pending (10k) — linear in backlog (~1.6ms/uncovered revision)
  while retrieval compute itself stays ~10ms. Consequence: after any
  bulk backfill, served query latency is dominated by backlog
  accounting, not retrieval. Budgets must therefore be stated CONDITIONAL
  on backlog size; steady-state (drained) latency is NOT measured at
  scale in-session (full drain infeasible — see §D).
- Latency/quality numbers per rung: see scale_ladder.json + dossier.
  Server-side p50/p95 and client e2e p50/p95/p99 are recorded SEPARATELY
  (contract requirement); the gap is the freshness finding above.

## §N — non-inferiority policy

- Retrieval quality is reported as nDCG@10/Recall@10 with paired
  bootstrap 95% CIs on sized BEIR subsets, against R00 repro refs
  (scifact 0.6589, nfcorpus 0.3188). A rung is non-inferior when the
  rung CI covers (ref - 0.01). The operating point uses the R08-parked
  fallback hashbow encoder, so parity with learned-embedding refs is NOT
  expected; shortfalls are recorded as budget verdicts with this cause.
- Lifecycle/parity/crash gates admit NO margin: exact agreement required.

## §L — limitations (final)

- Quality measured under pending-queue (backfill) condition at both
  rungs; steady-state (fully drained) quality at scale is unknown.
- e2e latency ~= server compute + ~1.4-1.6ms x pending revisions
  (freshness backlog accounting per /api/query). Reported separately;
  the e2e column is NOT retrieval latency.
- c4 concurrency degrades e2e p95 (2k: 5.6s; 10k: 30s) while server p95
  stays <1s: queueing + per-request freshness under contention.
- Ingest per-chunk decay (218->39 docs/s) and drain rate variance
  (1.0-5.3 jobs/s) are disclosed ranges, not point budgets.
- 100k/250k/1M: INCONCLUSIVE (not attempted). --check-admit admits on
  RAM/disk even at 1M (exit 0); refusal rests on the measured
  time-envelope scaling recorded in the dossier. This is a halt with
  quantitative grounds, per EVALUATION §5.
- Attempt-1 evidence preserved as *.attempt1.* in this directory.
- Operator-error hardening (attempt 2, incident in delivery.md): parity
  `agree` requires a non-empty sample; `db_counts`/`served_stack` raise
  FileNotFoundError on a missing DB. Rationale: sqlite silently creates an
  empty DB at a deleted path, and newer SQLite accepts `IN ()`, so every
  downstream gate passed vacuously on zero rows. Corroborating (not
  canonical) 10k evidence is listed in delivery.md, not in the dossier's
  rung table.
