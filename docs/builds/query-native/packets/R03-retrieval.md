# R03 — Learned representations and global indexes

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Provide 100% corpus candidate eligibility using lexical and measured learned representations, with an embedded ANN option (e.g. usearch/HNSW) and a crash-recoverable index publication protocol.

## Inputs and dependencies

- Required prior packets: R02 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-06, QN-08; `ARCHITECTURE.md` Section 4; `EVALUATION.md` Section 3.
- Existing baseline: `src/trace_lite/router/hybrid.py`, `src/trace_lite/router/lexical.py`.
- Missing facts and readiness checks: Measure memory footprint of exact vector dot products vs embedded ANN on CPU.

## Scope and interfaces

- Owned scope: `src/trace_lite/router/` index adapters, encoder wrapper, projection workers, retrieval benchmarks.
- Shared surfaces: Router dispatch interface consumed by R05, R06, R07.
- Non-goals: Do not hardcode a single deep learning framework; support pluggable local ONNX / lightweight backends.

## Suggested approach

1. Implement an encoder adapter for learned representations (e.g., quantized ONNX MiniLM or fast local embeddings) with exact metadata.
2. Replace flat NumPy scanning with an embedded ANN adapter (e.g., `usearch` or SQLite vector index) supporting add, search, delete, and snapshot save/load.
3. Fix corroboration gate: check lexical support between query and candidate text specifically, not database-wide matching.
4. Ensure unfiled documents and items late in row-ID order remain fully eligible for vector ranking.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C3-1 Full corpus coverage | Documents with rowid > 2000 and outside facets participate in vector scoring | Exhaustive row retrieval test | `evidence/query-native/R03/coverage.json` | 100% eligibility |
| C3-2 Representation quality | Frozen BEIR SciFact and NFCorpus nDCG@10 meet or exceed frozen baseline | Run `run_beir_unified.py` | `evidence/query-native/R03/beir_eval.json` | nDCG@10 >= baseline |
| C3-3 ANN recall fidelity | Embedded ANN achieves >= 95% Recall@10 compared to exact brute-force vector search | ANN vs Exact recall test | `evidence/query-native/R03/ann_recall.json` | Recall@10 >= 0.95 |
| C3-4 Targeted corroboration | Off-topic query with generic vocabulary correctly abstains without false positive | Corroboration gate probe | `evidence/query-native/R03/abstention_test.json` | Zero spurious hits |

## Experiment supplement

- Hypothesis: Quantized local ONNX embeddings eliminate 128-d hash-collision gray noise and raise FiQA/NFCorpus quality while staying < 5ms CPU search.
- Baseline: Hashed bag-of-words FlatHybrid (`hybrid.py`).
- Fallback: If ONNX runtime is unavailable in host environment, retain optimized exact lexical + sparse projection with explicit degraded labeling.

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_router_hybrid.py -v`
  - `python -m pytest tests/test_index_adapter.py -v`
- Evidence Directory: `evidence/query-native/R03/`
- Delivery Record: `evidence/query-native/R03/delivery.md`
- Independent Review: `evidence/query-native/R03/review.md`

## Recovery and escalation

- If embedded ANN introduces memory leaks or segmentation faults, fall back immediately to chunked exact scoring.
- Halt dependent packets if vector index manifests fail persistence across process restarts.

