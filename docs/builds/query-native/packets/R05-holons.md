# R05 — Cross-source reconciliation and subgraph holons

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Persist overlapping cross-source organization without merging away evidence identity or flattening incompatible conditions, replacing the legacy intra-document-only holon constraint.

## Inputs and dependencies

- Required prior packets: R03 and R04 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-03; `ARCHITECTURE.md` Section 6; `EVALUATION.md` Section 3.
- Existing baseline: `src/trace_lite/filing/holon.py` (lines ~59–61 enforce `len(doc_ids) == 1`).
- Missing facts and readiness checks: Validate DAG cycle detection and typed holon member schemas.

## Scope and interfaces

- Owned scope: `src/trace_lite/filing/` holon store, reconciliation engine, schema migrations for `holons` and `holon_members`.
- Shared surfaces: Holon graph accessed by R06 (maintenance) and R07 (compiler).
- Non-goals: Do not physically fuse atom text across sources; holons maintain memberships, not merged strings.

## Suggested approach

1. Refactor `holon.py`: replace rigid `len(doc_ids) == 1` check with kind-aware validation:
   - `discourse_span`: contiguous, single-source episode.
   - `thematic_cluster`: multi-source, overlapping membership.
   - `claim_cluster`: cross-source evidence with explicit roles (`premise`, `claim`, `qualification`, `counterevidence`).
   - `query_synthesis`: query-assembled virtual holon.
2. Implement reconciliation: distinguish linking (graph edge), grouping (holon membership), and canonicalizing (entity resolution).
3. Enforce strict cycle detection on hierarchical containment DAGs.
4. Calculate holon centroid sufficient statistics incrementally `(vector_sum, member_count)`.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C5-1 Cross-source grouping | Atoms from distinct sources successfully join a shared `thematic_cluster` holon | Multi-doc holon test | `evidence/query-native/R05/cross_doc_holon.json` | Text remains separate |
| C5-2 Conflict preservation | Contradictory claims coexist under a topic with explicit disagreement roles | Contradiction fixture probe | `evidence/query-native/R05/conflict_preservation.log` | Zero false consensus |
| C5-3 Discourse protection | Single-source constraint enforced strictly on `discourse_span` kinds | Type constraint validation | `evidence/query-native/R05/discourse_guard.log` | Rejects multi-source discourse |
| C5-4 Cycle prevention | Attempt to form circular containment in holon hierarchy raises validation error | Containment DAG stress test | `evidence/query-native/R05/cycle_detection.log` | Zero cycle leakage |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_holon_reconciliation.py -v`
  - `python -m pytest tests/test_cross_source_grouping.py -v`
- Evidence Directory: `evidence/query-native/R05/`
- Delivery Record: `evidence/query-native/R05/delivery.md`
- Independent Review: `evidence/query-native/R05/review.md`

## Recovery and escalation

- If automatic cross-source clustering creates low-quality links, retain independent global vector/lexical retrieval.
- Preserve legacy holon IDs and migrations; do not drop existing tables without schema backup.

