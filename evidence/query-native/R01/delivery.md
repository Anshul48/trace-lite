# R01 — Delivery (B2)

Record type: builder delivery
Author role and date: R01 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R01-storage.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + pre-existing
coordinator prompt-text modifications only (`COORDINATOR_LAUNCH_PROMPT.md`,
`docs/builds/query-native/{COORDINATOR,STATE}.md` — not mine, untouched by this
packet); `src/`, `tests/`, `benchmarks/` otherwise as at base except owned files below
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5, WAL/NORMAL,
repo v9fs, scratch/evidence DBs under /tmp ext4

## Delivery section

- Behavior/artifact changed and why: atomic source replacement with retained
  revisions + durable projection outbox (C1-1..C1-4). New `source` service owns the
  single-writer replacement transaction; `store` gains migration-001 DDL, outbox
  leases, generation records, and the legacy migration tool.
- Files changed:
  - `src/trace_lite/store/schema.sql` (additive: `schema_version`, `source`,
    `source_revision`, `idempotency_keys`, `projection_job`, `projection_generation`,
    `legacy_atom_map`; no span/extraction/graph/holon tables)
  - `src/trace_lite/store/__init__.py` (additive exports only)
  - `src/trace_lite/store/outbox.py` (new), `src/trace_lite/store/generations.py`
    (new), `src/trace_lite/store/migrate.py` (new)
  - `src/trace_lite/source/{__init__.py,service.py}` (new package)
  - `src/trace_lite/store/database.py` UNCHANGED (compat shim needs no rewrite)
  - `tests/test_store_lifecycle.py`, `tests/test_migration.py` (new, durable
    regression tests); all pre-existing tests unmodified
  - `evidence/query-native/R01/{readiness.md,delivery.md,crash_test.log,
    idempotency.json,outbox_recovery.log,migration_parity.json}` (this packet)
- Material implementation decisions:
  - Canonical retained payload is `source_revision.text` inline (byte-exact); fresh
    rows use coordinate space `raw_utf8_bytes`, migrated rows `legacy_atom_text`.
  - One writer txn per replacement: revision + current pointer + lexical delta (one
    `atom`/`fts_atoms` row per current revision; superseded rows removed in-txn) +
    compact event + outbox job; governor checkpoint stays post-commit.
  - Crash seam is a closed-over `_pre_commit_fault` hook param (not a global).
  - `legacy_atom_map.atom_id` is deliberately NOT a FOREIGN KEY: the map records
    where each legacy atom went and must survive the atom row's later removal by
    the replacement lexical delta (`revision_id` side stays enforced).
  - Outbox lease floor 30 s enforced in `claim()`; expiry reclaim + restart resume
    from persisted rows; completion idempotent; worker work verified outside the
    writer txn (concurrent publish on a second connection succeeds mid-claim).
  - Migration: `VACUUM INTO '<db>.bak-001'` snapshot before any write; single-txn
    backfill; one legacy revision per `doc_id` (`"\n"`-join + char offsets for
    multi-atom docs); rerun-safe via `schema_version = 1`.
- Known limitations and checks not run: no external payload files (inline text is
  the retained payload); no dense embeddings/graph extraction (R03/R04 non-goals);
  no benchmark-scale loads (bounded fixtures only); `atom`-level legacy writers
  still function (compat shim per packet instruction, overriding ARCH §9's
  reject-old-writers for that surface); durability FULL-vs-NORMAL comparison is R09.

## Evidence

| Criterion | Command or evaluation method | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C1-1 Atomic replacement | `.venv/bin/python /tmp/gen_r01_evidence.py` (exit 0) + `test_crash_*` (3 tests) | fault pre-commit keeps old revision current, counts unchanged, MATCH parity vs clean rebuild, integrity ok | `evidence/query-native/R01/crash_test.log` | injected fault, not power loss |
| C1-2 Idempotent submission | same script (exit 0) + `test_idempotent_*` (2 tests) | 3 replays return identical revision id, table counts frozen; different payload raises conflict naming `ev-key-1` | `evidence/query-native/R01/idempotency.json` | single-writer scope |
| C1-3 Async outbox durability | same script (exit 0) + `test_outbox_*`, `test_worker_*` (3 tests) | lease floor enforced, wrong-owner completion rejected, expired lease reclaimed, job visible + completable after close/reopen, manifest active | `evidence/query-native/R01/outbox_recovery.log` | lease expiry simulated via SQL (no 30 s wall wait) |
| C1-4 Migration integrity | same script (exit 0) + `tests/test_migration.py` (6 tests) | 29 atoms -> 28 revisions, fidelity 1.0, independent byte audit ok, backup snapshot valid, rerun safe | `evidence/query-native/R01/migration_parity.json` | bounded fixture (28 docs) |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 73 passed (53 pre-existing unmodified + 20 new) in ~18 s | session log | pre-existing warning only |
| Gates 1,2,3,5,7,10 | `test_byte_exact_*` (unicode/emoji/CRLF/frontmatter), `test_idempotent_*`, `test_crash_*`, `test_obsolete_*` + `test_migrated_ids_*`, `test_rebuild_*`, `test_restart_*`, plus `test_namespace_*` (gate 8) and reader-separation test | all pass; `verify_store` checks hashes + FTS integrity-check + MATCH probes + manifests | new test files | bounded fixtures, not scale loads |

## Independent findings

(none — builder role; verifier uses review.md)
