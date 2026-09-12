# R02 — Source spans and segmentation views

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Provide source-faithful evidence units (spans) and local context envelopes, eliminating monolithic 1-note = 1-atom storage while enabling boundary experiments without destructive identity changes.

## Inputs and dependencies

- Required prior packets: R01 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-01; `ARCHITECTURE.md` Section 3; `EVALUATION.md` Section 4.
- Existing baseline: `src/trace_lite/obsidian/parser.py`, `src/trace_lite/filing/holon.py`.
- Missing facts and readiness checks: Verify byte offset accounting across Unicode, CRLF line endings, and frontmatter.

## Scope and interfaces

- Owned scope: `src/trace_lite/source/` segmentation modules, `src/trace_lite/obsidian/parser.py` adapter, span extraction tests.
- Shared surfaces: Span data structures consumed by R03 (embeddings) and R04 (local graphs).
- Non-goals: Do not run complex ML perplexity models for boundary detection (structural baseline first; prediction evaluated in R08).

## Suggested approach

1. Store canonical immutable source payloads; implement byte-level slicing with coordinate-aware offsets (`start_byte`, `end_byte`).
2. Implement structural paragraph and bullet list splitting with length caps (≤1,200 chars), preserving parent context.
3. Keep atomic code fences and markdown tables intact; resolve antecedent pronouns/definitions to parent envelopes.
4. Expose versioned segmentation views so re-segmentation creates new views without breaking prior evidence references.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C2-1 Exact byte round-trip | Every claimed span matches raw source bytes verbatim across Unicode/CRLF | Offset slice test suite | `evidence/query-native/R02/byte_fidelity.log` | 100% byte fidelity |
| C2-2 Sub-document addressability | A1-B-A2 sequence retrieves A1 and A2 independently while retaining sequence order | Sub-document retrieval test | `evidence/query-native/R02/subdoc_test.json` | Spans ≤ 1,200 chars |
| C2-3 Structural integrity | Code blocks and tables remain undivided; no orphaned syntax fragments | Syntax parser stress test | `evidence/query-native/R02/syntax_blocks.log` | Zero syntax breaks |
| C2-4 Discourse envelope | Antecedent definitions and headers accessible from child spans | Context expansion test | `evidence/query-native/R02/discourse_context.json` | 1-hop parent envelope |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_source_spans.py -v`
  - `python -m pytest tests/test_parser_fidelity.py -v`
- Evidence Directory: `evidence/query-native/R02/`
- Delivery Record: `evidence/query-native/R02/delivery.md`
- Independent Review: `evidence/query-native/R02/review.md`

## Recovery and escalation

- Parser failure produces retained source evidence with `status='partial'`, never corrupted or fabricated offsets.
- Route implementation bugs to B4 repair; parser edge cases must not alter database schema.

