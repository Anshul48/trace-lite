# P03 Delivery — Hearst Multi-Parent Faceted Classification Engine

- Packet: `docs/builds/filing-cabinet/packets/P03-hearst-faceted-classification-engine.md`
- Candidate commit: (this commit — Wave 3)

## Files
- `src/trace_lite/filing/taxonomy.py` — `Taxonomy` over `facets`: `create_facet(dimension,
  name, parent_id)`, deterministic `/`-joined paths, `subtree`/`subtree_ids`/`children`,
  `move_facet` with `CircularFacetError` on self/descendant reparenting, `UnknownFacetError`.
- `src/trace_lite/filing/engine.py` — `FilingEngine`: `assign_facets` (validated upsert into
  `memberships`), `query_facets(facets, match_all)` with per-facet subtree expansion
  (AND/OR), `facets_of_atom`, hashed-BoW `text_vector`/`cosine`, `refresh_centroid` /
  `facet_centroid` / `warm_centroids` (MED-01 in-memory centroid cache + `centroid_blob`).
- `src/trace_lite/filing/holon.py` — `HolonStore` (`holons`/`holon_members` tables):
  `create_holon(atom_ids, tier)` enforces ≥2 atoms, single-doc, per-document-order
  contiguity; `get_holon`, `holons_of_doc`.
- `tests/test_filing.py` — 4 durable tests (C01/C02/C03 + cycle rejection).

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/test_filing.py -q` → **4 passed**.
- 1 repair used (test-side: missing-facet probe needed a real atom first; product untouched).
