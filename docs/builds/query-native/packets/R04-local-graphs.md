# R04 — Evidence-linked source-local graphs

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Extract intra-source relational structures (assertions, qualifications, conditions, mechanisms) as local graphs (stigmergy) linked to exact source evidence spans, rather than treating documents as flat bags of words.

## Inputs and dependencies

- Required prior packets: R02 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-02; `ARCHITECTURE.md` Section 5; `EVALUATION.md` Section 3.
- Existing baseline: `src/trace_lite/schema.sql` (requires explicit `assertions` and `relations` tables).
- Missing facts and readiness checks: Validate local relation extraction schema and ensure zero orphan edge references.

## Scope and interfaces

- Owned scope: `src/trace_lite/graph/` local extraction modules, assertion schemas, graph fixtures.
- Shared surfaces: Graph schema shared with R05 (cross-source reconciliation).
- Non-goals: Do not attempt global cross-document deduplication or ontology building (handled in R05).

## Suggested approach

1. Implement typed assertion frames: `subject`, `predicate`, `object`, `polarity`, `condition`, and `epistemic_status` (`source_asserted`, `validated`, `hypothesized`).
2. Anchor every relation to source span IDs with byte-range provenance.
3. Build a deterministic structural relation extractor (detecting causal keywords: "improves", "degrades under", "restores", "requires", "conflicts with").
4. Preserve qualification edges explicitly (e.g. `Method A ──improves──▶ Throughput` qualified by `High contention`).

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C4-1 Relational fidelity | Extracted relations preserve direction, polarity, and conditions without loss | Mechanism fixture suite | `evidence/query-native/R04/relation_fidelity.json` | 100% role accuracy |
| C4-2 Provenance anchoring | 100% of extracted relations resolve to valid source span IDs | Graph integrity check | `evidence/query-native/R04/provenance_check.log` | Zero unanchored edges |
| C4-3 Condition retention | "Batching restores benefit under contention" retains conditional qualifier | Queue example probe | `evidence/query-native/R04/condition_test.json` | Qualifier edge exists |
| C4-4 Extraction fallback | Malformed text or parser issues yields partial graph without crashing | Fuzz testing & error recovery | `evidence/query-native/R04/fallback_test.log` | Zero unhandled exceptions |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_local_graph.py -v`
  - `python -m pytest tests/test_assertion_extractor.py -v`
- Evidence Directory: `evidence/query-native/R04/`
- Delivery Record: `evidence/query-native/R04/delivery.md`
- Independent Review: `evidence/query-native/R04/review.md`

## Recovery and escalation

- Do not attempt unconstrained open-domain fact extraction that causes memory blowup.
- If a relation is ambiguous, store as `status='unresolved'` rather than forcing an inaccurate relation.

