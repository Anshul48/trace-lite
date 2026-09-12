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

## Double Truncation & Adversarial Audit Calibration

1. **Tier 2 Facet Truncation & Balanced Sampling**:
   - *Original limitation*: Tier 2 used `SELECT atom_id FROM memberships WHERE facet_id = ? ORDER BY atom_id LIMIT 400`. In large facets (e.g. 100k+ members at 1M scale), notes with ID > 400 were permanently excluded from beam candidate selection.
   - *Calibration*: Implemented `strategy="balanced"` using bimodal head+tail sampling (`ORDER BY atom_id ASC LIMIT 200` UNION `ORDER BY atom_id DESC LIMIT 200`). This ensures both foundational notes and newly ingested notes (IDs > 2000 up to 1M) are represented in candidate scoring.

2. **Tier 3 Dense Matrix Truncation**:
   - *Latency profile*: Stride-samples 2,000 rows across the 1M matrix via `np.linspace(0, n-1, 2000)`, bounding compute to ~2-4ms while covering the entire corpus span.
   - *Quality profile*: Scans the full dense matrix (`scan_cap=None`) with BM25-led ranking, ensuring exact retrieval.

3. **Ghost Facet Saturation Remediation**:
   - Sifting in `FacetedBeam.candidate_ids` now filters out 0-member facets before taking `beam_width`, preventing deleted notes from saturating beam slots and starving lower-ranked legitimate facets. Stale centroid blobs are purged on note deletion.

4. **Corroboration Gate Term Isolation**:
   - Abstention corroboration checks term/Porter-stem overlap specifically against the returned candidate document's text rather than running database-wide FTS queries.

5. **Ingest Re-warm Optimization**:
   - `POST /api/ingest` utilizes incremental vector updates (`router.add_atom`) and debounced background re-warming (0.5s timer), eliminating the $O(N^2)$ lock freeze during bulk sequential syncs.
