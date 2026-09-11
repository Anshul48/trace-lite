# P03 — Hearst Multi-Parent Faceted Classification Engine

Status: READY
Kind: implementation
Contract revision: 2026-09-11.2
Owner/session: Builder Session 3

## Outcome
Implement Hearst multi-parent faceted classification for Trace-Lite. Replaces rigid single-hierarchy folder structures with a forest of independent taxonomic dimensions (Topics, Entities, Types, Projects, Sources). Documents and atoms can belong to multiple facets simultaneously without forcing an artificial single-parent hierarchy. Implements document-local holons (A-B-A grouping).

Concrete Example: A research note on "FlashAttention Triton Kernels" is assigned to `Topics:GPU/Attention`, `Entities:Triton`, `Types:Paper`, and `Projects:Trace`. Searching for any of these facets immediately retrieves the document.

Failure Case: Circular taxonomic parentage must be rejected with `CircularFacetError`. Facet paths must be deterministically resolved.

## Inputs and dependencies
- Required prior packets: P02.
- Relevant contract sections: `ARCHITECTURE.md` §2.2.
- Target directory: `src/trace_lite/filing/`.

## Scope and interfaces
- Owned files:
  - `src/trace_lite/filing/__init__.py`
  - `src/trace_lite/filing/engine.py`
  - `src/trace_lite/filing/taxonomy.py`
  - `src/trace_lite/filing/holon.py`
  - `tests/test_filing.py`
- Methods:
  - `create_facet(dimension: str, name: str, parent_id: str | None = None) -> str`
  - `assign_facets(atom_id: int, facet_ids: list[str], confidence: float = 1.0) -> None`
  - `query_facets(facets: list[str], match_all: bool = True) -> list[int]`
  - `create_holon(atom_ids: list[int], tier: str = "nav") -> str`

## Suggested approach
1. Define default dimensions: `Topics`, `Entities`, `Types`, `Projects`, `Sources`.
2. Implement facet tree DAG validation in `taxonomy.py`.
3. Implement multi-membership indexing and query filtering in `engine.py`.
4. Implement document-local chunk grouping (A-B-A holon pattern) in `holon.py`.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Multi-membership indexing | Document assigned to 3 orthogonal facets; appears in queries for all 3 | Facet test pass | No single-parent constraint |
| C02 | Facet tree traversal | Query child facet; returns atoms in subtrees | Hierarchy test pass | Subtree resolution < 5ms |
| C03 | A-B-A Holon grouping | Contiguous paragraphs grouped into semantic holon spans | Holon test pass | Intra-doc boundaries only |

## Execution and evidence
- Execution command:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_filing.py
  ```
- Evidence directory: `evidence/P03/`.

## Recovery and escalation
- Safe rollback: `git checkout HEAD -- src/trace_lite/filing/ tests/test_filing.py`
