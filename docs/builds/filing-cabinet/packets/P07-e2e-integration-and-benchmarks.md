# P07 — End-to-End Integration & Master Benchmark Suite

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: integration / verification
Contract revision: 2026-09-11.2
Owner/session: Verification Session 7

## Outcome
Execute comprehensive end-to-end integration and verification suite for Trace-Lite:
1. **Full workflow test**: Ingest Obsidian markdown vault -> Extract Hearst facets -> Execute 3-tier queries -> Verify sub-50ms P95 latency.
2. **Cordis harness test**: Run simulated agent micro-steps -> Verify context compiler token budget ($\le 3,500$ tokens) -> Intercept invalid step via TMS authority gate -> Verify RPE delta feedback.
3. **Master benchmark qualification**: Ingest 10,000 documents; verify sustained ingestion rate ($\ge 1,200\text{ docs/sec}$), memory RSS ($\le 500\text{ MB}$), and zero SQLite corruption.

Concrete Example: The test runner executes `pytest tests/` and runs the benchmark script, outputting green verdicts across all subsystems with certified latency histograms.

Failure Case: If any query in the 1,000-query benchmark exceeds 50ms at P95, the benchmark fails and logs the slow query traces.

## Inputs and dependencies
- Required prior packets: P01, P02, P03, P04, P05, P06.
- Target directory: `tests/test_e2e.py`, `benchmarks/run_trace_lite_benchmark.py`.

## Scope and interfaces
- Owned files:
  - `tests/test_e2e.py`
  - `benchmarks/run_trace_lite_benchmark.py`
  - `evidence/P07/trace_lite_benchmark_report.json`

## Suggested approach
1. End-to-end integration test: Execute `pytest tests/test_e2e.py` testing the complete chain from Obsidian note ingestion to 3-tier retrieval.
2. Cordis micro-step test: Execute `pytest tests/test_cordis.py` verifying context compilation under 3,500 tokens, 4-tier authority lattice vetoes, and RPE feedback.
3. Master benchmark qualification: Run `benchmarks/run_trace_lite_benchmark.py --docs 10000` verifying sustained throughput $\ge 1,200\text{ docs/sec}$ and P95 retrieval latency $\le 50\text{ ms}$.

## Acceptance Criteria

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Full test suite passes | `pytest tests/` passes with 100% green | Pytest stdout summary | Zero skipped tests |
| C02 | Sub-50ms query latency verified | Latency benchmark logs P95 <= 50ms | Benchmark report | 1,000 query evaluation |
| C03 | Cordis TMS invariant gate certified | Unauthorized agent proposal blocked; RPE feedback updated | TMS test pass | Poset ordering verified |
| C04 | Ingestion throughput certified | 10,000 documents ingested at >= 1,200 docs/sec | Benchmark timing metrics | Zero locked table errors |

## Execution and evidence
- Execution commands:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python3 benchmarks/run_trace_lite_benchmark.py --docs 10000
  ```
- Evidence directory: `evidence/P07/`.

## Recovery and escalation
- Stop on failure; profile slow queries and memory usage.
