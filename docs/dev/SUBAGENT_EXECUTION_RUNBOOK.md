# Subagent Execution Runbook: Sequential Phased Builds

This document governs the sequential execution of subagents across Build Alpha and Build Beta.

---

## 1. Execution Principles

1. **Strictly Sequential Execution**: Subagents work one at a time in a deterministic pipeline. A subagent must complete its task and pass all automated tests before the next subagent is dispatched.
2. **Ground Truth from Disk**: Every subagent is instructed to read `docs/dev/GLOBAL_CONTEXT.md` and the relevant `BUILD_*_SPEC.md` from disk before making changes.
3. **Automated Verification at Every Gate**: No branch or step is marked complete without running `uv run pytest -o pythonpath=.` and verifying 0 test failures.

---

## 2. Sequential Subagent Dispatch Pipeline

```mermaid
sequenceDiagram
    autonumber
    participant Coord as Coordinator (Orchestrator)
    participant Agent1 as Subagent 1: Spine FTS5 Specialist
    participant Agent2 as Subagent 2: Lattice & Retrieval Specialist
    participant Agent3 as Subagent 3: Benchmark & CLI Specialist
    participant Agent4 as Subagent 4: Graph Activation Specialist

    Note over Coord: Phase 1: Build Alpha (feature/tri-channel-parity)
    Coord->>Agent1: Dispatch Task: Implement Spine FTS5 (Step 1)
    Agent1-->>Coord: Task Done + Unit Tests Passing
    Coord->>Agent2: Dispatch Task: Lattice Tri-Channel, Gating & Offsets (Steps 2-4)
    Agent2-->>Coord: Task Done + Unit Tests Passing
    Coord->>Agent3: Dispatch Task: Incremental Organize & Benchmark Harness (Steps 5-6)
    Agent3-->>Coord: Task Done + 121 Tests + Baseline Benchmark Locked (report_alpha.json)

    Note over Coord: Phase 2: Build Beta (feature/graph-hipporag)
    Coord->>Agent4: Dispatch Task: Graph Schema, PPR Engine & Quad-Channel Fusion
    Agent4-->>Coord: Task Done + Multi-Hop Tests + HippoRAG Benchmark Locked (report_beta.json)

    Note over Coord: Phase 3: Pareto A/B Analysis & Master Release
    Coord->>Coord: Compare report_alpha.json vs report_beta.json and prepare merge
```

---

## 3. Subagent Task Contracts

### Subagent 1: Spine FTS5 Specialist
- **Specification**: `docs/dev/BUILD_ALPHA_SPEC.md` (Step 1)
- **Target Files**: `src/trace_lite/spine/store.py`, `tests/test_spine.py`
- **Output**: FTS5 virtual table created, `search_lexical` returning normalized scores, unit tests passing.

### Subagent 2: Lattice & Retrieval Specialist
- **Specification**: `docs/dev/BUILD_ALPHA_SPEC.md` (Steps 2, 3, 4)
- **Target Files**: `src/trace_lite/engines/lattice.py`, `src/trace_lite/db.py`, `tests/test_engines.py`
- **Output**: Tri-channel hybrid fusion, sufficiency gating, exact citation offsets, hot inbox search.

### Subagent 3: Benchmark & CLI Specialist
- **Specification**: `docs/dev/BUILD_ALPHA_SPEC.md` (Steps 5, 6), `docs/dev/BENCHMARK_MATRIX.md`
- **Target Files**: `src/trace_lite/engines/benchmark.py`, `src/trace_lite/cli.py`, `benchmarks/`
- **Output**: Incremental organize, `tl benchmark` CLI command, baseline benchmark execution.

### Subagent 4: Graph Activation Specialist
- **Specification**: `docs/dev/BUILD_BETA_SPEC.md`
- **Target Files**: `src/trace_lite/cortex/forest.py`, `src/trace_lite/engines/graph.py`, `src/trace_lite/engines/lattice.py`
- **Output**: `graph_edges` table, `GraphActivationEngine` (PPR), adaptive gating, quad-channel retrieval.
