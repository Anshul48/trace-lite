# R03 — Learned representations and global indexes

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R02 VERIFIED
Owned scope: src/trace_lite/router/; new encoder/index adapters; projection jobs; retrieval tests and benchmark variants

## Outcome

Provide full-corpus candidate eligibility using lexical and measured learned representations, with an ANN option and a recoverable publication protocol.

## Implementation work

1. Pin a small learned encoder and a stronger relevant comparison, with artifacts/tokenizer/pooling/normalization/runtime identified.
2. Compare BM25 and learned hybrid ranking against exact vector search before ANN approximation.
3. Implement index adapter add/search/delete/save/load plus immutable manifest publication and version filters.
4. Test the chosen binding's filtering behavior, delta visibility, tombstones and reload. Record unsupported capabilities.
5. Union global candidates with route metadata. Remove corpus-wide lexical support as an answer-sufficiency claim; retain relevance status.

## Acceptance and verification

- A real encoder invocation produces measured telemetry; no silent hash fallback.
- Evidence late in row-ID order and outside facets is eligible; sampled latency behavior is explicitly separate.
- ANN recall against exact search and task quality pass R00's frozen envelope at measured resources.
- Delete/update/restart cannot expose obsolete evidence as current; missing index manifests are detected.
- Long inputs cannot silently truncate away relevant qualifications; encoder generations never mix.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R03/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

If ANN fails its envelope, retain exact search only at its measured supported scale or lexical baseline with explicit degraded semantics. Model/library choice is not itself qualification.

