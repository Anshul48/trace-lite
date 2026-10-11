# Lane D — independent review + targeted research (2026-09-12)

Read-only wrt Lane A/B/C implementation and harness files. No commit/push.
No lane patches existed to review (`evidence/overnight/` held only the
reconcile note at inspection time).

## 1. Reconcile vs prior result 1 (reinspected this session)

- HEAD `0df044b` on main, tree dirty — CONFIRMED (`git log`, `git status`).
- `.venv` Python **3.11.15**, pytest only via `.venv` — CONFIRMED (ran it).
- Drain call sites are `source.project`, not `router/projection` — CONFIRMED:
  `app.py` calls `projection.drain_outbox` at :1124/:1135/:1162/:1220/:1464;
  `workers.py` imports `trace_lite.source.project`; `router/projection.py`
  has no `drain_outbox` (it hosts `iter_atom_batches`, used by S4 seed).
- `_warm_reader` → `hybrid.warm()`, read-only, both modes — CONFIRMED
  (`app.py:743-775`).
- Honest-detach on maintenance/lock-contention/unservable base — CONFIRMED
  (`app.py:887-933`); docstring matches runtime.
- Praxist mandate is draft, post-S4 commit+push gate unmet — CONFIRMED
  (mandate header + dirty tree, no push).
- Taken as pointers (NOT reinspected): envelope `run_until_drained` per
  rung, serve-under-drain sequential loop, probe ok-predicate, O(N^1.85)
  two-point fit, 315-passed S6 suite claim.
- Acceptance contract (bulk vs online split, count nouns, pin rate, R09
  NEEDS_REPAIR, 10k refusal): endorsed as-is. One addition proposed in §4.

## 2. Paid-research budget verification (prerequisite failed → deferred)

- `praxist` CLI exists; it has NO `budget` subcommand (verified `--help`).
- No `~/.praxist` config, no provider/model/API-key in env, no DeepSeek CLI.
- Model identity, pricing, metering all unverifiable → per lane orders,
  NO paid calls were made. Research below used free web sources only.

## 3. Design question: safe snapshot publication/reclamation under SQLite WAL

### Inspected local facts

- Publication order (R03/S4): segments → manifest → CURRENT (`os.replace`)
  → generation row (`index_adapter.py:338-362`). Crash-safe for readers
  that open post-flip; `*.tmp` ignored by opener.
- `VectorIndex.collect_orphans` (`index_adapter.py:426-450`) deletes every
  unreferenced manifest/segment/tombstone/`*.tmp` with NO reader refcount,
  NO grace period, NO coordination with concurrent `publish()`. A publisher
  between `save()` and CURRENT-flip has unreferenced-but-live segments a
  concurrent collector would delete. A reader between CURRENT-read and
  segment-load can hit deleted files (`open()` has no retry).
- `collect_orphans` currently has NO production callers (only
  `test_index_adapter.py`, `test_s4_vectors.py`). Hazard is latent, not
  live — but old generations then accumulate unbounded on disk, and the
  disk-budget probe measures what nothing reclaims.
- `projection_generation` rows are never deleted (`generations.py` has no
  delete path — full read). Every worker pass with completions
  (`workers.py:228-256`) and every writer txn (`app.py:706-727`) adds a
  row: unbounded table growth at sustained write rates.
- WAL hygiene exists: `WalGovernor` issues `wal_checkpoint(PASSIVE)` every
  5000 committed docs, wired into `source/service.py` and `database.py`
  post-commit paths. No `isolation_level` override anywhere → default
  sqlite3 module behavior (SELECTs autocommit; short snapshots), so
  long-lived reader pinning is unlikely by construction — but no test
  asserts it.

### External findings (primary sources)

- SQLite WAL: each read txn pins an end-mark snapshot; checkpoint "must
  stop when it reaches a page in the WAL that is past the end mark of any
  current reader"; "a long-running read transaction can prevent a
  checkpointer from making progress"; PASSIVE checkpoints may not run to
  completion under concurrent readers; only FULL/RESTART try harder and
  need `sqlite3_wal_checkpoint_v2` ([WAL doc](https://www.sqlite.org/wal.html),
  §§2.1–2.3, 3.2; fetched and grep-verified this session).
- RCU/epoch reclamation: split update into removal (unlink/CURRENT flip)
  and deferred reclamation; reclaim only after a grace period in which all
  pre-removal readers quiesced ([whatisRCU](https://github.com/torvalds/linux/blob/master/Documentation/RCU/whatisRCU.rst),
  [RCU Wikipedia](https://en.wikipedia.org/wiki/Read-copy-update)).

### Applicable conclusions

1. Keep the CURRENT-flip protocol; add the missing reclamation half: retain
   N−1 prior generations (Lucene-style deletion policy) and delete only
   after a grace period tied to reader quiesce. The existing
   `reader_registry` + per-view `generation_id` already supply the pins a
   collector needs (oldest pinned generation = retention floor).
2. Never run collection concurrently with publication: gate both behind the
   single maintenance owner (the served-runtime lock already serializes
   seed/catch-up vs attach; extend the same discipline to GC).
3. Same rule for `projection_generation` rows: GC superseded rows older
   than the oldest pinned generation; keep active + floor.
4. WAL: current design (short autocommit reads + post-commit PASSIVE
   checkpoints) is sound; add a test asserting no long read txn pins the
   WAL (hold explicit read txn → sustained writes → WAL grows and PASSIVE
   reports `checkpointed < log`; end txn → checkpoint completes), plus a
   WAL-bytes monitor on the sustained-write rung.

### Uncertainties

- Whether the S6 sustained-write rung measures WAL bytes at all (harness
  not reinspected — Lane C owns it).
- Windows file-deletion semantics for the collector (delete of open files
  fails) — untested; native Windows run still not executed.

### Falsification test (proposed, not run — Lane C owns harnesses)

1. Reader R1 opens generation G (`VectorIndex.open`); publish G+1; run
   `collect_orphans`; assert a fresh open succeeds AND R1's in-memory view
   still serves its pinned snapshot.
2. Loop `publish()` in one thread while `collect_orphans()` loops in
   another; assert every publish receipt hash-verifies (no live segment
   ever deleted). Current code is EXPECTED TO FAIL test 2 — that failure
   is the bug demonstration.

## 4. Praxist target status (independent assessment)

- Target A (non-blocking queue + lock-free generation swap): IMPLEMENTED
  as mechanism (supervised workers + leases in `filing/workers.py`;
  `guarded_activate`; thread-local reader + swap-retry in `route_pinned`),
  but the non-blocking claim is EXPERIMENTAL — the only overlap probe is
  the sequential harness flagged in reconcile. Not qualified.
- Target B (syntactic extractor replacing regexes): PARTIALLY IMPLEMENTED
  at best. S5 built a cheap-STRUCTURE extractor (`structure.py`, explicit
  exclusion of verb patterns) plus a rules-based interpret arm — not the
  mandate's "high-yield syntactic parsing". Yield-vs-regex-baseline at
  fixed precision is unmeasured (frozen contract §4). Experimental.
- S4-tuning non-goal: RESPECTED (`S4_DELTA_BOUND` is a documented
  constant; retry/backoff defaults labeled untuned).
- Formal `praxist-task-init`: still GATED (draft mandate, dirty tree, no
  push). No init invoked.

## 5. Proposed contract addition

Reclamation ownership: publication (Lane B files) and collection (currently
uncalled) must be co-owned with the maintenance lock as the serialization
point; no `collect_orphans` call sites until the grace-period rule (§3.1)
is implemented and the falsification test (§3) passes.
