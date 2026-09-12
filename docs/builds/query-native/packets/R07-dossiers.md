# R07 — Evidence dossiers and Cordis integration

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R06 VERIFIED
Owned scope: src/trace_lite/cordis/compiler.py and models/adapter; src/trace_lite/api/; dossier tests

## Outcome

Compile query-generated evidence documents that preserve required context, disagreement, provenance and budget accounting.

## Implementation work

1. Accept objective text independently from goal ID and include query/scope/snapshot in compilation.
2. Union retrieval routes, expand necessary conditions/antecedents/counterevidence, rerank and deduplicate.
3. Emit structured selections/relations/omissions and named support states; synthesis is a separate derived operation.
4. Count tokens with the consumer tokenizer; surface mandatory overflow and incomplete evidence groups.
5. Adapt current API/Cordis contracts. Verify the actual external harness when available; label simulated/schema-only checks.

## Acceptance and verification

- Opaque goal IDs do not affect relevance when objective text is unchanged.
- A cross-source dossier cites exact source versions and preserves incompatible conditions.
- Oversized useful evidence uses anchored excerpts or explicit insufficiency; no silent prefix-only truncation.
- Mandatory constraints are all included or compilation explicitly fails with required budget.
- No corpus-wide term hit or lexical-only relevance automatically declares answer support.
- Real runtime loading/consumption is evidenced separately from Pydantic compatibility.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R07/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not convert lexical action screening into a claimed NLI/authorization guarantee. A retained LLM synthesis stays derived; retention alone does not validate it.

