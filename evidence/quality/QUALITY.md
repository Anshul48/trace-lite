# Retrieval Quality — nDCG@10, Abstention, Selective Error (across DBs)

Harness: `benchmarks/run_quality_eval.py`. Labeled corpus of 6 distinct-vocab topics;
144 answerable queries with graded judgments (2 = exact-phrase doc, 1 = same topic)
+ 34 unanswerable (gibberish + off-corpus natural language).

| DB | nDCG@10 | success@10 | MRR | Abstention recall | Coverage | Selective error | Pass |
|---|---|---|---|---|---|---|---|
| 2.4k docs | 0.911 | 1.000 | 0.917 | 1.000 | 1.000 | 0.000 | ✅ |
| 24k docs | 0.911 | 1.000 | 0.917 | 1.000 | 1.000 | 0.000 | ✅ |

Reports: `evidence/quality/quality-2k.json`, `evidence/quality/quality-24k.json`.

## What the eval caught (and fixed)

First run: ranking excellent (nDCG 0.91) but abstention recall **0.29** — off-topic
natural-language queries scored up to 0.61 with zero shared vocabulary. Root cause:
max-over-pool selection bias — the max hash-collision cosine over 400+ candidates in
128 dims reaches 0.6, inside the θ=0.35 boundary. Threshold sweeping could not separate
the distributions (overlap 0.53–0.61).

Fix (substrate-honest, not threshold-hacking): corroboration gate in
`CascadeRouter.route` — Tier 2/3 hits additionally require indexed-term support
(single FTS EXISTS, ~0.2ms; Tier 1 exempt, its hits are lexical by construction).
Pure-vector matches carry no paraphrase capability in a hashed-BoW substrate, so the
conjunction loses nothing real and kills the noise path. Result: abstention 0.29 → 1.00
with coverage still 1.00. Durable test: `test_corroboration_gate_rejects_dense_only_noise`.

Limit note: topics use disjoint vocab, so the lexical check separates cleanly here;
overlapping-vocab corpora will show lower (honest) abstention — re-run the harness
per corpus instead of assuming these numbers transfer.
