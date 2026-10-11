# R01 — Readiness (B1)

Role: R01 builder (B1 readiness + B2 build)
Packet and contract revision: R01-storage.md / TL-QN-2026-09-12.1
Date (UTC): 2026-09-12
Base commit: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` (verified via `git rev-parse HEAD`)

## 1. Ownership and isolation

Owned (create/modify only these):

- `src/trace_lite/store/**` (`database.py` additive only, `schema.sql` additive only,
  new modules `outbox.py`, `generations.py`, `migrate.py`)
- `src/trace_lite/source/**` (new package: `service.py` + `__init__.py`)
- `tests/test_store_lifecycle.py`, `tests/test_migration.py` (new files)
- `evidence/query-native/R01/**`

Prohibited and untouched: `src/trace_lite/api/`, `router/`, `filing/`, `cordis/`,
`obsidian/`, `benchmarks/`, `evidence/query-native/R00/`, `docs/`, all existing tests.
Shared checkout; owned file set is disjoint from all other packets — no other writer
is active (confirmed by dispatch; `git status --short` shows only coordinator prompt-text
modifications plus untracked `evidence/`).

## 2. Prerequisite check (R00 VERIFIED)

- `evidence/query-native/R00/` present: `delivery.md`, `workload.json`, `gates.json`,
  `fixtures.json`, `quality_repro.json`, `profile.txt`, `readiness.md`, `review.md`, `beir/`.
  Frozen baseline NOT modified (read-only for this packet).
- Baseline suite green on base commit: `53 passed` (`.venv/bin/python -m pytest tests/ -q`,
  exit 0, ~16 s). No existing test will be modified; all 53 must stay green.

## 3. Command validation (interpreter + SQLite capability)

- Interpreter: `.venv/bin/python`, Python 3.11.15 (uv-managed project-local env). No new
  dependency needed (stdlib `sqlite3`/`hashlib`/`uuid` only).
- Loaded SQLite build: 3.53.1 (`.venv/bin/python -c "import sqlite3; ..."`).
- FTS5 available: `CREATE VIRTUAL TABLE ... USING fts5(x)` succeeds.
- WAL mode + post-commit `PRAGMA wal_checkpoint(PASSIVE)` verified on a scratch DB
  under `/tmp` (reader/writer separation relies on WAL, already enabled by `Database`).
- Consistent-backup primitive: `VACUUM INTO '<backup>'` (atomic snapshot; used by the
  migration tool before any schema write). Scratch DBs live under `/tmp` (ext4);
  repo stays clean (`tmp_path` fixtures only).

## 4. Design resolutions (routine details, from ARCHITECTURE)

- Migration 001 adds: `schema_version`, `source`, `source_revision`, `idempotency_keys`,
  `projection_job` (outbox), `projection_generation`, `legacy_atom_map`. No
  span/extraction/graph/holon tables (R02/R04/R05 own those). All DDL is
  `CREATE TABLE IF NOT EXISTS`, so fresh DBs and migrated DBs converge.
- Canonical retained payload for R01 is `source_revision.text` (inline, byte-exact);
  no external payload files, so no pre-commit content-addressed file write is needed.
  Fresh revisions use coordinate space `raw_utf8_bytes`; migrated rows are labeled
  `legacy_atom_text` per ARCH §2 (no reconstructed raw offsets).
- One writer transaction per replacement: revision insert + current-source pointer +
  lexical delta (one `atom`/`fts_atoms` row per current revision; superseded rows
  removed in the same txn) + compact event + outbox job. Worker work happens outside
  the writer txn (claim → work on reader connections → complete).
- Crash seam: optional `_pre_commit_fault` closed-over callback invoked inside the
  writer txn before commit (not a global). Expected-revision concurrency enforced;
  stale publish raises and names the revision.
- Idempotency: `(key, payload_hash) -> revision_id`; same key + different payload
  raises a conflict error naming the key.
- Outbox leases: `lease_seconds >= 30` enforced; expired-lease reclaim; restart resume
  from persisted rows; idempotent completion.

## 5. Rollback plan

- Migration tool copies a consistent snapshot via `VACUUM INTO '<db>.bak-001'` BEFORE
  any schema write. Rollback = close handles, copy backup over the live file, reopen.
- Migration is versioned (`schema_version = 1`) and rerun-safe: a second run detects
  the version row, skips the backfill, and re-verifies parity.
- If parity < 100% the tool raises without committing the backfill (single txn) and
  dependent packets R02+ halt per packet recovery rules. Escalation triggers: schema
  contradiction with ARCHITECTURE, unfixable existing-test regression, parity < 100%.

## 6. Verdict

NOT BLOCKED. No contract contradiction found. (ARCH §9 "reject old writers" is
superseded for the `atom` surface by this packet's explicit compat-shim instruction:
existing `Database` methods keep working unmodified; recorded as a known limitation
in delivery.) Proceeding to B2 build.
