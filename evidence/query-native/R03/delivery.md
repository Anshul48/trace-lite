# R03 — Delivery (B2)

Record type: builder delivery
Author role and date: R03 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R03-retrieval.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01+R02 sets
(R02 VERIFIED base; R04 builds concurrently — no R04-owned files touched)
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite + FTS5, numpy 2.4.6,
pytest 9.1.1; bench data read-only from trace-bench-data; DBs in /tmp ext4

## Delivery section

- Behavior/artifact changed and why: packet-FALLBACK retrieval plane
  (B1: no network — `uv`/HF downloads impossible, usearch/onnxruntime absent,
  HF cache empty). Pinned sparse-projection encoder honestly labeled
  **DEGRADED** (no learned weights), full-corpus eligibility kept, numpy-IVF
  ANN over the same space, crash-safe index publication, targeted
  corroboration gate confirmed end-to-end (C3-1..C3-4).
- Files changed (owned set ONLY):
  - `src/trace_lite/router/encoder.py` (new: `EncoderSpec` pinned
    tokenizer/weights/runtime/pooling/normalization + `config_hash`,
    `HashBowEncoder` with `degraded=True`, `DEGRADED-no-learned-weights` label)
  - `src/trace_lite/router/index_adapter.py` (new: `VectorIndex` numpy-IVF —
    deterministic kmeans++ train, exact-oracle `search_exact`, filtered search
    with overretrieve + `adequate` coverage flag, tombstone deletes,
    save→manifest→CURRENT→generation-row publish with fsync, hash-verified
    `open` restart recovery, orphan report/collect)
  - `src/trace_lite/router/projection.py` (new: bounded-batch encoding outside
    the writer lock, `build_and_publish` with superseded-watermark guard,
    records `projection_generation` kind `vector_index` — no schema migration)
  - `src/trace_lite/router/__init__.py` (additive exports only)
  - `tests/test_router_hybrid.py` (new, 6 tests), `tests/test_index_adapter.py`
    (new, 10 tests); pre-existing tests unmodified
  - `evidence/query-native/R03/{readiness.md,delivery.md,gen_evidence.py,
    coverage.json,beir_eval.json,beir/beir-{scifact,nfcorpus,fiqa}.json,
    ann_recall.json,abstention_test.json}` (this packet)
- Material implementation decisions:
  - ANN-vs-exact oracle is untruncated under filters (product overretrieve
    bounds must not truncate the recall reference); both paths share
    deterministic (-score, atom-id) tie-breaks so recall measures misses.
  - Filtered search overretrieves then flags `adequate=False` instead of
    claiming "no evidence" when the filter removes the top-k (ARCH §7).
  - IVF config (n_lists=24, nprobe=20 at 5k scale) chosen for ANN-vs-EXACT
    fidelity only — qrels never involved, so FINAL-split isolation holds.
  - Adversarial filters (allowed set far from query) still dip in recall;
    covered by the `adequate` flag, disclosed below.
  - Default quality-path ranking semantics untouched: BEIR reruns reproduce
    frozen baselines bit-for-bit (deltas 0.0).
- Known limitations and checks not run: DEGRADED encoder (hash collisions
  remain; learned-encoder swap is a future packet with network); IVF recall
  measured on SciFact-full + synthetic, not NFCorpus/FiQA-full; latency
  profile (`scan_cap=2000`) recall cost not quantified here; no power-loss
  test (injected crash-order only); no 1M-scale index sizing (R09 ladder).

## Evidence

| Criterion | Command or evaluation method (exit) | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C3-1 Full corpus coverage | `.venv/bin/python evidence/query-native/R03/gen_evidence.py` (exit 0) + `test_dense_path_*`, `test_published_index_*` | 2500/2500 warmed exhaustive; rowid 2450 unfaceted in dense ranking + IVF top-10 | `evidence/query-native/R03/coverage.json` | 2.5k bounded fixture; DENSE_POOL=2000 named cap |
| C3-2 Representation quality | `.venv/bin/python benchmarks/run_beir_unified.py --dataset {scifact,nfcorpus,fiqa} --out-dir evidence/query-native/R03/beir` (exits 0/1/1 — script's own trace-floor gate; contract gate below) + `/tmp/merge_beir.py` (exit 0) | scifact 0.6589 (+0.0000), nfcorpus 0.3188 (+0.0000), fiqa 0.2495 (+0.0000) — all within ±0.01 | `evidence/query-native/R03/beir_eval.json`, `beir/` | FINAL test.tsv scored, never tuned; fiqa run, not sampled |
| C3-3 ANN recall fidelity | same gen script (exit 0) + `test_ivf_recall_*`, `test_recall_with_*` | IVF 0.9817 @10 vs exact on SciFact-full (60 systematic queries); n_lists 24/nprobe 20; p50 4.0 ms/p95 8.1 ms; ANN recall kept SEPARATE from nDCG | `evidence/query-native/R03/ann_recall.json` | hashbow space; adversarial-filter dip disclosed, `adequate` flag |
| C3-4 Targeted corroboration | same gen script (exit 0) + `test_off_topic_*`, `test_on_topic_*` | db-wide FTS True (leak signal) vs anchor support False; route abstains, zero anchors, zero spurious hits; on-topic still answerable | `evidence/query-native/R03/abstention_test.json` | single-term-overlap gate semantics unchanged |
| Publication durability | `test_publish_then_restart_recovery`, `test_crash_before_publish_*`, `test_segment_tamper_*`, `test_orphans_*`, `test_publish_records_*`, `test_stale_encoder_*` (all pass) | reopen identical results; pre-publish crash keeps gen-c1; tamper raises; orphans collected; generation row active with manifest hash | new test files | injected order faults, not power loss |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 155 passed, 1 pre-existing warning | session log | one transient extractor failure mid-run passed alone + full suite green (concurrent-checkout interference, not a regression) |

## Independent findings

(none — builder role; verifier uses review.md)
