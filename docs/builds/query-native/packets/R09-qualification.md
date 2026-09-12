# R09 — Integrated qualification and migration readiness

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R07 VERIFIED; R08 VERIFIED or explicit experimental/PARKED disposition
Owned scope: benchmarks/ and tests integration; evidence/query-native/R09/; release/migration readiness record

## Outcome

Qualify the integrated configuration across quality, update consistency, recovery and realistic capacity, with exact scope.

## Implementation work

1. Pin the integrated candidate, artifacts and configuration; rerun all exact lifecycle gates and frozen retrieval evaluations.
2. Run 2k → 10k → 100k → 250k → 1M-source stages only while prior gates/resource projections pass.
3. Exercise real ingestion plus concurrent queries/edits/deletions and model outages; capture end-to-end timings.
4. Verify projection rebuild parity, FTS postings, namespace/version filtering and migration rollback.
5. Produce a machine-readable dossier and independent review naming supported scale, quality, freshness and durability.

## Acceptance and verification

- Same configuration has both quality and latency/resource evidence; no profile substitution.
- Every required manifest field and stage result is present, including failures and omitted capabilities.
- All exact provenance/update/recovery gates pass; capacity claims stop at the largest completed passing workload.
- A 1M-source result includes span/assertion/edge counts, build cost and memory/storage peaks.
- Migration shadow checks and rollback succeed before any switch is proposed.
- Independent reviewer reproduces decisive gates on the named integrated candidate.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R09/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Preserve failures and artifacts. Do not launch a costly rung with unknown resource needs. Qualification does not itself authorize publishing, deleting old data, or modifying TRACE.

