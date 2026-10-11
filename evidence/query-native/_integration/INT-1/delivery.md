# INT-1 — Delivery (write-path integration)

Contract TL-QN-2026-09-12.1, targeted repair: wire verified R01/R02/R04/R05/R06
library components into the served write path. Base HEAD 0df044b + R01–R07.
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite + FTS5, no network.

## Owned files changed (only)

- `src/trace_lite/source/project.py` (new): projection pipeline orchestrator —
  segment (R02) → extract (R04) → reconcile (R05 corpus grouping) with a
  bounded synchronous outbox drain, per-revision coverage checklist
  `[segmented, extracted, reconciled, indexed]`, obsolete-worker guard.
- `src/trace_lite/source/service.py` (extended, additive): derived idempotency
  keys, `atom_id_for_revision`, `job_for_revision`, `facets_of_atom`.
  No existing behavior touched.
- `src/trace_lite/source/__init__.py` (additive export of `project`).
- `src/trace_lite/api/app.py` (write path only): `ingest_note`, `sync_notes`,
  `remove_note` now run publish → facet repair/map → drain → invalidate →
  generation publish; `freshness` adds coverage-based pending;
  ingest/sync/status responses gain additive fields; lifespan ensures
  grouping DDL + resume-drains pending jobs. Query/dossier routes and
  R06 lock/generation-swap logic untouched.
- `tests/test_write_path_integration.py` (new, durable): acceptance demo + 2 unit tests.
- This dir: `delivery.md`, `lifecycle_demo.log`, `lifecycle_demo.json`.

## Wiring summary (per write)

1. R01: every served write calls `source_service.publish`/`retract` — immutable
   revision, idempotency record (derived key `int-1:{source}:{payload-hash}`),
   outbox job — in its own atomic writer transaction. Same key+payload replays
   return the prior revision with no new job and no duplicated projections
   (existence checks at every stage).
2. Facets: exact repair (`reseed_accum` + `refresh_centroid`, the public
   out-of-band-change API) then legacy hint mapping — same memberships as before.
3. R02/R04/R05: bounded synchronous `drain_outbox` (limit 64) claims each job,
   verifies the revision is still current (obsolete → complete without
   projecting; retract → complete, caller invalidates), runs
   segment → extract → corpus-group, completes on full non-index coverage,
   fails (retryable + event) otherwise. Corpus = one pipeline-maintained
   `thematic_cluster` holon (cross-source grouping; no auto links — similarity
   never mints typed edges); first lone atom defers honestly until a 2nd atom
   arrives (backfilled on the next drain).
4. R06-F4: premise edits/deletes call `engine.invalidate_dependents` (uncalled
   before) with requires = superseded revision + removed atoms; corpus excluded
   (state refreshed synchronously). Dependent syntheses flip to `stale`.
5. Publication: one `filing` generation bump per write/batch after projections
   (served lexical publication; generation-swap reads unchanged). R03 ANN
   publication intentionally NOT run per write (full-corpus rebuild, unconfigured
   index dir, tiny-corpus risk) — stays INT-2 per path-matrix row 5.
6. Freshness: `pending = undrained jobs + in-scope revisions missing coverage
   without a pending job`. Scope = source-pointed revisions only (superseded
   history and unpointed rows never block; required by the R06 healed-drain test).
   Never vacuous: empty queue + uncovered revision still reports pending.

## Acceptance demo results (all OK, see log/json)

ingest(3 notes → 3 revs/views/runs, corpus 3 members, jobs drained, trusted) →
query (answerable, dossier cites premise.md) → synthesis setup (active) →
edit via /api/sync (history superseded+current, synthesis **stale** with no
manual engine call, corpus active holding the new atom, new text served) →
replay (no new revisions/views) → delete via watcher (lexical gone, history
retained, 2 atoms) → restart on same DB (query/dossier/spans/relations alive,
synthesis still stale, coverage complete).

## Verification

- New: `tests/test_write_path_integration.py` — 3 passed.
- Full: `.venv/bin/python -m pytest tests/ -q` — **240 passed**, pre-existing
  tests unmodified (includes R08 experimental file, untouched).
- Bugs found and fixed during wiring: (1) coverage `indexed` (= job done)
  checked before job completion deadlocked the drain — completion now gates on
  non-index stages; (2) poison-guard `seen` check ran after `claim()` re-took
  the lease and cleared the error — the re-claim is now restored to pending;
  (3) `holons`/`holon_members` come from `HOLON_SCHEMA`, not `schema.sql` —
  lifespan ensures the DDL before serving and coverage degrades honestly when
  absent (killed a `no such table` race under concurrent ingest+query).

## Remaining gaps for INT-2

- R03 ANN (`VectorIndex`/`build_and_publish`) still unwired (row 5); served
  retrieval remains the lexical/faceted cascade with generation pinning.
- Dossier citations are atom-anchored, not revision/span-provenance (row 6).
- Stale-synthesis repair (`repair_stale_holons` bounded jobs) not scheduled —
  exposed via `stale_holons` in freshness; no auto-repair runs.
- Crash between retract-commit and synchronous invalidation leaves dependents
  un-staled (ms window under the writer lock); context exists in events, no
  resume replay yet. Lifespan resume-drain covers pending *jobs*.
- Batch contract is per-source (each note atomic; a mid-batch failure leaves
  earlier notes committed) — explicit, differs from the old all-or-nothing batch.
- Revision payload is the raw note (frontmatter included); the lexical atom
  carries the same bytes (R01 atom==revision contract wins over legacy
  body-stripped atoms; query tests confirm no ranking/count impact).
