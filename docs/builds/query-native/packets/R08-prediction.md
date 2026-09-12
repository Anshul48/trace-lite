# R08 — Predictive segmentation and selective repair experiments

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R07 VERIFIED
Owned scope: experimental segmentation/repair adapters; evaluation variants; evidence/query-native/R08/; no silent default changes

## Outcome

Determine whether predictive boundaries and learned repair add value over the qualified structural/graph baseline.

## Implementation work

1. Run the EVALUATION comparison matrix for structural, uncertainty-based and contextualized variants.
2. Distinguish token surprise, embedding change and relational mismatch. Preserve conditions even when boundary scores suggest a cut.
3. Collect deterministic repair traces; compare learned scope prediction with full and rule-based rebuilding.
4. Freeze training/evaluation splits by source family and edit pattern. Measure real model work/cost.
5. Write a promotion decision for each mechanism independently, including a negative or inconclusive outcome.

## Acceptance and verification

- Predictive claims name an actually executed method/model and its per-source costs.
- Held-out negation, role reversal, quantity and temporal edits remain detectable.
- Quality/cost gains satisfy the frozen criterion with uncertainty analysis; accumulated errors are measured.
- Low residual alone cannot bypass source/condition checks.
- If evidence is inconclusive, structural segmentation/deterministic repair remain active and predictive capability is labeled experimental.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R08/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

R09 can proceed after explicit PARKED/inconclusive disposition, qualifying only the deterministic baseline. Do not require deployment of an inferior predictor to complete the program.

