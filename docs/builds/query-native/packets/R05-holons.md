# R05 — Cross-source reconciliation and subgraph holons

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R03 and R04 VERIFIED
Owned scope: src/trace_lite/filing/; reconciliation modules; holon/dependency migrations; graph tests

## Outcome

Persist overlapping cross-source organization without merging away evidence identity or incompatible conditions.

## Implementation work

1. Introduce typed discourse, thematic, argument/claim and derived holons with explicit member versions and relation references.
2. Retrieve candidate mappings, then distinguish link/group/entity resolution/claim equivalence/derivation.
3. Keep compatible aliases reversible; require scope/role/polarity checks before claim equivalence.
4. Add representative passages, centroid sufficient statistics and bounded containment DAG validation.
5. Track duplicates without counting them as independent corroboration; retain disagreements within a topic.

## Acceptance and verification

- A1 and A2 group across source boundaries while original evidence/context remains available.
- Similar contradictory claims are related but not collapsed into one fact.
- Discourse constraints still hold; cross-source types accept valid membership.
- Cyclic containment fails; wider relation cycles cannot cause unbounded query traversal.
- Reversing a mistaken mapping restores separate mentions and invalidates dependent structure.
- All members/relations have real referential integrity and no orphan IDs.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R05/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not simply remove the old intra-document assertion. Migrate by kind and retain old IDs. If grouping quality is inconclusive, keep the graph and global retrieval while holding automatic promotion.

