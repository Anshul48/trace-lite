# R08 — Predictive segmentation and selective repair experiments

Status: DRAFT
Kind: experiment
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Conduct controlled ablation experiments to determine whether Prediction-Error Atomization (PEA) and learned selective repair provide statistically significant retrieval quality gains over the qualified structural/graph baseline (R02/R04), without violating runtime budgets.

## Inputs and dependencies

- Required prior packets: R07 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-04; `ARCHITECTURE.md` Section 9; `EVALUATION.md` Section 2.
- Existing baseline: Qualified structural segmentation (R02) and deterministic invalidation (R06).
- Missing facts and readiness checks: Measure inference latency and memory requirements of token surprise / boundary entropy vs structural splitting.

## Scope and interfaces

- Owned scope: `src/trace_lite/experimental/` prediction adapters, evaluation runners, `evidence/query-native/R08/`.
- Shared surfaces: Pluggable boundary signal interface in `src/trace_lite/source/`.
- Non-goals: Do not force deployment of an unverified or heavy model if the experiment is inconclusive.

## Suggested approach

1. Implement boundary surprise estimator (character/subword n-gram entropy, local embedding shift deltas).
2. Run comparative evaluation against frozen structural splits on BEIR and synthetic reasoning datasets.
3. Compare selective repair prediction against bounded deterministic graph invalidation.
4. Evaluate trade-offs: measure nDCG@10 delta vs CPU milliseconds per source.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C8-1 Controlled ablation | Experimental PEA evaluated against structural baseline on identical splits | Paired evaluation run | `evidence/query-native/R08/pea_ablation.json` | Paired t-test / bootstrap |
| C8-2 Qualifier preservation | Boundary cuts do not split antecedent conditions from their respective claims | Condition severance probe | `evidence/query-native/R08/qualifier_preservation.log` | Zero severed conditions |
| C8-3 Cost-benefit accounting | Exact latency and memory cost per source documented for predictive methods | Telemetry profiler | `evidence/query-native/R08/cost_telemetry.txt` | CPU ms & RSS reported |
| C8-4 Objective disposition | Explicit promotion decision recorded: PROMOTE, REJECT, or PARK (experimental) | Formal decision memo | `evidence/query-native/R08/disposition_memo.md` | Evidence-grounded verdict |

## Experiment supplement

- **Hypothesis**: Information-theoretic surprise boundaries improve context compactness and retrieval nDCG by >= 0.03 over fixed paragraph splitting.
- **Baseline**: R02 Structural paragraph/bullet splitter.
- **Data splits**: Held-out split from `workload.json` (frozen in R00).
- **Interpretation rule**:
  - If nDCG improves >= 0.03 at <= 10ms CPU cost: PROMOTE to default pipeline.
  - If nDCG delta is statistically insignificant or latency > 50ms: PARK as experimental fallback; R09 qualifies the structural baseline.

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python benchmarks/run_pea_experiment.py --baseline structural --candidate pea`
  - `python -m pytest tests/test_prediction_error.py -v`
- Evidence Directory: `evidence/query-native/R08/`
- Delivery Record: `evidence/query-native/R08/delivery.md`
- Independent Review: `evidence/query-native/R08/review.md`

## Recovery and escalation

- If the experiment fails or is inconclusive, R09 proceeds cleanly using the qualified structural baseline.
- Do not stall the overall program on inconclusive research findings.

