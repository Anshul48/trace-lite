# P04 — Sub-50ms 3-Tier Dual-Dispatch Retrieval Router

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: implementation
Contract revision: 2026-09-11.2
Owner/session: Builder Session 4

## Outcome
Implement the 3-tier dual-dispatch retrieval router guaranteeing an end-to-end P95 query latency $\le 50\text{ ms}$:
- **Tier 1: Lexical Short-Circuit ($\le 5\text{ ms}$)** for syntax-dense queries (quotes, UPPER_SNAKE, code symbols).
- **Tier 2: Top-Down Faceted Beam Search ($\le 35\text{ ms}$)** navigating Hearst facet trees with in-memory warmed centroid vectors.
- **Tier 3: Global Flat Hybrid Fallback ($\le 25\text{ ms}$)** fusing dense embeddings and FTS5 BM25 via Reciprocal Rank Fusion (RRF).
- **Calibrated Abstention**: Emits provable `insufficient_evidence` when top confidence falls below $\theta_{\text{floor}}$ (0.35).

Concrete Example: A query `get_node_version` contains syntax symbols; Tier 1 detects syntax density and short-circuits via compiled FTS5 in 2.1ms. A conceptual query cascades through Tier 2 beam search, returning in 28ms.

Failure Case: Nonsense or out-of-domain queries must return `verdict="insufficient_evidence"` with empty candidate list, preventing hallucinated hits.

## Inputs and dependencies
- Required prior packets: P02, P03.
- Relevant contract sections: `ARCHITECTURE.md` §2.3, `DESIGN_REVIEW.md` MED-01.
- Target directory: `src/trace_lite/router/`.

## Scope and interfaces
- Owned files:
  - `src/trace_lite/router/__init__.py`
  - `src/trace_lite/router/cascade.py`
  - `src/trace_lite/router/lexical.py`
  - `src/trace_lite/router/beam.py`
  - `src/trace_lite/router/hybrid.py`
  - `src/trace_lite/router/fusion.py`
  - `tests/test_router.py`

## Suggested approach
1. In `lexical.py`: Syntax detector checks for backticks, quotes, underscores, camelCase, path separators.
2. In `beam.py`: Warm facet centroids during router init; execute cosine similarity beam search across facet tree centroids.
3. In `fusion.py`: Standard RRF formula $RRF(d) = \frac{1}{60 + r_{dense}} + \frac{1}{60 + r_{sparse}}$.
4. In `cascade.py`: Coordinate tiers with timing instrumentation and calibrated abstention threshold.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Lexical short-circuit P95 <= 5ms | Run 500 syntax queries; measure latency | Latency report p95 <= 5.0ms | Bypasses dense vector encode |
| C02 | Cascade end-to-end P95 <= 50ms | Run 1,000 mixed benchmark queries | Latency report p95 <= 50.0ms | Zero cloud API spend |
| C03 | Calibrated Abstention | Query gibberish terms; returns `insufficient_evidence` | Abstention test pass | Zero hallucinated hits |

## Execution and evidence
- Execution command:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_router.py
  ```
- Evidence directory: `evidence/P04/`.

## Recovery and escalation
- Safe rollback: `git checkout HEAD -- src/trace_lite/router/ tests/test_router.py`
