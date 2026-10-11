# R03 — Independent Review (B3 verifier)

Record type: independent review
Author role and date: R03 independent verifier (B3) / 2026-09-12
Packet and contract revision: R03-retrieval.md / TL-QN-2026-09-12.1
Candidate identity: git HEAD `0df044b51fb3b40ca3360ea7abd78ac36924b9f8`
  + R01+R02 (VERIFIED) + R03/R04 working sets (concurrent; R04 out of scope)

## Execution scope (what the verifier actually ran)

From repo root with `.venv/bin/python` (3.11.15); all exit codes recorded:

| # | Command | Exit | Observed |
|---|---|---|---|
| 1 | `pytest tests/test_router_hybrid.py tests/test_index_adapter.py -q` | 0 | 16 passed (6 + 10) in 3.7 s |
| 2 | `pytest tests/ -q` (full suite, unmodified) | 0 | 155 passed, 1 pre-existing warning (fastapi/httpx StarletteDeprecationWarning) in 21 s |
| 3 | `/tmp/r03_verify_probes.py` (own fixture, own queries, /tmp only — no repo files) | 0 | 13/13 checks pass (see §Probes) |
| 4 | BEIR cross-check script (R03 `beir/*.json` vs `evidence/beir/*.json` core metrics) | 0 | scifact/nfcorpus/fiqa IDENTICAL |
| 5 | `find_spec` probes for `usearch`, `onnxruntime` | 0 | both `None` — fallback premise confirmed live |

## Verdict: PASS

All four criteria reproduce independently. No FAIL-level finding.
Two minor (non-blocking) findings F1–F2 below.

## Evidence C3-1..C3-4

- **C3-1 Full corpus coverage — PASS.** Builder claims 2500/2500 warmed,
  rowid 2450 in dense ranking + IVF top-10. Verifier's own fixture
  (2102 docs, distinct vocabulary, late doc at rowid 2101, unfaceted):
  warm exhaustive (2102 vectors), late doc in `_dense_ranked` pool and in
  hybrid top-10. Tests `test_dense_path_*` / `test_published_index_*` re-run
  green. `DENSE_POOL=2000` is a named parameter (`hybrid.py:17`, `search`
  signature carries `pool`/`scan_cap`); quality path passes
  `scan_cap=None` (unbiased full scan), latency sampling is opt-in and named.
- **C3-2 Representation quality — PASS (numeric gate), with honesty
  qualification (see §Representation honesty).** R03 `beir_eval.json`:
  scifact 0.6589, nfcorpus 0.3188, fiqa 0.2495 — deltas +0.0000, within
  ±0.01 of frozen R00 baselines. R03 `beir/*.json` core metrics byte-agree
  with saved `evidence/beir/*.json` (all three IDENTICAL on
  nDCG/Recall/queries/docs/abstentions); checksums_ok all true.
  `run_beir_unified.py` loads `qrels/test.tsv` for scoring only (nDCG/recall
  accumulation, lines ~117–145); no threshold fitting, training, or tuning
  branch found. ANN query sampling (`sorted(qrels)[::5]`) enumerates test
  query IDs only — relevance labels never steer index/encoder choice
  (IVF params fixed for exact-fidelity; code comment states this).
- **C3-3 ANN recall fidelity — PASS.** Builder: IVF 0.9817 @10 vs exact on
  SciFact-full, 60 systematic queries, n_lists=24/nprobe=20.
  Oracle inspection: `search_exact` is genuine brute force (full-matrix
  `@ q`, `argpartition` over all N, untruncated under filters), sharing only
  the deterministic (-score, atom-id) tie-break with the IVF path — the
  tie-break sharing is correct methodology (recall measures misses, not sort
  order), not a tautology. Verifier confirmed `search_exact` scans the full
  corpus (`scanned == N`) while IVF scans a subset on larger fixtures, and
  reproduced recall 1.0 on an own 300-doc fixture with 4 unseen queries
  (own vocabulary, n_lists=8/nprobe=6). `recall_vs_exact` skips empty-ref
  queries without counting them; checked harmless here — all 60 SciFact
  queries counted (`"queries": 60`), so no masking.
- **C3-4 Targeted corroboration — PASS.** Builder: db-wide FTS True (leak
  signal) vs anchor support False; route abstains with zero anchors.
  Verifier replicated with fresh vocabulary (db-wide generic word "bank"
  present; candidate anchor text unrelated): `has_lexical_support` True
  db-wide, False anchored; `route` → `insufficient_evidence`, zero anchors.
  On-topic guardrail test re-run green (no over-abstention).

## Representation honesty (central question) — HONEST, no overclaim

Delivery reports BEIR Δ=+0.0000 on all three datasets. Verifier determination:
**no learned encoder is active; queries are served by the legacy 128-d
hash-bag path with new plumbing — and the builder says so explicitly.**

- `readiness.md` takes the packet's EXPERIMENT-supplement fallback
  (no network, no usearch/onnxruntime, empty HF cache). Verifier re-probed
  live: `find_spec('usearch')` and `find_spec('onnxruntime')` both `None`.
- `encoder.py` module docstring, `EncoderSpec.weights =
  "none-hashed-sha256-mod-128(degraded)"`, default `degraded=True`;
  `HashBowEncoder.degraded is True` asserted in tests.
- The `DEGRADED-no-learned-weights` label propagates everywhere checked:
  `coverage.json`, `beir_eval.json`, `ann_recall.json` encoder block,
  index manifests (`encoder_label` verified on a verifier-built index),
  delivery record, and the `vector_index` generation receipt
  (`test_publish_records_active_generation_row` asserts
  `encoder.degraded is True`).
- C3-2's numeric gate is therefore met *by construction* (fallback space =
  baseline space; default quality-path ranking semantics byte-identical),
  which the builder discloses outright ("rerun risk is code churn, not
  representation power"). No learned-representation credit is claimed for
  hash-bag scores. **No FAIL-level honesty finding.**

## Disclosed limits check (§5 ANN-vs-task separation kept? YES)

- ANN-vs-task separation kept: `ann_recall.json` carries an explicit
  `task_ndcg_note` ("reported separately in beir_eval.json"); delivery
  table keeps C3-3 (recall) and C3-2 (nDCG) rows distinct. EVAL §5 satisfied.
- DENSE_POOL-style caps named: yes (`DENSE_POOL=2000`, named `pool`/`scan_cap`
  params; `dense_scan_cap_quality: null` in coverage.json).
- IVF params + latency honest: n_lists=24/nprobe=20 stated as fidelity-tuned
  (not qrel-tuned); p50 4.0 ms / p95 8.1 ms reported; adversarial-filter
  recall dip + `adequate` flag disclosed; IVF recall measured on
  SciFact-full + synthetic only (not NFCorpus/FiQA-full) — disclosed.
- `scan_cap=2000` recall cost unquantified, no power-loss test, no 1M sizing —
  all disclosed as not-run. No silent sampling found.

## Scope check — CLEAN for R03

`git status --short` dirty set attributed: R03 owns only
`src/trace_lite/router/{encoder,index_adapter,projection}.py` (new),
`router/__init__.py` (diff verified additive-exports-only),
`tests/test_router_hybrid.py`, `tests/test_index_adapter.py` (new),
`evidence/query-native/R03/`. All other dirty files (R01/R02/R04 migrations
in `store/schema.sql` labeled by packet, `parser.py` R02 fields,
`graph/`/`source/`/`store/{generations,migrate,outbox}.py`, sibling tests,
coordinator docs) belong to concurrent packets — untouched by R03.
No R03 finding. One dependency note (not a finding): R03's
`build_and_publish`/`publish` import R01-owned `store.generations`
(VERIFIED base); the R03 publication path is not self-contained outside
this candidate tree.

## Findings

- F1 (minor): `test_dense_caps_are_named_not_silent` contains two vacuous
  assertions (`assert CascadeRouter.__init__.__code__.co_consts`,
  bare `assert True`). The test's real assertions (DENSE_POOL==2000,
  signature params) are genuine; the placeholders should be replaced with
  profile-behavior checks in a follow-up. Not blocking.
- F2 (minor): `recall_vs_exact` silently skips queries with empty exact refs.
  Verified harmless on current evidence (60/60 counted). Suggest reporting
  `skipped_empty` count for future larger fixtures. Not blocking.

## Next action

R03 may proceed to coordinator sign-off as **PASS on fallback** (learned
representations explicitly deferred for lack of network, not delivered).
Recommend the coordinator record follow-up debt: (a) learned-encoder swap
packet when network/weights available (config-hash + generation-row
machinery already supports it); (b) F1/F2 test-hygiene cleanup; (c) NFCorpus/
FiQA-full IVF recall + `scan_cap` recall-cost quantification before any
latency-profile qualification claim.
