# R00 — Independent Review (B3 verifier)

Record type: independent review
Author role and date: B3 independent verifier / 2026-09-12
Packet and contract revision: R00-baseline.md / TL-QN-2026-09-12.1
Candidate identity claimed: git HEAD 0df044b51fb3b40ca3360ea7abd78ac36924b9f8 plus dirty prompt-text files only
Builder conclusions treated as claims; nothing below trusts builder output.

## Delivery section

N/A (verifier role; no behavior/artifact changed by this review except this file).

## Candidate identity check

- `git rev-parse HEAD` → `0df044b51fb3b40ca3360ea7abd78ac36924b9f8`. Matches claim.
- `git status --short -- src tests benchmarks` → empty; `git diff --stat -- src tests benchmarks` → empty. `src/`, `tests/`, `benchmarks/` are unmodified vs HEAD. Confirmed.
- Full-tree `git status --short` shows one item beyond the claimed dirty set: `M docs/builds/query-native/STATE.md` (in addition to the claimed `M COORDINATOR_LAUNCH_PROMPT.md`, `M docs/builds/query-native/COORDINATOR.md`, plus untracked `evidence/query-native/` which is the expected new R00 evidence). Recorded as finding F1; product code is unaffected.

## Scope of independent execution

- Interpreter: `.venv/bin/python` from repo root for all reruns, as instructed.
- Reran quality harness: `.venv/bin/python benchmarks/run_quality_eval.py --tag b3-verify --out /tmp/b3_quality.json` → exit 0.
- Reran BEIR scifact: `.venv/bin/python benchmarks/run_beir_unified.py --dataset scifact --out-dir /tmp/b3_beir/` → exit 0.
- Did NOT rerun nfcorpus/fiqa (per instruction, time); instead byte-compared R00 BEIR files to saved `evidence/beir/` artifacts and re-hashed all 9 corpus/query/qrel files against `workload.json`.
- Inspected `workload.json`, `gates.json` against EVALUATION §1; inspected `profile.txt`, `fixtures.json`; grepped `benchmarks/` and `tests/` for `qrels` usage and threshold-tuning patterns.
- Modified no file other than this `review.md`. Modified nothing under `src/`, `tests/`, `benchmarks/`, or any other R00 evidence file.

## Evidence (observed results, C0-1..C0-4)

| Criterion | Independent check | Observed result | Verdict |
| --- | --- | --- | --- |
| C0-1 quality reproduction | Reran quality harness to `/tmp/b3_quality.json` (exit 0); compared to `evidence/query-native/R00/quality_repro.json` | Rerun: nDCG@10 1.0, mrr 0.5, abstention_recall 1.0, selective_error 0.0, pass=true — field-identical to `quality_repro.json`. Builder-flagged discrepancy CONFIRMED: rerun value (1.0/0.5) differs from historical `evidence/quality/quality-2k.json` (0.911/0.9167, tiers "2" vs rerun tiers "3", coverage 1.0 vs 0.5). Pass=true + discrepancy documented, which is the contract's quality leg | PASS (with documented discrepancy) |
| C0-1 BEIR scifact | Reran to `/tmp/b3_beir/` (exit 0); compared to R00 `beir/beir-scifact.json` AND saved `evidence/beir/beir-scifact.json` | Rerun: nDCG@10 0.6589, R@10 0.7765, CI [0.6136,0.7029], checksums all true, clears_floor=true — exact match to both files (Δ=0.0, within ±0.01) | PASS |
| C0-1 BEIR nfcorpus/fiqa | Byte-compared R00 files to saved artifacts on ndcg/recall/CI/checksums/docs/queries/abstentions/clears_floor; re-hashed all corpus files | All 8 fields MATCH on both datasets (nfcorpus 0.3188/0.1446, fiqa 0.2495/0.3085). All 9 sha256 + byte counts in `workload.json` corpora entries re-verified True (ALL_OK) | PASS (by artifact equivalence + hash re-verification; harness not re-executed) |
| C0-2 profiling | Inspected `profile.txt` | Real cProfile output: 20017 calls in 0.110s, 18-row cumulative table with ncalls/tottime/cumtime; ingest 20 docs 0.110s (5.51ms/doc), query 19.7ms, db 112KB. Plausible. Note: TestClient/httpx + thread-lock acquire dominate the table; product-code frames are not isolated — profile measures host+transport overhead as much as ingestion | PASS (with profiling-resolution note F4) |
| C0-3 workload/gates freeze | Inspected `workload.json`, `gates.json` against EVALUATION §1 element list | Candidate, runtime (py 3.11.15, sqlite 3.53.1, FTS5, 7 pinned deps), host (cpu/ram/repo v9fs + bench ext4 with free space), durability (WAL/NORMAL, R09-FULL comparison noted), ranking (top_k/RRF/seed-42 CI/dense-pool/tokenizer/dedup rule), corpus checksums, qrel FINAL-split labels, isolation policy, span math (1M×8=8M), ladder, regression tolerance ±0.01 + floors, exact §4 correctness gates, resource/admission rules, comparison matrix B0–B6, promotion rule — all present and labeled engineering decisions. Gaps listed as findings F2 (none blocking: measured span/edge distributions are explicitly deferred to R02) | PASS (with completeness notes F2) |
| C0-3 fixtures | Inspected `fixtures.json` | 5 families present: conditions, negation, entity-ambiguity, unfiled, off-topic-abstention — each with query, sources, expected outcome; labeled DEV-only, non-qualification | PASS |
| C0-4 test-set isolation | Grepped `benchmarks/` + `tests/` for `qrels`; grepped harnesses for threshold/tuning patterns | Only hits are `benchmarks/run_beir_unified.py` `load_qrels` + scoring lines (load → per-query nDCG/recall computation); zero hits in `tests/`; zero threshold/argmax/grid-search hits. qrels are read for scoring only — legitimate. No tuning-against-test evidence found | PASS |

## Independent findings

| ID | Severity | Finding |
| --- | --- | --- |
| F1 | Low (docs accuracy) | Tree has one dirty file the R00 records do not mention: `M docs/builds/query-native/STATE.md`. `workload.json`'s `dirty` list also omits it (and carries a stray leading space in one entry). `src/`/`tests/`/`benchmarks/` confirmed clean, so baseline behavior is unaffected; the candidate-identity record is slightly stale. |
| F2 | Low (EVALUATION §1 completeness notes, none verdict-blocking) | Against the §1 element list: (a) no GPU field — host report implies CPU-only but never states it; (b) no parser/encoder/extractor artifact versions or hashes beyond the tokenizer prose ("128-d hash-bag dense, legacy"); (c) no measured spans/source distribution, assertion/relation/membership totals, graph degree tails, index size, or update mix — `span_projection` carries only the 1M×8 planning assumption, with measurement explicitly deferred to R02; (d) no explicit BEIR provenance/licensing statement; (e) OS recorded only as "WSL2", no kernel/Windows build pin. Freeze is usable as committed; R02/R09 should close (b) and (c) before qualification claims. |
| F3 | Info | nfcorpus `clears_floor=false` (0.3188 vs 0.32 floor) and fiqa below its 0.38 floor are faithfully carried from the saved artifacts into R00 (identical values) — pre-existing floor misses, correctly documented, not regressions. |
| F4 | Info | `profile.txt`'s top rows are TestClient/httpx/threading overhead (`{method 'acquire' of '_thread.lock' objects}` 0.085s of 0.110s); it evidences real execution but does not isolate product-code bottlenecks (ingest-centroid claims rest on this coarse profile). Adequate for a frozen baseline; R02+ should profile service internals. |
| F5 | Info | Quality-harness drift (tiers "2"→"3", coverage 1.0→0.5, nDCG 0.911→1.0 at same 2400 docs) reproduced deterministically by this rerun, supporting the builder's product-code-fix hypothesis over flakiness — but the specific responsible commit was not bisected in this review. |

## Verdict

**Verdict: PASS** (R00 baseline reproduced and frozen; usable as the comparison anchor for R01+).

Criteria mapping: C0-1 PASS (BEIR exact within ±0.01 incl. independent scifact rerun; quality pass=true with confirmed documented discrepancy per the contract's quality leg) · C0-2 PASS (real cProfile rows; F4 limits resolution, not validity) · C0-3 PASS (workload+gates frozen with splits, margins labeled engineering decisions, 1M-span projection rule; F2 gaps deferred, not missing-freeze) · C0-4 PASS (qrels scoring-only, no tuning evidence). No FAIL; no INCONCLUSIVE (no environment failure occurred; all instructed reruns exited 0).

## Next action

Accept R00 as the frozen baseline; unblock dependent packets. Suggested follow-ups (non-blocking): amend the dirty-file record for F1; close F2(b–c) in R02 before any qualification claim; bisect the quality-harness drift commit (F5) when convenient.
