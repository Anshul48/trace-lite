# Critic-01 Review — Remediation of Audit FAIL (commit fc39aea)

- Reviewer: independent subagent (adversarial brief), observed suite 32/32 green.
- Verdicts: F1 FIXED, F2 FIXED w/ caveats, F3 PARTIAL, F4 FIXED w/ gap, F5 PARTIAL, F6 FIXED.

## Accepted and fixed in this loop (commit below)

| Critic item | Fix |
|---|---|
| Ghost facet (0 members + stale blob) re-arms F3 starvation via `zip` truncation | `beam.candidate_ids` excludes empty shortlists; quota round-robin (`CAP // nfacets`) + remainder top-up; `test_beam_ghost_facet_cannot_starve_pool` |
| `mode` echoed-never-honored | `CascadeRouter.route(..., mode)`: tree→tiers 1+2, flat→tiers 1+3, unknown→hybrid; API passes `mode` through; `test_route_mode_dispatch` |
| Ingest warms hybrid only (beam centroids stale) | `/api/ingest` calls full `router.warm()`; response gains `doc_id` |
| Watcher deletions never propagate (ghost notes arm F3 trap) | `DebouncedWatcher(..., on_remove)`, `Database.delete_doc`, lifespan removal wiring + `note.removed` events; lifespan + unit tests |
| Fresh-install `tl ingest` crashes (no parent mkdir) | `Database.__init__` creates parent dirs; `test_ingest_creates_missing_db_dir` |
| Non-UTF8 ingest unhandled | CLI exits 1 with message; test |
| Duplicate asserts (app.py) | Single inner asserts in watcher closures |
| Beam recomputes `qvec` | `candidate_ids(..., qvec=None)` passthrough |

## Considered and declined

- Original F3 probe metric (top-10 ranking over near-identical docs): ranking ties by id is
  correct behavior; the audited defect was candidate sourcing, now pinned by pool tests.
- `tl status` checkpoint side effect: status is read-only (pages/journal/size, no checkpoint).
- Ruff gate: ruff not installed in venv and no network — noted, not run.
