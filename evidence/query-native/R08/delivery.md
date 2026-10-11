# R08 — Delivery (experiment; PARK)

Record type: experimenter delivery
Author role and date: R08 experimenter / 2026-09-12
Packet and contract revision: R08-prediction.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01–R07
INTEGRATED (uncommitted working tree, single writer; no other writer active)
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5,
numpy 2.4.6, pytest 9.1.1; bench data read-only from trace-bench-data
Interpreter: `.venv/bin/python` from repo root (contract)

## Delivery section

- Behavior/artifact changed and why: controlled PEA ablation (C8-1..C8-4).
  New `source.boundary_signals` declares the pluggable boundary-signal
  interface (protocol + registry + byte-exact split helpers; additive —
  `segmentation.py`/`views.py` behavior untouched). New
  `experimental/` package implements the two packet-specified scorers with
  no model downloads (network unavailable, HF cache empty): char-trigram
  surprisal fitted per document (`surprise.py`) and local cosine-distance
  deltas in the pinned R03 hashbow space (`shift.py`), applied as
  split-only refinements of structural spans (`segment.py`, byte contract
  verified per span), plus qualifier fixtures (`qualifiers.py`) and the §6
  repair comparison (`repair.py`). Runner
  `evidence/query-native/R08/run_ablation.py` performs the paired ablation
  (nothing under `benchmarks/` added or modified).
- Files changed (owned set ONLY):
  - `src/trace_lite/source/boundary_signals.py` (new interface module)
  - `src/trace_lite/source/__init__.py` (additive export only)
  - `src/trace_lite/experimental/{__init__,surprise,shift,segment,qualifiers,repair}.py` (new)
  - `tests/test_prediction_error.py` (new, 17 tests)
  - `evidence/query-native/R08/{run_ablation.py,pea_ablation.json,qualifier_preservation.log,cost_telemetry.txt,disposition_memo.md,delivery.md}` (this packet)
- Material implementation decisions:
  - Split-only refinement, never merge, never across structural spans, never
    into code/table/heading; per-document adaptive cut rule (mean+0.5σ,
    max 2 cuts/span, 120-char floor) — zero parameters tuned on any qrels,
    so DEV runs are measurements and test.tsv needed exactly one frozen run.
  - Paired bootstrap (10k resamples, seed 42, identical draws for deltas).
  - Repair thetas (0.02/0.05/0.10) are a-priori reporting points; all three
    reported, none selected.
- B1-equivalence / readiness gap note (no separate readiness file; recorded
  here per dispatch): retrieval is a B1-*equivalent* in-memory flat hybrid
  (BM25-lite k1=1.2/b=0.75 over porter stems, pool 50; hashbow-128
  IDF-cosine, pool 200; RRF K=60 fused[:100]; max-score span→doc dedup;
  nDCG@10 gain 2^rel−1), NOT the production CascadeRouter (no beam, no
  corroboration gate, dense pool 200 vs quality 2000, span-level vs
  doc-level). Absolute nDCG is therefore harness-relative — e.g. structural
  scifact-test scores 0.4968 here vs the frozen R00 baseline 0.6589 — and
  only the paired deltas are claims. DEV used train splits exclusively
  (scifact 150 / nfcorpus 100 / fiqa 60 sorted qids; full corpora);
  test.tsv scored once for the frozen candidates (300/323/648 queries).
- Known limitations and checks not run: no learned embeddings (no network);
  hashbow space is near-lexical, which handicaps the shift signal by
  construction — disclosed, not corrected; synthetic diagnostic is 8
  queries (no power); no power-loss or 1M-scale checks (out of scope).

## Evidence

| Criterion | Command or evaluation method (exit) | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C8-1 Paired ablation | `.venv/bin/python evidence/query-native/R08/run_ablation.py` (exit 0, 1171 s) | TEST ΔnDCG all negative: surprise −0.0295/−0.0144/−0.0280, shift −0.0163/−0.0171/−0.0289 (scifact/nfcorpus/fiqa); 4/6 CI-negative; DEV agrees | `evidence/query-native/R08/pea_ablation.json` | B1-equivalent harness; paired deltas only |
| C8-2 Qualifier preservation | same run + `test_predictive_cuts_sever_qualifiers` | +surprise severs 12/12, +shift 10/12; structural 0/12 | `evidence/query-native/R08/qualifier_preservation.log` | 12-fixture probe |
| C8-3 Cost accounting | same run (`process_time` + tracemalloc + `ru_maxrss`) | structural 0.24/0.47 ms, surprise 19.3/31.2 ms, shift 5.9/10.6 ms (mean/p95 per source) | `evidence/query-native/R08/cost_telemetry.txt` | 200-doc sample |
| C8-4 Disposition | `disposition_memo.md` | PARK: no +0.03 anywhere, severance > 0, predictor misses negations | `evidence/query-native/R08/disposition_memo.md` | unanimous grounds |
| §6 repair comparison | same run (`repair_probe`) | det misses 0 (false-inv 4); pred@0.05 misses negation; pred@0.10 misses 3/5 meaningful | `pea_ablation.json` → `repair` | 7-edit fixture |
| New tests | `.venv/bin/python -m pytest tests/test_prediction_error.py -q` (exit 0) | 17 passed | `tests/test_prediction_error.py` | — |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 237 passed (220 pre-existing + 17 new), 1 pre-existing warning | session log | pre-existing obsidian load-flake passes alone (see below) |

## Independent findings

(none — experimenter role; reviewer uses review.md)

## Suite-flake note (pre-existing, not a regression)

`tests/test_obsidian_sync.py::test_concurrent_ingest_and_query_stress` failed
under full-suite/file-level load pre-change and passed alone — the exact
load-flake R06 documented on the untouched base (verified pre-change: 219
passed + this 1 flake, then 1 passed alone). The post-change full run above
was 237/237 fully green. No R08 file touches obsidian, sync, or
concurrency paths.
