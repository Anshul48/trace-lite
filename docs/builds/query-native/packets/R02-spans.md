# R02 — Source spans and segmentation views

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R01 VERIFIED
Owned scope: new source parsing/segmentation modules; src/trace_lite/obsidian/parser.py adapter; source service; span tests

## Outcome

Provide multiple source-faithful evidence units and local context envelopes, while allowing boundary experiments without destructive identity changes.

## Implementation work

1. Preserve raw source payload and expose coordinate-system-aware evidence resolution.
2. Add structural parsing for supported text formats with mapping for normalization/frontmatter. Keep code/table units and antecedent context.
3. Produce stable occurrence spans and versioned segmentation/grouping views; handle repeated identical passages distinctly.
4. Route API/CLI adapters through the same source service. Add structural discourse holons with type-specific constraints.
5. Expose a boundary-signal interface for R08; do not report structural parsing as prediction-error inference.

## Acceptance and verification

- Unicode/CRLF/frontmatter and repeated-text fixtures round-trip every claimed raw-byte excerpt exactly.
- A1–B–A2 has independently retrievable spans plus recoverable original context.
- Long/no-paragraph sources do not become unbounded evidence blobs or split code/table semantics without explicit context.
- Re-segmentation preserves old provenance or explicit supersession mappings; it does not mutate prior evidence references.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R02/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Legacy bodies lacking original bytes retain legacy coordinate labels. Parser failure produces retained evidence plus partial parsing status, not fabricated exact source positions.

