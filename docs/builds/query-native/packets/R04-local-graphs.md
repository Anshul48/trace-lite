# R04 — Evidence-linked source-local graphs

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R02 VERIFIED; independent of R03 after schema freeze
Owned scope: new extraction/graph modules; schema migrations via designated schema owner; local-graph fixtures

## Outcome

Extract relational structure with source-linked assertion frames instead of only topic similarity.

## Implementation work

1. Implement typed assertions/arguments/qualifications and evidence anchors; preserve source-asserted versus validated relation status.
2. Parse explicit structural relations deterministically; add a real optional model extractor behind a schema/evidence validator.
3. Represent unresolved references and alternative entity bindings without forced canonicalization.
4. Bound long-source work using local windows and candidate links. Preserve residual source for missed relations.
5. Record model/prompt/schema identity and extraction error/partial status.

## Acceptance and verification

- Mechanism fixtures retain actor direction, polarity, conditions, quantities and version/time scope.
- Every extracted relation resolves evidence and reports its epistemic status.
- The queue example preserves “batching restores benefit” without inventing a cache-line mechanism.
- Missing/ambiguous evidence remains unresolved; invalid model output cannot become trusted relations.
- Measured extraction precision/recall and cost are reported separately from schema validation.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R04/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not expand into exhaustive ontology building or autonomous causal discovery. A model outage retains searchable passages and marks the local graph incomplete.

