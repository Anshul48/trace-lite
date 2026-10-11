# Lane B (persistent query serving) — report 2026-09-12

Worktree: `.muse/worktrees/subagent-v2-01a09709-...-4321-71b0-a560-1cceee554c72`
Run dir (this dir): `evidence/overnight/lane-b-4321-20260912/`
Base: HEAD 0df044b + main-tree sync; owned base checksums in
`owned_base_checksums.txt` (verified identical in live main at handoff).
Interpreter: main `.venv` Python 3.11.15, pytest 9.1.1, `PYTHONPATH=<worktree>/src`
(shadows the editable install that points at main `src/`).

## Defects (measured at N=2000, `audit_measurements.json` pre / `post/` post)

D1. Generation refresh re-encodes the whole corpus. `FlatHybrid.warm()`
(hybrid.py) runs per reader init/refresh via app `_warm_reader` ←
`get_reader` ← `route_pinned`. After 1 insert: 2001 `text_vector` calls,
0.079 s. Persistent request path, every generation move.
D2. Per-query full id scan. `CascadeRouter._vector_search` runs
`SELECT id FROM atom` on EVERY query (0.023 s at N=2002) for the allow-list.
D3. Per-attach full merge + k-means retrain under the snapshot lock.
`ensure_served_persistent` → `ServedVectors.materialize()` rebuilds every
call (0.034–0.041 s at N=2001; `_merged` 0.003 s + k-means 0.018 s) while
app holds `rt.lock` (app.py:918-933) — lock hold scales with N.
D4. Old base labeled as newer generation. `ensure_served_persistent(router,
snap, filing_gen)` pins with the caller id without comparing to
`snap.generation_id` (M5 probe: snap gen-A served as gen-FILING-NEWER).
Window: write committed (filing G+1) before catch-up converges.
D5. No snapshot identity/version/disposal API (`ServedVectors`): every
`materialize()` a fresh unversioned copy; staleness visible only via
filing-gen mismatch at next attach.

## Changes (owned files only; 3 files, +425/-? lines in `*.patch`)

R1 `router/hybrid.py` — delta-aware `warm(full=False)`: fingerprint scan
detects new/changed/deleted rows (same-id text replacement included);
re-encodes only the delta under the frozen IDF epoch (add_atom semantics);
`full=True` forces exact rebuild+refit; `last_warm` reports
{full, encodes, reused, removed, live}; returns live count.
R2 `router/cascade.py` — pin-keyed live-id cache in `_vector_search`
(one fetch per pin; cleared on attach/detach); additive
`_vector_base_generation` introspection (set by refresh=pin, ensure=base).
R3 `router/vector_store.py` — content-versioned materialize cache:
first call per version merges+trains, repeats return a fresh shell sharing
immutable template storage; `_invalidate()` on every base/overlay mutation
(incl. untombstone + upsert-drop corners); additive `content_version`
property, `release()`, `describe()`; `ensure_served_persistent` attached
return gains `base_generation_id`/`delta_size`/`materialized_cached`
(detached shape byte-identical — frozen, exact-match tested).
Untouched owned files: `index_adapter.py`, `learned.py`, `encoder.py`
(audited; defects live in callers). No edits outside owned files.

## Tests / measurements

- R1 probe (`/tmp/lane_b_r1.py`): 1 insert→1 encode; same-id update→1 encode
  + serves new terms; delete→0 encodes; idle→0; route-level EXACT agreement
  (verdict/route/anchors) vs full warm over 4-query battery; min same-id
  cosine 0.999999.
- R2/R3 probe (`/tmp/lane_b_r23.py`): 1 id-scan SQL per pin, 0 on repeat,
  refetch+retraction-visible on re-pin; materialize 4.4 ms cold →
  0.003 ms cached; invalidation→single rebuild; shell isolation (publish/
  delete on one shell invisible to siblings/template); release() keeps
  shells valid; ensure detached shape exact, attached keys additive.
- Post-repair audit: M1 refresh 2001→1 encodes (0.079→0.008 s); M3 repeat
  attach 0.0405→0.0000 s; full build unchanged (one-time O(N) kept).
- Full suite: **315 passed, 6 skipped, 1 failed in 103 s** — the failure is
  `test_cutover_serves_persistent_after_background_seed` (seed atoms 2≠3),
  confirmed pre-existing on untouched main base (3/3 + 1/1 main runs) and
  out of Lane B scope (app.py seed timing + Lane C test expectation).

## Limitations + falsifying evidence (no self-certification)

- L1. First reader build is still O(N) encodes (no persistent FlatHybrid
  snapshot exists). Falsify: `last_warm["full"] is True` + encodes==N on
  first warm per process/thread (by design; only repeats are bounded).
- L2. Delta path freezes IDF; near-tie dense order can differ from a full
  refit (same class as pre-existing add_atom). Falsify: route-level
  disagreement vs `warm(full=True)` on any query battery (none found; R1
  probe asserts exact agreement).
- L3. D4's pin label is UNCHANGED (frozen contract: pin==filing id; tests
  require equality at convergence). The skew is now OBSERVABLE
  (`_vector_base_generation`, `describe()`, additive ensure keys) but HTTP
  surfacing needs coordinator app.py wiring. Falsify: `vector_generation_id
  != _vector_base_generation` after a post-seed edit without compaction =
  honest skew signal working as designed.
- L4. Detach-during-maintenance is app.py behavior (frozen + tested); Lane B
  reduced contention (brief lock holds) but did not change availability
  policy. Falsify: pin-rate drop under write load = policy, not vector bug;
  check `served_adequate` + route labels per sample.
- L5. Uniform-text corpora collapse IVF k-means (15/17 empty postings,
  scanned 0) identically on base and repaired code — pre-existing
  index_adapter behavior, untouched (recall contracts at risk if retuned).
- L6. No committed tests added: `tests/` is outside Lane B ownership;
  verification is probes (in /tmp, preserved for re-run) + the repo suite.
- Live-main note: Lane C edited `benchmarks/` during this lane; owned files
  verified untouched (checksums match) — patches apply cleanly.
