# P05 — DeepSeek Harness (Cordis / DSH) Executive Plugin Suite

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: implementation
Contract revision: 2026-09-11.2
Owner/session: Builder Session 5

## Outcome
Implement the formal DeepSeek Harness (`dsh` / Cordis runtime) executive plugin suite. Adheres strictly to the Pydantic 2 schema specifications and formal protocols in `research_specifications/schemas/interfaces.py`:
- `TraceLiteMemoryPlugin` implementing `ITraceLiteMemoryPlugin` and `ILedgerService`: Unified async storage contract for events, atoms, and faceted queries.
- `TraceLiteContextCompiler` implementing `IContextCompilerMiddleware`: Compiles micro-step prompt context within strict token budgets ($\le 3,500$ tokens), prioritizing higher authority invariants.
- `ModularContractBoundaryTMS` implementing `IMCBTMSEngine`: Enforces the 4-tier authority lattice: `USER (4) > ARCH_SPEC (3) > AGENT_DECISION (2) > TOOL_OUTPUT (1)`.
- `StepAcceptanceGate` implementing `IStepAcceptanceGate`: Pre-commit step verification with Reward Prediction Error (RPE) feedback ($\Delta = +1.0$ on accept, $\Delta = -1.0$ on reject).

Concrete Example: An agent proposing an action that violates an ARCH_SPEC constraint is intercepted by `ModularContractBoundaryTMS.evaluate_invariants()` and vetoed. The step gate records a rejection, and applies an RPE penalty ($\Delta = -1.0$) to reinforce constraint adherence.

Failure Case: A lower authority level attempting to modify or revoke a higher authority level (e.g. AGENT_DECISION attempting to revoke USER invariant) raises an immediate veto error (`InvariantVetoError`).

## Inputs and dependencies
- Required prior packets: P04.
- Source specifications: `/mnt/c/Users/anshu/OneDrive/Desktop/trace lite and trillion dreams/research_specifications/schemas/interfaces.py`.
- Target directory: `src/trace_lite/cordis/`.

## Scope and interfaces
- Owned files:
  - `src/trace_lite/cordis/__init__.py`
  - `src/trace_lite/cordis/plugin.py`
  - `src/trace_lite/cordis/compiler.py`
  - `src/trace_lite/cordis/tms.py`
  - `src/trace_lite/cordis/gate.py`
  - `tests/test_cordis.py`
- Conformance: Full type compatibility with `AuthorityLevel`, `TraceEvent`, `AtomRecord`, `FacetedQuery`, `CompiledContext`, `GateVerdict`.

## Suggested approach
1. Import and extend models from `research_specifications/schemas/interfaces.py`.
2. In `tms.py`: Implement dominance checking via `authority.dominates(other)`. Maintain DAG of active invariants.
3. In `compiler.py`: Dynamic context budgeting: allocate max 3,500 tokens across system laws, active goals, and retrieved citations.
4. In `gate.py`: Verify proposed action against active invariants; update RPE running delta.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | 4-Tier Authority Lattice enforcement | AGENT_DECISION cannot override USER invariant; raises InvariantVetoError | Authority unit test pass | Formal poset ordering verified |
| C02 | Micro-Step Context Compiler budget | Context compiled for 100 steps; token count never exceeds 3,500 | Compiler test pass | Highest authority invariants prioritized |
| C03 | RPE feedback loop | Failed step applies delta = -1.0; accepted step applies delta = +1.0 | RPE test pass | State tracked deterministically |

## Execution and evidence
- Execution command:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_cordis.py
  ```
- Evidence directory: `evidence/P05/`.

## Recovery and escalation
- Safe rollback: `git checkout HEAD -- src/trace_lite/cordis/ tests/test_cordis.py`
