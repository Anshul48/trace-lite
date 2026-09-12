# R09 — Integrated qualification and migration readiness

Status: DRAFT
Kind: integration
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Qualify the complete integrated query-native memory architecture across retrieval quality, concurrency, update consistency, crash recovery, and realistic scale (2k -> 10k -> 100k -> 250k -> 1M spans), producing a definitive qualification dossier.

## Inputs and dependencies

- Required prior packets: R07 VERIFIED; R08 dispositioned (VERIFIED or explicitly PARKED).
- Relevant contract sections: `PROJECT.md` QN-10; `ARCHITECTURE.md` Section 10; `EVALUATION.md` All Sections.
- Existing baseline: R00 baseline report and qualification gates.
- Missing facts and readiness checks: Verify persistent ext4 disk storage and available RAM before 1M qualification run.

## Scope and interfaces

- Owned scope: End-to-end qualification harnesses in `benchmarks/`, integration test suite, `evidence/query-native/R09/`.
- Shared surfaces: Entire Trace-Lite public surface (API, CLI, store, router, compiler).
- Non-goals: Do not substitute latency profiles or sample masks for full-corpus vector evaluation during quality qualification.

## Suggested approach

1. Re-run all exact lifecycle correctness gates (crash recovery, idempotency, ghost facet clearance, budget guard).
2. Execute the staged capacity ladder: 2k sanity probe -> 10k -> 100k -> 250k -> 1M spans on persistent ext4 storage.
3. Test under live concurrent load: simultaneous ingestion, graph invalidation, and API query serving.
4. Generate machine-readable qualification dossier (`evidence/query-native/R09/qualification_dossier.json`) and SHA-256 verification receipt.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C9-1 Unified configuration | Both retrieval quality (nDCG@10) and latency measured on identical configuration | Multi-metric evaluation run | `evidence/query-native/R09/unified_eval.json` | No profile swapping |
| C9-2 Lifecycle integrity | All 10 exact lifecycle correctness gates pass without exception | Lifecycle gate test suite | `evidence/query-native/R09/lifecycle_pass.log` | 10/10 gates green |
| C9-3 Staged scale ladder | System qualifies at 250k spans with P95 <= 35ms and RSS <= 450MB | Scale ladder benchmark | `evidence/query-native/R09/scale_ladder.json` | P95 <= 35ms, RSS <= 450MB |
| C9-4 1M span qualification | System ingests 1M spans without OOM or WAL corruption; vector search operational | 1M qualification run | `evidence/query-native/R09/1m_qualification.json` | Persistent ext4 |
| C9-5 Qualification receipt | Machine-readable dossier and cryptographic verification receipt generated | Dossier builder script | `evidence/query-native/R09/receipt.json` | Valid cryptographic hash |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python benchmarks/run_integrated_qualification.py --scale 250k --output evidence/query-native/R09/`
  - `python benchmarks/run_integrated_qualification.py --scale 1m --output evidence/query-native/R09/`
- Evidence Directory: `evidence/query-native/R09/`
- Delivery Record: `evidence/query-native/R09/delivery.md`
- Independent Review: `evidence/query-native/R09/review.md`

## Recovery and escalation

- If 1M scale encounters WSL memory limits, capture the diagnostic at 250k and record exact ceiling in dossier.
- Never write large-scale benchmark databases to `/tmp` (use persistent ext4 path).

