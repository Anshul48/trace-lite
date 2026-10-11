# INT-3 — Delivery (learned-retrieval spike, B2 vs fallback)

Contract TL-QN-2026-09-12.1. Builder role, bounded spike: prove or disprove
learned retrieval on the SERVED path. Either outcome acceptable if measured.
Base: `evidence/query-native/_integration/candidate_manifest.json`
(HEAD `0df044b`) + INT-2 served seam. No other writer active during the spike.

## 1. Artifact (step 1)

- Model: `BAAI/bge-small-en-v1.5` (384-d, 512-token limit, CLS pooling)
- Revision: `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a` (HF `refs/main`)
- Download: 134,590,228 bytes (~128.4 MiB, within the 500 MB spike cap)
- Cache: `/tmp/int3_hf/hub/models--BAAI--bge-small-en-v1.5`
  (the image default `~/.cache/huggingface` is a read-only mount —
  `OSError: [Errno 30] Read-only file system` on first fetch; the harness
  sets `HF_HOME=/tmp/int3_hf`)
- Load (CPU, torch 2.13.0+cu130, sentence_transformers 5.7.0, 8 threads):
  33.4 s from local snapshot; smoke encode 384-d, L2 norm 1.0
- Stack versions: torch 2.13.0+cu130 (CPU inference only), transformers 5.14,
  sentence_transformers 5.7.0. No new dependencies installed (step 2:
  nothing to install; `uv` untouched, system interpreter untouched).

## 2. Encoder (step 3, owned files)

- `src/trace_lite/router/learned.py` (new): `LearnedBgeEncoder` behind the
  INT-2 seam (`fit_idf` no-op / `encode_weighted` / `encode_many` /
  `config_hash` / `spec`, `degraded=False`).
- Pinned: no prompts either side (symmetric), CLS pooling (model-pinned
  `1_Pooling` config), L2 norm, bert-uncased WordPiece tokenizer.
- Long-span policy (never silent truncation): one batched length check;
  texts over 510 content tokens take overlapping windows (510, stride 458,
  52 overlap), each window re-verified to re-tokenize within the limit
  (decode→re-encode inflation is halved until it fits), window vectors
  mean-pooled + renormalized. Build records `chunked_texts`/`chunk_windows`.
  (Mid-spike hardening: the first planner trusted decode roundtrips.
  Investigation showed the `>512` warnings in the logs come from the
  planner's own untruncated length probe (`max_length=None` → the
  tokenizer warns), never from the model forward — the encode path always
  truncates at 512 and every planned chunk is ≤510 content tokens, so no
  span was ever silently truncated. The verify-and-halve fix plus
  `verbose=False` landed before the fiqa arm. Cross-planner replication:
  scifact learned 0.6562 / +0.0397 and nfcorpus learned 0.3240 / +0.1077
  under BOTH planners (point estimates identical to 4dp) — the hole was
  immaterial. All reported numbers use the hardened planner.)
- Seam wiring (`cascade.refresh_served_vector` + `router/__init__` exports,
  additive only): consults `learned.served_default()` when no explicit
  encoder is passed (None = pinned hashbow fallback, byte-identical path),
  and uses `encode_many` when the encoder offers it. Fallback behavior
  unchanged — proven by the served fallback arms reproducing the frozen
  baselines' configuration deterministically (scifact rerun: 0.6165 twice).
- `tests/test_learned_retrieval.py` (new): pinning identity, light import
  (no torch/ST at library import), weights-missing `WeightsMissingError`
  (degraded path), served-default-None fallback preservation, plus a live
  weights test gated on `INT3_LIVE=1` + cached snapshot (skipped by default
  so the suite never pays torch import or a download).

## 3. Method (step 4, served path)

- Harness `/tmp/int3_spike_eval.py` (scratch, not committed): per dataset,
  bulk-ingest the frozen BEIR corpus into a scratch DB on `/tmp` (ext4),
  serve via `TestClient(create_app(db))` + `/api/query` (limit 10,
  mode hybrid) and `/api/dossier` (first 3 test queries, fixed order).
  Startup publishes the filing generation; the first query builds the
  served vector index. Both arms share the IDENTICAL served configuration;
  only the served vector encoder differs. Scoring mirrors
  `benchmarks/run_beir_unified.py` exactly (gain 2^rel-1, nDCG@10,
  Recall@10, 10k-resample bootstrap seed 42) plus a paired bootstrap CI on
  the per-query learned-minus-fallback delta.
- Splits: scifact full (5183 docs / 300 test queries), nfcorpus full
  (3633 docs / 323 test queries), fiqa FIXED sample (first 5000 corpus docs
  in file order — full 57k exceeds the 20k served build cap; all 648 test
  queries; unindexed relevant docs score as misses under the same rule for
  both arms). FINAL test.tsv only, never tuned.
- ANN-vs-exact (`/tmp/int3_cost_ann.py`): same encoder + same IVF heuristic
  in the learned space, served nprobe=8 plus nprobe=20 sensitivity.

## 4. Results (served path, TestClient + /api/query + /api/dossier)

Machine-readable: `spike_eval.json` + per-query
`perquery-{ds}-{arm}.jsonl`; costs: `cost.json` (+ `cost-{ds}.json`).

| dataset (split) | fallback nDCG@10 [95% CI] | learned nDCG@10 [95% CI] | paired Δ [95% CI] | R@10 fb→lrn | routes fb → lrn |
|---|---|---|---|---|---|
| scifact full (5183 docs / 300 q) | 0.6165 [0.5682, 0.6631] | 0.6562 [0.6100, 0.7006] | **+0.0397 [+0.0199, +0.0623]** | 0.7298→0.7698 | lex271/vec26/hyb3 → lex271/vec27/none2 |
| nfcorpus full (3633 docs / 323 q) | 0.2162 [0.1831, 0.2503] | 0.3240 [0.2894, 0.3588] | **+0.1077 [+0.0801, +0.1352]** | 0.0960→0.1442 | vec143/hyb138/lex22 → vec231/hyb54/lex22 |
| fiqa fixed sample (first 5000 docs file order / 648 q) | 0.0209 [0.0136, 0.0293] | 0.0586 [0.0458, 0.0723] | **+0.0377 [+0.0268, +0.0491]** | 0.0226→0.0595 | vec468/hyb48/lex131 → vec506/hyb9/lex131 |

All three paired 95% CIs exclude zero: learned retrieval WINS on the
served path on 3/3 datasets. Zero HTTP errors in 2,542 served queries;
abstentions flat-or-better (scifact 0→1, nfcorpus 20→16, fiqa 1→2).
Dossiers (`/api/dossier`, first 3 test queries per dataset, fixed order):
identical `insufficient`/0-selection outcomes on both arms, no errors —
dossier integrity unchanged (BEIR factoid queries simply do not meet the
compiler's support bar under either encoder).

Serving cost (served config, CPU-only):
- Steady-state per-query client latency p50/p95 ms —
  scifact 22.3/40.3 → 23.1/82.5; nfcorpus 27.0/89.8 → 34.2/107.7;
  fiqa 8.6/44.4 → 35.5/73.5. Single-query BGE encode dominates the delta.
- Cold first query after a generation swap (synchronous in-index build on
  the query path): fallback 4.9–7.4 s vs learned 436–770 s (scifact 738 s,
  nfcorpus 770→604 s rerun, fiqa 436 s for 3.6–5.2k docs at 7–16 docs/s).
  Writers publish a filing generation per data transaction, so every write
  batch re-arms this stall for the next query.
- ANN-vs-exact in the learned space at the served nprobe=8 (EVAL §5
  separation — fidelity, not task quality): scifact 0.8927, nfcorpus
  0.7650, fiqa 0.9255; nprobe=20 sensitivity: 0.9723 / 0.9297 / 0.9886
  (IVF search itself is 1–8 ms; the served default leaves recall on the
  table, tunable later).
- Index RAM: 5.6–8.0 MB matrices (384-d fp32); process RSS delta with
  weights 0.9–1.6 GB (torch runtime included). Model disk 128 MB ≤ 500 MB.
- Served fallback arms are deterministic (scifact fallback 0.6165 twice
  across runs); the scifact learned arm was re-run end-to-end with the
  hardened chunk planner and confirmed 0.6562 / +0.0397 (cross-planner
  point estimates identical to 4dp).
- R00 floors (0.65/0.32/0.38) are library-profile gates; neither served arm
  is measured in that profile (served Tier-3 is unwarmed, vector branch +
  gates apply), so floors are reported, not claimed — cause documented here.

## 5. Disposition: KEEP-FALLBACK (default unchanged) — quality gate PASSES, serving-cost gate FAILS

Per EVALUATION §2 (gain + paired uncertainty + ALL regression/cost gates):
- Primary metric: PASS — significant paired nDCG@10 gains on 3/3 datasets.
- Regression: PASS — no error/abstention/dossier regressions; pre-existing
  tests untouched and green (full suite below).
- Cost/availability: FAIL — flipping the default would put a 7–13 minute
  synchronous rebuild on the first query after every generation swap
  (i.e. after every write batch), vs 5–7 s for hashbow. The INT-2 §4 known
  gap (in-memory build at swap time, no background publication, no
  published-segment reads) becomes load-bearing under a 100× slower
  encoder. ANN fidelity at the served nprobe=8 (0.765 nfcorpus) is also
  below the posture R03 held (0.98 at nprobe 20) and needs its own served
  measurement before it can ride the default path.

Science verdict: learned retrieval on the served path is PROVEN (not
blocked, not failed science). Promotion is deferred to a serving packet:
background/async index publication (or published-segment reads on the
served path) + nprobe selection with served latency/fidelity numbers +
a numeric cold-query latency gate. No default flipped, no fallback
semantics changed (`served_default()` remains None unless set).

## 6. Verification

- New tests `tests/test_learned_retrieval.py`: 5 passed + 1 live-weights
  test skipped by default; with `INT3_LIVE=1` + cached snapshot: 6 passed
  (chunk policy, 384-d, unit norm).
- Full suite: `.venv/bin/python -m pytest tests/ -q` → 256 passed,
  1 skipped (the gated live test), 0 failures. Pre-existing tests
  unmodified (worktree `tests/` diff empty).
- No installs (step 2); no migrations; no `store/`/`graph/`/`filing/`/
  `source/`/`cordis/`/`api/`/docs/ changes. Owned footprint only:
  `src/trace_lite/router/learned.py` (new), `router/cascade.py` +
  `router/__init__.py` (additive seam wiring), `tests/test_learned_retrieval.py`
  (new), `evidence/query-native/_integration/INT-3/`.
