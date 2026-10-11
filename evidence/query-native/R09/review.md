# R09 independent verification review (R09 B3 QUALIFICATION VERIFIER)

Contract TL-QN-2026-09-12.1. Verifier ran `.venv/bin/python`; all exit codes recorded.
Builder dossier is treated as claim, not evidence. No file besides this review was modified.

## Verdicts

| Rung | Workload executed | Correctness passed | Capacity characterized | Qualification achieved |
|---|---|---|---|---|
| 2k | YES | YES (6/6 exact gates) | YES | NO (quality/publication budgets FAIL) |
| 10k clean rerun | YES | YES (6/6 exact gates, log-verified) | YES | NO (same budget failures) |
| 100k / 250k / 1M | NO (not attempted) | — | NO | NO |

Highest defensible claim: **2k and 10k are capacity-characterized diagnostics
(CONDITIONAL); no rung is qualified.** "2k CONDITIONAL stands" only as a
diagnostic label, not as qualification. The dossier's `refused_rungs.10000`
"INCONCLUSIVE (invalidated)" text is STALE — the clean rerun supersedes it and
promotes 10k to characterized/CONDITIONAL-diagnostic. `highest_qualified_rung`
should read "highest characterized rung" to avoid implying qualification.

**Packet verdict: FAIL** (qualification not achieved; C9-3/C9-4 unmet, C9-5
receipt predates the merged ladder). The 2k/10k diagnostic evidence itself is
accepted: workloads executed, exact gates green, ceilings honestly recorded.
Per steering, documentation does not override this FAIL into any PASS.

## Verification executed (exit codes)

1. `pytest tests/test_qualification_gates.py -q` → 5 passed, exit 0.
2. Full suite `pytest tests/ -q` → **261 passed, 1 skipped**, exit 0 (matches claim).
3. `--check-admit 100k / 1m --output evidence/query-native/R09` → exit 0,
   `admit:true` both (RAM/disk only). Read-only path; nothing written.
4. 2k entry in `scale_ladder.json` is byte-identical (sha622908cf…) to
   `scale_ladder.pre10k.json`; merged 10k entry is byte-identical to the
   sandboxed `out/scale_ladder.json` (which holds ONLY the 10000 key — the
   rerun never touched 2k). Bench root intact (`trace.db` 79.6MB, vault,
   rung_10000.json port 8139). `rerun_10k.log` monotonic 13:11:12→13:51:44,
   no errors/restarts/port conflicts; the 13:15→13:48 gap is the measured
   1706.7s seq-latency wall, not a relaunch. Ports currently free, no residue.
   The `/bin/sh`-has-no-`PIPESTATUS` wrapper note is corroborated (observed
   here too); harness exit 0 follows from the `return 0` path after the logged
   PASS verdict. Single attempt: CONFIRMED.
5. Quality independently re-scored from a scratch copy of the preserved 10k DB
   (evidence untouched; tree code identical to rerun — no src/benchmarks/tests
   mtime newer than 13:51): replication EXACT (scifact 0.6277 CI
   [0.4875,0.7619]; nfcorpus 0.1789 CI [0.1023,0.2591]).

## Quality adjudication (central)

Raw per-query rankings were NOT preserved (see F3), so the verifier regenerated
ranked lists deterministically from the preserved DB + frozen BEIR files.

- Subset construction (`load_beir_subset` first-N `corpus.jsonl` docs; covered
  queries in qrels order; first 40 scored) plus full-corpus ideal/recall
  denominators is a **harness artifact** that structurally depresses scores:
  perfect-ranker ceilings under the harness's own denominators are 0.938
  (scifact) / 0.835 (nfcorpus), and unindexed relevant docs are counted as
  misses. Retrieval pool caps are NOT a cause (quality profile: dense full
  scan, sparse_pool=100 — verified in `cascade.py`).
- **Corrected subset-scoped measurement** (ideal DCG + recall denominators
  restricted to indexed docs; the exact interim corrected read):
  scifact nDCG **0.6603** (recall_vs_indexed 0.725); nfcorpus nDCG **0.1973**
  (recall_vs_indexed 0.1253).
- scifact `non_inferior_vs_r00=false` is a **rule artifact**: |0.6603−0.6589| =
  0.0014 ≤ frozen ±0.01 → non-inferior TRUE; the flag is false only because the
  code uses absolute point distance on confounded numbers (F2). No meaningful
  scifact regression (all-70-covered subset-nDCG 0.5938 — first-40 flatters by
  ~+0.07, still same ballpark; neither is paired with R00's 300-query number).
- nfcorpus `false` is **honest signal of integrated-pipeline shortfall**:
  retrieval finds only ~1/8 of INDEXED relevant docs (0.1253), far below any
  denominator effect (correction moves 0.1789→0.1973 only); no first-40
  selection bias (all-107-covered subset-nDCG 0.2052 ≈ 0.1973). The gap
  magnitude vs R00 remains confounded by 4:1 synth-distractor dilution,
  backfill-condition IDF instability (steering-flagged), broad-query top-10
  crowding (70–109 relevants/query), and unpaired query sets. **Chunked-span
  causation is NOT established** (span→doc dedup in place, 6/6 probes pass) —
  do not cite it as the cause; it is one unisolated confounder among the rest.

## Amendments audit (finding 7 of STEERING_ASSESSMENT: STILL OPEN)

- Margins prespecified: YES — R00 `gates.json` froze ±0.01 + floors before R09.
- Paired data: NO — subset-harness vs full-corpus R00 on different queries.
- Code (`score_beir`, line 649) still uses absolute point-estimate distance.
- Prose §N ("CI covers ref−0.01") is one-sided coverage, not S6's lower-bound
  of paired differences against the margin. Code and prose disagree with each
  other and both disagree with S6.
- §Q queue-gate correction is accepted as measurement repair (old
  `pending==0` gate was unsatisfiable by bounded-drain architecture), and
  publication stays FAIL — documentation does not override failure here.
  But per S3 a sampled rate alone never constitutes a completion claim;
  `queue_healthy` remains diagnostic-grade. Halting rule (budgets never halt)
  is tolerable only because rung FAILs are retained — they are.

## Refusal validity: JUSTIFIED, not premature

Recomputed from measured curves: ingest chunk fit t(k)=6.5+10.6k s/2000 docs
→ 100k ≈ 3.7h (dossier's "1–2h" is optimistic; refusal stronger than stated),
250k ≈ 23h; latency 1.44ms/pending → 100k 120-query phase ≈ 4.5h (matches
dossier), 1M ≈ 45h; drain @2.238/s → 100k ≈ 12h, 1M ≈ 124h. A bounded 100k
diagnostic (~4.5–5h unattended, ingest + spots, no 120q phase) was launchable
in principle but correctly skipped: 10k already characterizes all three
ceilings across 4 agreeing runs, and steering bars expensive ladders that only
reconfirm visible defects.

## Findings (scope: R09 files clean — harness + gate tests + R09 evidence only;
`run_trace_lite_benchmark.py` RSS edits are coordinator repairs, not litigated)

- F1 (method): no immutable candidate manifest — 10k ran against a shared
  dirty tree (12 src files + others uncommitted); dossier pins no commit/diff (S0).
- F2 (method): `non_inferior` prose/code/S6 triple mismatch (see above).
- F3 (contract): per-query ranked outputs not retained, violating EVALUATION
  §7 receipt requirements; forced regeneration to adjudicate.
- F4 (minor): `--check-admit` traceback-exits 0 when the output dir lacks
  `scale_ladder.json` (admit never printed); builder's exit-0 claims are valid
  (run against evidence dir) but the CLI is fragile.
- F5 (evidence): dossier stale post-rerun — `refused_rungs.10000` still says
  invalidated; receipt binds the pre-merge dossier/ladder only.
- F6 (minor): `server.log` empty (uvicorn output lost on SIGKILL crash test);
  lifespan proven by other artifacts.

## Next action

Packet FAIL stands; do not re-litigate 100k+. Required follow-up (harness
owner, not verifier): (1) fix `score_beir` denominators (subset-scoped) +
  preserve per-query lists; (2) implement S6 paired-difference non-inferiority
  and align §N prose with code; (3) update dossier (10k rung entry with
  0.6603/0.1973 corrected read, refresh `refused_rungs.10000`, rename highest
  rung, re-issue receipt); (4) record candidate commit+dirty in dossier (S0);
  (5) nfcorpus shortfall → paired same-corpus A/B diagnostic (flat vs chunked
  spans, no synth distractors, drained steady-state) before any quality claim.
