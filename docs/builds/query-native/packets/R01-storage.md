# R01 — Atomic evidence and projection lifecycle

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Replace a source atomically, retain prior revisions, and make projection work durable without holding SQLite write transactions during expensive model or graph computations.

## Inputs and dependencies

- Required prior packets: R00 VERIFIED (with frozen baseline and workload gates).
- Relevant contract sections: `PROJECT.md` QN-01, QN-05; `ARCHITECTURE.md` Section 2; `EVALUATION.md` Section 4.
- Existing baseline: `src/trace_lite/storage/database.py`, `src/trace_lite/schema.sql`.
- Missing facts and readiness checks: Confirm SQLite WAL checkpoint behavior and outbox queue schema.

## Scope and interfaces

- Owned scope: `src/trace_lite/store/`, migration scripts, `src/trace_lite/source/`, store lifecycle tests.
- Shared surfaces: Schema DDL (serves as base for R02, R04, R05).
- Non-goals: Do not run dense embeddings or extract relational graphs (deferred to R03/R04).

## Suggested approach

1. Freeze DDL for source revision tracking, spans, idempotency tokens, projection jobs outbox, and generations.
2. Wrap source replacement, FTS5 sync, and outbox task generation into a single SQLite write transaction.
3. Decouple writer and reader connections; enforce WAL mode and active WAL governor post-commit.
4. Implement idempotent job leases and retries so background workers run outside the main write transaction.
5. Create a versioned migration tool to safely migrate existing SQLite stores to the new schema without data loss.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C1-1 Atomic replacement | Simulated crash leaves either intact old revision or intact new revision | Crash injection during write | `evidence/query-native/R01/crash_test.log` | Never partial deletion |
| C1-2 Idempotent submission | Repeated ingestion of identical payload yields identical revision ID without duplicates | Double-delivery stress test | `evidence/query-native/R01/idempotency.json` | Conflicting hash errors explicitly |
| C1-3 Async outbox durability | Projection tasks survive daemon restart and resume cleanly | Worker restart test | `evidence/query-native/R01/outbox_recovery.log` | Lease timeout >= 30s |
| C1-4 Migration integrity | Existing legacy atoms migrate with preserved text and ID mappings | Migration dry-run & hash check | `evidence/query-native/R01/migration_parity.json` | 100% text fidelity |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_store_lifecycle.py -v`
  - `python -m pytest tests/test_migration.py -v`
- Evidence Directory: `evidence/query-native/R01/`
- Delivery Record: `evidence/query-native/R01/delivery.md`
- Independent Review: `evidence/query-native/R01/review.md`

## Recovery and escalation

- Always retain backup of source database before running migrations.
- If migration fails parity checks, halt dependent packets R02+ immediately.
- Implementation defects route to B4 repair; schema contract issues escalate to coordinator.

