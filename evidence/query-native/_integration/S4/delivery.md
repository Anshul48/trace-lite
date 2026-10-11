# S4 persistent vector serving — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + STEERING S4 row + line-85 methodology.
Interpreter: `.venv/bin/python`.

## Problem (measured)

`CascadeRouter.refresh_served_vector` (called from `api/app.py::_ensure_served_vector`
on every serving-generation change) loads and re-encodes the whole corpus on the
query path, and refuses to build above 20,000 atoms (`SERVED_VECTOR_MAX_ATOMS`).
Steady state is therefore "edit anything, re-encode everything, on first query" —
the exact cost the steering packet removes. A 50k-note hybrid claim needs this
behavior change, not a faster benchmark.

## Design (routine details resolved autonomously)

- `src/trace_lite/router/vector_store.py` (new, owned): `ServedVectors` serves
  **published base + bounded delta/tombstones** over a shared R03 index directory.
  - `EmbeddingCache`: content-keyed RAW vectors `(sha256(text), config_hash)`.
    Distinct source occurrences keep per-atom entries sharing one cached vector.
    `encodes`/`hits` counters are the update-cost meter. Persisted via atomic
    `s4-cache.json`; config mismatch refuses to load (never mixes epochs).
  - Frozen IDF epoch per generation (steering line 65): the sidecar
    `s4-delta-<gen>.json` records `(config_hash, idf, doc_count)`; the delta
    overlay inherits it. Compaction merges under the SAME epoch — never a silent
    IDF change. A new epoch needs one explicit background rebuild.
  - `open()` only opens (CURRENT + hash-verified base + sidecar). No published
    base => honest unservable note, zero encodes, zero files — the named lexical
    fallbacks serve. `ensure_served_persistent(router, served, gen)` is the
    drop-in replacement for the query-triggered full build (attaches the merged
    in-memory view to the existing ANN branch; INT-2 filtering semantics reused
    unchanged, not reimplemented).
  - `seed_from_db()` is the one explicit background build (transitional repair):
    distinct contents encode once, epoch freezes, R03 save→manifest→CURRENT→row
    publishes. Steady state after that: `ingest_atom` / `delete_atom` touch only
    the bounded overlay + sidecar (atomic tmp+replace).
  - `publish()` compacts base+overlay into a new generation (zero text encodes:
    vectors come from the merged matrix). `recover()` heals post-flip crashes
    idempotently (sidecar rewrite + row reconcile reusing the existing row for
    the manifest hash — no row growth). `prune_generations(keep=1)` deletes
    superseded generations; `disk_usage(db_path)` reports per-generation bytes,
    DB/WAL bytes, and host headroom in one record.
  - Generation vs publication are separate: an opened reader handle is immutable
    and keeps serving across later publishes; only a fresh open sees the new
    CURRENT (tested).
- `src/trace_lite/router/index_adapter.py` (hardening only): `_fsync_dir` is now
  best-effort (Windows cannot fsync a directory handle; tmp+`os.replace`
  atomicity holds on both platforms). No behavior change on Linux.
- `src/trace_lite/router/__init__.py` (exports only): +`ServedVectors`,
  +`EmbeddingCache`, +`S4_DELTA_BOUND`, +`ensure_served_persistent`.
- No store migration was needed (cache/sidecars live beside the index; the
  R03 manifest layout is untouched and its orphan scans never match `s4-*`).
  `api/app.py`, `source/*`, `store/*`, `filing/*` untouched — S3 worker
  semantics (leases, generation guards) unchanged by construction.
- S4 tuning explicitly out of scope: `S4_DELTA_BOUND = 256` is a plain constant,
  not grid-searched; batch sizes/drain limits left at shared defaults.

## Comparison (line 85; `s4_vectors.json`, 240 atoms / 12 queries / k=10)

| arm | filtered recall@10 | cold open | cold query | warm p50 | disk |
|---|---|---|---|---|---|
| A persisted adapter (R03 IVF) | 1.0 | 0.0017 s | 0.0003 s | 0.00013 s | 135,608 B |
| B exact baseline (brute force) | reference | — (0.0013 s total) | — | — | — |
| C persistent ANN candidate (S4) | 0.9917 | 0.0029 s | 0.0005 s | 0.00015 s | 143,377 B |

Update cost per one-source edit: legacy full re-encode 240 encodes / 0.021 s
(same fit+encode operations as `refresh_served_vector`) vs candidate **1 encode**.
RAM serving matrix: 122,880 B. Disk headroom at two co-resident generations:
host free > 100× footprint (asserted in-test). Windows: stdlib+numpy only, zero
POSIX-only imports (asserted by scan), atomic `os.replace` on both paths,
dir-fsync best-effort; no native Windows run on this host (Linux/WSL — stated,
not claimed). Recovery: all five boundaries proven in-test (see below).
**Choice: C**, for the reasons recorded in `s4_vectors.json` (recall bar met,
O(1) updates, no-build first query, coherent crash recovery, measured headroom).

## Before / after

| behavior | before | after (S4 layer) |
|---|---|---|
| one-source edit | re-encodes N atoms on next query | 1 encode (replay 0, delete 0, dup-content 0) |
| first query, nothing published | builds whole corpus inline (or refuses >20k) | opens only: 0 encodes, 0 files, honest detach |
| generation staging | build-then-swap inside the reader | readers hold old base; CURRENT flip is the only publication step |
| crash mid-publish | R03 file boundaries only | + delta-tmp ignored, CURRENT-without-sidecar adopts epoch, row reconciled idempotently |
| old generations on disk | accumulate, no accounting | per-generation bytes + WAL + headroom in one record; `prune_generations` reclaims |

## Verification

- `tests/test_s4_vectors.py` (7): incremental-edit encode counts (1/0/0/0 +
  shared-vector dup + top-1 agreement exact/ANN), no-build-on-first-query
  (0 encodes + 0 files before AND after seed, fallback route serves),
  crash at all five publication boundaries (old-gen serves pre-flip; row
  repaired idempotently post-flip; torn-tmp ignored; merged content served
  post-sidecar-crash and healed), bounded delta → compaction with 0 re-encodes
  and recall ≥ 0.95 pre/post, generation separation (old handle byte-identical
  ranks after publish), disk co-residence with hash verification + prune,
  full A/B/C comparison writing `s4_vectors.json`.
- `run.log`: 7 passed. Full suite: **288 passed, 6 skipped** (281 + 7 new;
  same 5 S1 native-run + 1 pre-existing skips), zero regressions.

## Owned files touched

`router/vector_store.py` (new), `router/index_adapter.py` (`_fsync_dir`
tolerance only), `router/__init__.py` (exports only),
`tests/test_s4_vectors.py` (new), `evidence/…/S4/**`. Nothing else.

## Gates at risk

None. No gate weakened: R00–R08/INT-1/2/S1/S2/S3 suites run unmodified and
green; the legacy query-triggered build is preserved byte-for-byte as the
transitional path. Follow-ups owned elsewhere: cut `api/app.py` query path
from `refresh_served_vector` to `ensure_served_persistent` + background
`seed_from_db` scheduling (app wiring is outside S4 ownership); bound/batch
tuning (explicitly out of scope); native Windows run; 50k operating-point
curves (S6).
