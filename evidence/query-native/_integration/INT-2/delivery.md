# INT-2 — Delivery (served retrieval + dossier integrity)

Contract TL-QN-2026-09-12.1, targeted repair: wire VERIFIED components into
the served read path. Base HEAD 0df044b + R01–R07 + INT-1 (all VERIFIED, no
regressions). Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite + FTS5,
no network (learned-encoder weights unfetchable — seam only, see §5).

## Owned files changed (only)

- `src/trace_lite/router/cascade.py` (+ minimal adapter glue; R03 index
  semantics untouched): served R03 ANN sidecar + holon expansion + honest
  route labels on `QueryResult` (additive fields only).
- `src/trace_lite/api/app.py` (QUERY/DOSSIER routes only; no
  write-path/lock/freshness changes): generation-pinned ANN ensure in the
  query-serving helper, route labels on `/api/query`, server-side
  resolution + scoped relation re-retrieval on `/api/dossier`.
- `src/trace_lite/cordis/dossier.py` (conn support + visibility
  enforcement): `resolve_candidate`, revision-slice verification,
  degraded-`atom_self` gating, same-filter enforcement for
  relations/disagreements/virtual-holons/synthesis, partitioned-group
  omissions, conservative support.
- `tests/test_served_path_integration.py` (new, 11 tests): the §3 battery.
- This dir: `delivery.md`, `served_retrieval.json`, `citation_battery.log`.

## 1. Wiring summary

### (a) Served retrieval uses the intended branches (row 5)

- `CascadeRouter.refresh_served_vector(generation_id, encoder=None)` builds
  an in-memory R03 `VectorIndex`/IVF from the reader's own snapshot via the
  pinned `HashBowEncoder` (default), pinned to the serving generation. No
  R03 semantics changed (no edits to `index_adapter.py`/`encoder.py`).
- `route_pinned` calls `_ensure_served_vector(view)` per attempt (read-only):
  rebuild on generation swap, enable `holon_guided`. Empty/oversize
  (>20k atoms)/failed builds detach quietly → named lexical fallbacks.
- `route()`: hybrid mode tries the ANN branch after Tier 1 with the same
  theta + anchor-corroboration gates; filtered-coverage inadequacy falls
  through to beam/hybrid (ARCH §7 filtered-fallback). Served adequacy is
  narrower than the raw R03 flag: a short result with nothing actually
  filtered is genuine, not inadequate. Tree/flat profiles keep their pinned
  branch sets (existing tier assertions hold bit-for-bit).
- Holon-guided candidates (ARCH H→C): co-members of live
  `thematic_cluster` holons blend as `route="holon"` extras (cap 5, seeds 3,
  deterministic score blend, truncated to limit). Derived/stale syntheses
  and deleted holons never contribute. Off by default in the library;
  enabled on the served path only.
- Honest labels: every anchor carries `route`; `QueryResult` adds
  `route`/`routes`/`vector_adequate`/`vector_generation_id`/`note`
  (additive; `tier_used` kept as the legacy bucket — vector reports 2).
  `/api/query` surfaces all of them. Fallback stays fully available as the
  named baseline (unattached router serves lexical/beam/hybrid unchanged).

### (b) Dossier integrity (row 6)

- The endpoint MUST pass `conn` (`conn=view.db.conn`); the pure function
  stays usable without it. `resolve_candidate` binds each candidate to
  `(revision_id, span_id, byte range)` BEFORE filtering: span-anchored via
  `excerpt_span` (+ scope adopted from the span's revision), atom-bound via
  the served atom row (namespace/access/version from store truth,
  overriding caller claims; bounds from the atom row — this also fixes beam
  anchors, which carry no byte offsets). Outcomes: RESOLVED / DEGRADED
  (identity unprovable → `atom_self:degraded` fallback, surfaced) /
  INVALID (proven wrong → dropped as `citation_invalid`, never included).
- New `source_slice`-via-`revision_id` verify path (payload sliced from the
  named revision in the store); whole-atom candidates verify as
  `source_slice` (their text spans several R02 spans, so no single span is
  claimed). `EvidenceSelection` gains `citation_degraded` (default False).
- Bypass fix — two layers (chosen mechanism documented here):
  1. **Scoped re-retrieval** (endpoint): `_served_relations` loads relation
     rows for the resolved-visible revisions only (bounded 200); every
     served edge carries its `revision_id` plus preserved
     `condition_text`/polarity/anchors.
  2. **Same-filter enforcement** (compiler): edges touching a
     KNOWN-excluded (filtered-out) candidate/doc are dropped as named
     `relation_filtered`/`disagreement_filtered` omissions; revision-bound
     edges outside the resolved set are dropped as
     `relation_unresolved_revision`; candidate-embedded relations ride
     their (visible) host; virtual holons admit selections only;
     synthesis lineage referencing filtered ids is a named
     `synthesis_references_filtered` omission. Graph-native claim/assertion
     endpoints that name no known candidate are kept (exclusion unprovable
     — preserves the R07 qualifies-edge contract).
- Lexical-vote honesty: degraded selections never count as support;
  all-degraded relevance stays `insufficient`; atomic groups partitioned by
  filtering yield named `atomic_group_partitioned` omissions (visible
  members still selected, no completeness claim); conditions and
  counterevidence preserved verbatim; checker stays `lexical-heuristic-v1`.

## 2. Acceptance battery results (all OK, see log/json)

Served `/api/query` on 3 notes → `route=vector`, `vector_generation_id ==
generation_id`, adequate, premise anchored; gibberish abstains labeled;
unattached router serves named legacy fallback. Served dossiers: 3/3
resolved, `source_slice`, 6 re-retrieved relations, 1 derived virtual
holon. Unicode/CRLF/emoji byte-exact with `crlf` signal; blank-separated
repeats hold 4 distinct span IDs; tight-budget trims are anchored interior
windows (nonzero offsets, never prefixes). Replacement supersedes (new
revision cited, old text/revision uncited, all citations current; stale
versions filtered with counts). Watcher delete retracts (lexical gone,
history kept, dossier drops it). Proven-wrong atom/span texts dropped as
`citation_invalid`; unknown spans degraded+labeled+surfaced with
conservative support. Out-of-scope namespace/access/version candidates
withheld with their relations/disagreements named; historical retained
only with `current_only=False`. Partitioned atomic group named.
Negation/actor-reversal/quantity/conditional variants coexist with
explicit disagreements, preserved conditions, named checker. Quality probe
(6 docs via HTTP): relevant-first, pinned, all citations resolved,
relations revision-proven.

## 3. Verification

- New: `tests/test_served_path_integration.py` — 11 passed.
- Full: `.venv/bin/python -m pytest tests/ -q` — 251 passed (240
  pre-existing + 11 new), pre-existing tests unmodified. Includes R08
  experimental file untouched and the legacy router/store/obsidian/e2e
  files green.
- Bugs found and fixed during wiring: (1) strict both-endpoints relation
  filter broke R07's qualifies-edge contract → known-exclusion rule;
  (2) served-adequacy conflated genuine-short with filtered-short →
  narrower served rule; (3) unresolvable span failed closed as invalid →
  span claim dropped, degraded fallback applies; (4) battery fixtures that
  never reached citation checking (below relevance) → term-bearing fixtures.

## 4. Remaining gaps / next actions

- Served ANN is an in-memory per-reader build at generation-swap time
  (O(corpus) k-means per swap); corpora >20k atoms skip to named
  fallbacks. The file-publication path (`build_and_publish` + CURRENT)
  stays library-verified; pointing readers at published segments is future
  work (no write-path changes were permitted here, so per-write ANN
  publication was out of scope).
- Served historical mode: router atoms are current-only; historical
  dossier candidates are library-supplied (span-anchored) and proven in
  the battery, not discoverable through `/api/query`.
- `atom_id` reuse is impossible (AUTOINCREMENT), so post-replacement
  stale citations surface as degraded-unresolvable, not version-filtered,
  when old ids are replayed directly.
- Crash between retract-commit and synchronous invalidation (INT-1 known
  window) is unchanged by this packet.

## 5. INT-3 seam (no weights fetched)

`refresh_served_vector(..., encoder=None)` accepts any object with the
`HashBowEncoder` surface (`fit_idf`/`encode_weighted`/`config_hash`/spec
label). INT-3 plugs learned weights behind this seam; served receipts
record the encoder label + config hash per build. No network/model
downloads were performed here.
