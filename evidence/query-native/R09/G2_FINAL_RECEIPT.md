# G2 FINAL Receipt — FAIL (2026-09-13)

Contract: TL-QN-2026-09-13.3 / PACKET-G2. Candidate: HEAD `0df044b` + working tree.
Verdict: **G2 FAIL (final)**. Two full attempts, two repair cycles (one complete,
one incomplete). No third repair cycle exists. G3/G4 are not unlocked.

## Assessment summary

| Attempt | Result | Decisive evidence |
|---|---|---|
| Attempt-1 `G2-10000-20260913-074531` | FAIL | RSS 448.9MB halt; drain 11.07/s; c4 P95 8878ms, 20/40 timeouts |
| Repair cycle 1 | COMPLETE, probes partial | R1 drain 43.9/s; R3 storm fixed, mem 114–247MB; under-drain P95 still over |
| Attempt-2 `G2-10000-20260913-100349` | FAIL | Drain PASS 529.9s; memory REGRESSED 561.8MB; 87 unhandled lock-500s; WAL 3.95GB |
| Repair cycle 2 | INCOMPLETE (unreported) | Active past final 11:58 tripwire; stopped by coordinator; partial tree edits preserved, unverified |

Never executed in any attempt: SciFact nDCG@10 (quality UNKNOWN), 20/20
capability cases, crash recovery, replay idempotency, parity. Both runs halted
at the memory line before these stages.

## (a) Files (cumulative G2 work)

- `benchmarks/run_integrated_qualification.py` (untracked; builder + cycle-1 exact workload)
- `tests/test_qualification_gates.py` (untracked; 13 -> 16 tests)
- `evidence/query-native/R09/capability_cases.json` (FROZEN 20/20, neg=10, multi=20)
- `evidence/query-native/R09/repair_cycle1.json` (cycle-1 record)
- `evidence/query-native/R09/{scale_ladder,qualification_dossier,unified_eval,lifecycle_pass}[.attemptN].*` (both runs)
- EXPANSION cycle-1: `src/trace_lite/source/project.py` (R1 batch grouping),
  `src/trace_lite/router/hybrid.py` + `src/trace_lite/api/app.py` (R3 shared warmed view)
- EXPANSION cycle-2 (PARTIAL, UNVERIFIED): edits in flight in `project.py`,
  `app.py`, `hybrid.py`, `src/trace_lite/filing/workers.py` (txn bounding, retry,
  checkpoints, seed retry) — no verdict, probes unknown
- Untouched as required: `cascade.py` (permitted), `engine.py`, `vector_store.py`,
  `cordis/*`, `obsidian/*`, `store/*`, `docs/*`

## (b) Rationale

Attempt-1 failed on three independent defects: per-thread GIL-bound full
re-encodes serializing into an ~11s warm storm (9s queries, 20/40 timeouts,
449MB via zombie threads); O(N^2) holon folds (~80ms/job at 8k members);
per-chunk re-seeds plus duplicate caches (310MB setup HWM). Cycle-1 R1/R3
addressed drain and the storm with probe-verified wins and identical quality
A/B. Attempt-2 confirmed drain (529.9s converged) but R1's batched passes held
SQLite's single write lock for seconds: 87 unhandled `database is locked`
HTTP 500s on `POST /api/ingest` (`ingest_note` -> `service.publish`), WAL
3.95GB checkpoint starvation, RSS 561.8MB (regression; attribution unproven,
contention-plausible). Latency improved ~6x (P95 1579ms) with residual S4
catch-up GIL (~750ms/pass) scoped out of cycle 2. Cycle 2 targeted contention
discipline (short txns, busy-retry/503 with accounting, checkpoints, seed
retry) under tightened integrated acceptance (ingestion + drain + latency +
memory + checkpoint together; 503-conversion alone not green) but delivered no
verdict by the final tripwire and was stopped; its tree edits are preserved
but unverified. Per the agreed stop rules (two-cycle budget exhausted;
locking HALT fired), G2 closes as FAIL. The concrete blocker for any
successor effort: writer/transaction contention discipline with proven memory
attribution, plus first-ever quality/capability measurements.

## (c) Diff identity (final tree state)

- HEAD: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8`, dirty, 19 tracked files
  modified, 5759 insertions / 331 deletions (`git diff` sha256:
  `040fbefeb912c4493167a855f74adf3c2dc51d47282743f6a7e1c3d2f4ead92a`)
- `app.py` diff sha256: `6aa0e570cd0e69f10ab395e5bbe98ef72e169ba634afa145268457e4a473aa53`
- `hybrid.py` diff sha256: `58c7b23cbbb6f2c31a2a946694beb65a1887c7988d9dd15bd75e65120a32a1aa`
- Untracked file hashes: harness `268a12ef...4404a730`, gate tests
  `ff0ae58b...8857ca125`, `project.py` `44c8a37e...27d7a6d`, `workers.py`
  `eedd8862...5000dbb`, frozen cases `24a13aab...5cbcb942e3` (unchanged)
- Full diff reproducible via `git diff`; untracked files by hash above.
  Tree mixes pre-existing baselines with G2 work plus UNVERIFIED cycle-2
  partial edits (cycle-1 end: app.py 2085 / hybrid 258 changed lines; final:
  app.py 2349 / hybrid 317).

## (d) Commands, statuses, metrics, evidence

| # | Command | Exit | Result |
|---|---|---|---|
| G2-0 | `pytest tests/test_qualification_gates.py` (builder) | 0 | 13 passed |
| G2-1 | `run_integrated_qualification.py --scale 10k --lifecycle-full` (attempt-1) | 1 | FAIL: RSS 448.9 halt; 11.07/s; c4 P95 8878; 5 checks unexecuted |
| G2-2 | profiler attempt-2 | 0 (report) | NEEDS-EXPANSION: storm + O(N^2) fold + re-seed HWM |
| G2-3 | cycle-1 probes | 0 | drain 43.88/s; mem 114-247MB; under-drain P95 over; suites 58+37 green; nDCG A/B 0.7380 identical |
| G2-4 | `pytest tests/test_qualification_gates.py` (re-verify) | 0 | 16 passed |
| G2-5 | `--scale 10k --lifecycle-full` (attempt-2) | 1 | FAIL: drain PASS 529.9s; RSS 561.8 REGRESSED; 87 lock-500s; WAL 3.95GB; 5 checks unexecuted |
| G2-6 | cycle-2 probes | N/A | NO VERDICT (stopped at final tripwire; partial work preserved) |
| G2-7 | final 10k | N/A | NOT RUN (probes never justified it) |

Evidence: `evidence/query-native/R09/` — `scale_ladder[.attempt1].json`,
`qualification_dossier.json` (`g2_runs[0,1]` = both FAILs, history preserved),
`unified_eval[.attempt1].json`, `lifecycle_pass[.attempt1,.attempt2].log`,
`capability_cases.json` (FROZEN), `repair_cycle1.json`, this receipt.
Per-query rankings were never produced (quality stage never reached).
Frozen cases file hash verified unchanged across all repairs.

## Handoff for successor work (not authorized here)

If re-attempted: (1) finish contention discipline with the tightened
integrated probes as gate; (2) settle memory attribution (reader-hold vs
writer-hold checkpoint starvation); (3) incremental S4 catch-up (the 45x
latency residual); (4) first quality/capability measurement as the top
information priority. A bounded PRAXIST experiment (batching vs serialized
scheduling vs snapshot lifetimes on one frozen reproducer) remains a
separately-authorized option per supervisor note; nothing was installed.
