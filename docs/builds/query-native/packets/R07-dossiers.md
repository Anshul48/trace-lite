# R07 — Evidence dossiers and Cordis integration

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Compile query-generated evidence dossiers that preserve essential context, conflicting viewpoints, provenance citations, and strict prompt budget accounting, fixing the legacy `goal_id` vs `goal_state` ranking disconnect.

## Inputs and dependencies

- Required prior packets: R06 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-01, QN-09; `ARCHITECTURE.md` Section 8; `EVALUATION.md` Section 3.
- Existing baseline: `src/trace_lite/cordis/compiler.py` (lines ~92–98 rank against `goal_id` string).
- Missing facts and readiness checks: Confirm Cordis / DSH schema compatibility and token budget limits.

## Scope and interfaces

- Owned scope: `src/trace_lite/cordis/compiler.py`, context models, `src/trace_lite/api/` query endpoints, dossier tests.
- Shared surfaces: Downstream agent consumption contracts.
- Non-goals: Do not allow LLM-generated synthesis to masquerade as verified ground truth without provenance.

## Suggested approach

1. Refactor `TraceLiteContextCompiler`: rank candidates against actual objective text (`goal_state['objective']`), not opaque string IDs.
2. Assemble multi-source virtual holons dynamically at query time; retain qualification edges and explicit disagreements.
3. Replace blind whole-atom truncation with anchored excerpt extraction when budget is tight.
4. If mandatory constraints exceed the working memory token ceiling, fail explicitly with `status='insufficient_budget'` rather than dropping invariants.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C7-1 Objective term alignment | Changing opaque `goal_id` while keeping objective constant yields identical ranking | Objective invariance test | `evidence/query-native/R07/objective_align.json` | 100% rank parity |
| C7-2 Cross-source dossier | Compiled dossier integrates spans from multiple sources with exact byte citations | Multi-source dossier probe | `evidence/query-native/R07/cross_dossier.json` | Exact byte spans |
| C7-3 Conflict retention | Dossier surfaces conflicting evidence under explicit disagreement sections | Disagreement query test | `evidence/query-native/R07/conflict_dossier.log` | Retains both sides |
| C7-4 Mandatory budget guard | Oversized invariant set triggers explicit budget failure; zero silent drops | Budget overflow test | `evidence/query-native/R07/budget_guard.log` | Zero silent drops |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_context_compiler.py -v`
  - `python -m pytest tests/test_dossier_compilation.py -v`
- Evidence Directory: `evidence/query-native/R07/`
- Delivery Record: `evidence/query-native/R07/delivery.md`
- Independent Review: `evidence/query-native/R07/review.md`

## Recovery and escalation

- Do not market simple keyword matching as "Natural Language Inference" (NLI).
- Invariant drops or corrupted citations route immediately to B4 repair.

