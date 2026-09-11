# Scale Ladder — 100k → 250k → 1M Qualification

All gates: ingest ≥ 1,200 docs/s, retrieval P95 ≤ 50ms, RSS ≤ 500MB, Cordis gate, zero SQLite errors.

| Rung | Ingest | Retrieval p50/P95 | RSS | Pass | Report |
|---|---|---|---|---|---|
| 100k | 3,536 docs/s (28.3s) | 2.9 / 11.7ms | 165MB | ✅ | `evidence/scale/bench-100k.json` |
| 250k | 2,572 docs/s (97.2s) | 3.1 / 28.2ms | 346MB | ✅ | `evidence/scale/bench-250k.json` |
| 1M | 8,631 docs/s (115.9s) | 2.9 / 6.1ms | 412MB | ✅ | `evidence/scale/bench-1m.json` |

1M tier split: T1 P95 1.3ms / T2 3.9ms / T3 6.8ms; verdicts 825 answerable / 175 abstentions.

## What each rung forced (no research team needed — direct fixes sufficed)

- 100k red (P95 227ms) → profiled, not guessed: membership `DISTINCT` sort → covering-index
  ORDER+LIMIT with per-facet cap; per-query vector recompute → shared warmed matrix;
  FTS `OR`+rank over 25k matches → AND-first + unranked pools; FTS JOIN pathology
  (43ms vs 0.13ms two-step) → rowid walk + PK fetch. Result: P95 → 11.7ms.
- 1M red (tier-1 123ms, RSS 977MB) → float16 matrix (256MB), bulk facet assignment,
  chunked centroid refresh (999-variable cap), streaming corpus + matrix warm.
  Result: P95 6.1ms, RSS 412MB.

Note: 100k/250k reports predate the final two-step-FTS commit by behavior-equivalent
edits only (identical row sets, chunked math); 1M ran on the final tree.
