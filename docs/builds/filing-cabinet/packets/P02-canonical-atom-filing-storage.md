# P02 — Canonical Atom Filing Storage & Active WAL Governor

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../../query-native/PROJECT.md), [execution plan](../../query-native/EXECUTION.md), and [state](../../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Status: READY
Kind: implementation
Contract revision: 2026-09-11.2
Owner/session: Builder Session 2

## Outcome
Implement the core single-node SQLite storage engine for Trace-Lite. Provides append-only event sourcing (`events`), byte-exact canonical atom persistence (`atom`), external-content FTS5 index (`fts_atoms`), and an Active WAL Checkpoint Governor that issues `PRAGMA wal_checkpoint(PASSIVE)` every 5,000 committed documents strictly outside write transactions.

Concrete Example: Ingesting an Obsidian note or agent artifact writes an immutable event into `events`, stores canonical atom spans in `atom` with byte offsets, and indexes text into `fts_atoms`. After 5,000 documents, the WAL governor automatically folds WAL pages into the database without locking.

Failure Case: Calling checkpoint inside an open transaction raises `database table is locked`. The governor must execute post-commit outside active connection transaction blocks.

## Inputs and dependencies
- Required prior packets: P01.
- Relevant contract sections: `ARCHITECTURE.md` §2.1, `DESIGN_REVIEW.md` CRIT-04.
- Target directory: `src/trace_lite/store/`.

## Scope and interfaces
- Owned files:
  - `src/trace_lite/store/__init__.py`
  - `src/trace_lite/store/database.py`
  - `src/trace_lite/store/schema.sql`
  - `src/trace_lite/store/governor.py`
  - `tests/test_store.py`
- Schema:
  - `atom`: `(id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, content_hash BLOB, start_byte INT, end_byte INT, text TEXT NOT NULL)`
  - `events`: `(event_id TEXT PRIMARY KEY, stream_id TEXT, stream_seq INT, event_type TEXT, occurred_at TEXT, payload_json TEXT)`
  - `fts_atoms`: Virtual FTS5 table with `content='atom'`, `content_rowid='id'`.

## Suggested approach
1. Create `database.py` managing SQLite connection with WAL mode and `synchronous=NORMAL`.
2. Create `schema.sql` with table definitions and indexes.
3. Create `governor.py` tracking committed documents and invoking `PRAGMA wal_checkpoint(PASSIVE)` post-commit.
4. Add unit tests in `tests/test_store.py` verifying atom round-trip and checkpoint governor.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C01 | Canonical atom round-trip | Insert 1,000 atoms; retrieve by ID and hash; byte-exact match | Unit test pass | 100% byte fidelity |
| C02 | WAL governor fires at 5k docs | Bulk insert 10,000 documents; verify checkpoint logs | Checkpoint log output | Zero database locks |
| C03 | FTS external content search | Query FTS5 table; verify BM25 scores and correct atom retrieval | FTS search test pass | Zero text duplication |

## Execution and evidence
- Execution command:
  ```bash
  /mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/pytest tests/test_store.py
  ```
- Evidence directory: `evidence/P02/`.

## Recovery and escalation
- Safe rollback: `git checkout HEAD -- src/trace_lite/store/ tests/test_store.py`
