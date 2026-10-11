# TL Finish-Line Review — Coordinator Plan

## Objective
Answer: do we have a complete roadmap to the finish line for trace-lite?
Deliver: comprehensive arch evaluation (failure points) + directed epistemic
foraging (solutions) as one unified Architecture & Roadmap Dossier.

## Context
- Parked: Gate 2 qualification (contract TL-QN-2026-09-13.3). Old gates
  governed the single-node candidate; they do not bind the new direction.
- Organizing principle: atoms → overlapping holons → higher-level holons →
  selectively activated regions.
- Key disputes to settle with evidence, not assertion:
  1. Single-writer SQLite vs federated shelves (write amp, WAL, checkpoint,
     transaction shapes — not record count alone).
  2. Semantic holon vs physical partition (must be allowed to differ).
  3. Spreading activation in TL: compatible with exact citations when
     association justifies LOOKING, never asserting.
  4. Which sparsity (membership, connectivity, activation, routing, codes) —
     with absolute work budgets, not fixed percentages.
  5. DG-style pattern separation: experiment, not requirement, until measured.
  6. Hub suppression: traversal controls + competitive selection, not a static
     penalty alone.
  7. Precomputation: cache intermediate products; measure uncached path.

## Pipeline (workflow tl-arch-roadmap-review, live)
1. Cartographer (RUNNING): surveys trace-lite + utilities/trace + desktop
   design docs; writes /tmp/tl-arch-review/CONTEXT.md; proposes 5–8 chunks.
2. Parallel chunk audits (up to 7): adversarial failure mechanics with
   file:line refs + external solutions with URLs + minimal vs structural fix.
   Default map: evidence/ledger; semantic topology; associative traversal;
   serving/maintenance; federation/storage evolution; hardware/data plane;
   evaluation/scale ladder.
3. Critic: concrete gaps only (uncited claims, boundary contradictions,
   unmeasured nDCG, missing P95/tail, WAL, citation/retraction semantics).
4. Gap follow-ups (up to 3, one per lineage).
5. Synthesis: writes /tmp/tl-arch-review/ARCH_ROADMAP_DOSSIER.md, preserves
   every unresolved item, discloses omitted scope.

## Dossier acceptance (coordinator checks before answering)
- Failure inventory with severity + file refs + scale impact to 100k/250k/1M.
- Per-chunk minimal vs structural recommendations with trade-offs.
- Cross-cutting: soft vs hard state, routing recall + fallback, hub handling,
  sparsity choices, precomputation limits, hardware boundaries, Lite definition.
- Phased roadmap: frozen reproducers, measured gates, three-way comparison
  (incremental single-node vs partitioned vs partitioned+expansion) on one
  frozen realistic corpus; quality diagnosed BEFORE combined serving test.
- Open gaps verbatim + omitted-scope disclosure.

## Roadmap question — answer shape
- Direct verdict: roadmap complete or not, with what is proven vs open.
- What Lite means now (evidence-memory + bounded associative search +
  autonomous maintenance; TRACE adds reasoning/executive behavior).
- Next implementation commitment: stable evidence ownership, independently
  maintained projections, bounded query work; biology-flavored mechanisms stay
  experimental until measured.
