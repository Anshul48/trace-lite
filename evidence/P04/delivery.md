# P04 Delivery — Sub-50ms 3-Tier Dual-Dispatch Retrieval Router

- Packet: `docs/builds/filing-cabinet/packets/P04-sub50ms-dual-dispatch-router.md`
- Candidate commit: (this commit — Wave 3)

## Files
- `src/trace_lite/router/lexical.py` — `is_syntax_dense` (quotes/snake/UPPER/camel/paths/
  operators), `lexical_search` (FTS5 OR-of-quoted-terms + BM25, LIKE fallback, match-fraction scores).
- `src/trace_lite/router/beam.py` — `FacetedBeam`: top-3 warmed centroids → member atoms
  (cap 400) → cosine rank. Pure in-RAM after `warm()` (MED-01).
- `src/trace_lite/router/hybrid.py` — `FlatHybrid`: warmed numpy dense matrix + FTS5 sparse
  side fused by RRF; per-anchor calibrated `score=max(dense_cosine, sparse_fraction)` + `rrf`.
- `src/trace_lite/router/fusion.py` — `rrf_fuse` with k=60, stable best-first order.
- `src/trace_lite/router/cascade.py` — `CascadeRouter.route`: T1 short-circuit on ≥3 confident
  syntax hits → T2 beam ≥ θ → T3 hybrid ≥ θ (θ=0.35) → else `insufficient_evidence`, zero hits.
  `warm()` preloads centroids + vectors; `QueryResult.to_dict` maps to answerable/insufficient_evidence.
- `tests/test_router.py` — 4 durable tests (C01/C02/C03 + RRF formula).
- `pyproject.toml` — added `numpy>=1.24.0` (local vector math; already in venv 2.4.6).

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_router.py -q` → **4 passed** (240-doc corpus,
  500-query lexical P95 ≤ 5ms, 1,000-query mixed P95 ≤ 50ms, tiers {1,3} both exercised).
- 1 repair used (test-side: parent facet needed direct members for centroid; product untouched).
