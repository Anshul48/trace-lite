# R00 — Baseline and frozen evaluation contract

Contract: TL-QN-2026-09-12.1
Status: READY
Dependencies: None; design READY
Owned scope: benchmarks/; evidence/query-native/R00/; EVALUATION.md target register

## Outcome

Pin the real TL candidate, runtime and existing behavior. Establish a reproducible comparison before architectural changes.

## Implementation work

1. Record HEAD/diff, Python/SQLite builds, FTS capabilities, hardware/filesystem, source artifact checksums and exact retrieval profiles.
2. Reproduce bounded existing quality/latency behavior in new output paths. Inspect the historical benchmark path versus API/CLI ingestion.
3. Create fixture families for conditions, negation, entity ambiguity, source edits, unfiled evidence, hubs and natural-language abstention.
4. Freeze corpus splits, metric definitions, acceptable regression margins and resource envelopes. Estimate the richer 1M-source workload from measured spans and relations.

## Acceptance and verification

- Saved score reproduction either agrees within explained deterministic/numerical differences or records a discrepancy; no stale score is treated as a fresh pass.
- Profiling shows actual commit/warm/centroid work in the public ingestion path.
- workload.json and gates.json specify candidate/configuration and separate quality/performance claims.
- Test-set judgments remain outside training/tuning; a small baseline report and executable commands are available.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R00/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not change product behavior or historical evidence. If datasets/runtime are unavailable, preserve the manifest and mark affected comparisons INCONCLUSIVE. Numerical gates are not invented from old document-count claims.

