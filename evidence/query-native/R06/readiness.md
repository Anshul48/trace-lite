# R06 — Readiness (B1)

Record type: builder readiness
Author role and date: R06 builder / 2026-09-12
Packet and contract revision: R06-maintenance.md / TL-QN-2026-09-12.1
Base: git HEAD `0df044b` + R01–R05 working-tree sets (uncommitted, no other
writer active — coordinator-confirmed single-writer dispatch).
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5,
WAL/NORMAL, scratch/evidence DBs under /tmp (ext4).

## B1 checks

- Base suite: `.venv/bin/python -m pytest tests/ -q` → **182 passed**
  (verified this session before any R06 edit; 1 pre-existing Starlette
  warning only).
- R05 prereq: STATE.md marks R05 INTEGRATED (182/182 B5 recheck green,
  B3 PASS). Frozen holon contract (delivery.md §"Frozen holon contract")
  reviewed: `holon_state` (S_h blob, W_h, count, reps ≤3, digest, status),
  `holon_version` append-only, containment DAG (depth ≤ 8, budget-guarded),
  `dependency` reverse index, empty/deleted holons clear descriptors.
  R06 touches none of those files and alters none of those columns.
- SQLite build 3.53.1 satisfies ARCH §8 (documented WAL-reset fix ≥ 3.51.3),
  so concurrent WAL checkpoints from reader connections are supported.
- Owned files confirmed writable; prohibited set (graph/, router/,
  source/, filing/holon.py, filing/reconcile.py, cordis/, obsidian/,
  pre-existing tests, benchmarks/, R00–R05 evidence, docs/, schema.sql,
  migrate.py) will not be modified. No schema migration is needed:
  accumulators persist via existing `facets.centroid_blob` (+ membership
  counts); freshness/invalidation use existing `projection_generation` /
  `projection_job` / `dependency` / `holon_state` rows.
- Status-key compat: `tests/test_obsidian_sync.py` requires the keys
  `total_atoms`, `total_trees`, `index_trusted`, `needs_organization`,
  `pending_atoms` on `/api/status`. R06 keeps every key and computes
  (rather than hardcodes) their values, plus additive freshness fields.

## Decision: R05 `invalidated` vs R06-packet `stale`

R05 `_invalidate_dependents` writes `holon_state.status = 'invalidated'`
and `revalidate()` only transitions `invalidated → active`. The R06 packet
(C6-4) mandates `status = 'stale'`. Both values are hidden from every
R05 active-only reader (`holons_of_doc`, `holons_of_atom`,
`holons_of_kind` all filter `status = 'active'`), and there is no CHECK
constraint on `holon_state.status`, so writing `'stale'` from the new
engine-level invalidator is storage-compatible. Consequence, accepted:
R05 `revalidate()` is a no-op on `'stale'` rows — R06 owns the
`stale → active` transition in its bounded repair worker (new code in
the owned `engine.py`, mirroring the R05 snapshot shape). No R05 file
is touched to reconcile this.

## Lock-removal safety analysis (guard-mutex removal)

Current state (`src/trace_lite/api/app.py`): one `guard =
threading.Lock()` serializes every handler around a single shared
`sqlite3.Connection` (`check_same_thread=False`), the shared
`FilingEngine._centroids` dict, the shared warmed `CascadeRouter`
views, and the rewarm timer.

Shared-state inventory and new protection:

| # | Shared state | Current guard coverage | R06 protection (no global guard) |
|---|---|---|---|
| 1 | Writer `Database.conn` (single sqlite3 connection; connections are not safe for concurrent use even with `check_same_thread=False`) | guard | Dedicated `write_lock` serializes ALL writer-conn use (ingest/sync/watcher/teardown). Readers never touch it. |
| 2 | Concurrent SQLite writes (WAL = one writer + many readers) | guard (implicitly) | Same `write_lock`: at most one writer transaction at a time; `busy_timeout=5000` (already in `Database`) absorbs transient reader-cleanup contention. |
| 3 | Reader connections | n/a (shared the writer conn) | One `Database` per reader thread (thread-local, `check_same_thread=True` default): R01 "one-instance-per-thread" rule. WAL gives each a stable snapshot; registered for lifespan close. |
| 4 | Router warmed views (`hybrid._matrix/_pos/_idf`, beam shares) mutated by `warm()/add_atom()/remove_atom()` | guard | Per-thread `CascadeRouter` inside the thread-local reader stack; warmed lazily and re-warmed only on that thread when the pinned generation changes (generation-swapped reads). No cross-thread view sharing. |
| 5 | `FilingEngine._centroids` RAM (mutated by refresh/clear + beam stale-eviction) | guard | Per-thread engine inside the reader stack; writer-side engine used only under `write_lock`. |
| 6 | Beam stale-eviction writes (`UPDATE facets ... centroid_blob = NULL` inside `candidate_ids`) on a reader path | guard | Each reader owns its connection, so no cross-thread connection sharing. Writer runs `clear_stale_centroids()` after every sync under `write_lock`, so readers should never hit the stale path (ghost probe proves 0 ghosts); residual concurrent UPDATE-vs-UPDATE waits on `busy_timeout`, never deadlocks (no lock ordering: single statement, no nested acquisition). |
| 7 | Rewarm timer (`threading.Timer` touching shared router) | guard | Removed. Readers self-refresh via generation pin; no background mutation of shared state remains. |
| 8 | Generation publication (readers must not see half-published index state) | guard (implicitly) | Writers publish via existing `create_generation` + `activate_generation` (kind `filing`) only after commit; readers compare pinned generation id vs active and rebuild. Late-reader = explicit `semantic_pending` metadata, never stale-as-complete. |
| 9 | Lifespan teardown vs in-flight requests | guard | Teardown takes `write_lock`, stops watcher, closes writer DB + all registered reader DBs. In-flight reader threads hold their own conns; worst case a query during shutdown raises a handled 500, never corrupts state. |

Residual risks (accepted, bounded fixtures): (a) reader-reader `UPDATE
facets` contention on the stale-eviction path — pre-empted by
writer-side clearing, covered by `busy_timeout`, escalated per packet
on any `SQLITE_BUSY` error; (b) per-thread re-warm cost after each
publication — bounded fixtures only (no benchmark scale); (c) leased
reader conns held across TestClient threadpool reuse — thread-local
reuse is safe (one request per thread at a time).

## Rollback

Code-only revert: `git checkout -- src/trace_lite/filing/engine.py
src/trace_lite/api/app.py` plus deletion of the two new test files.
No DDL change is made anywhere (no migration, no `CREATE TABLE`, no
column change), so there is no data migration to roll back.
`projection_generation` rows of kind `filing` written by R06 code are
inert to pre-R06 code (unknown kinds are simply never queried), and
`holon_state.status = 'stale'` rows read as non-active under both old
and new readers. Evidence under `evidence/query-native/R06/` is
additive.

## Verdict: NOT BLOCKED → proceed to B2

No deadlock/busy evidence at base, parity oracle (`refresh_centroid`)
exists for the accumulator work, ghost-clearing primitives exist to
wire consistently, and the generation/outbox helpers exist for
publication. Escalation triggers armed: deadlock/busy errors, parity
drift > 1e-5, any existing-test regression.
