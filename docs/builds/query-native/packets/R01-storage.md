# R01 — Atomic evidence and projection lifecycle

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R00 VERIFIED
Owned scope: src/trace_lite/store/; new migration/source-service modules; API/CLI ingestion delegation; store lifecycle tests

## Outcome

Replace a source atomically, retain prior revisions, and make projection work durable without holding transactions during model computation.

## Implementation work

1. Freeze DDL for source/revision/span identity, idempotency, projection jobs, dependencies and generations.
2. Add one transaction boundary for source replacement, lexical maintenance, compact event and outbox creation.
3. Implement writer ownership, reader connections, expected-revision checks, lease/retry and idempotent job completion.
4. Define FULL/NORMAL acknowledgment semantics; record actual SQLite patched-runtime status before concurrent checkpoint tests.
5. Add a versioned migration into a separate database, consistent backup and rollback manifest.

## Acceptance and verification

- Crash before/after commit yields coherent old/new state; canonical payload hashes and lexical MATCH behavior agree.
- Duplicate request and worker delivery cannot duplicate effects; conflicting idempotency payloads fail explicitly.
- Old-job publication and concurrent source edits cannot overwrite a later revision.
- Pending/ready/failed projection status survives restart; long model work occurs outside the writer transaction.
- Migration preserves legacy text and ID mappings without inventing original raw-byte provenance.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R01/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not delete the source database. If migration parity fails, keep old writer/read paths active and diagnose the copied candidate. Cross-file payload publication must be tested before relying on it.

