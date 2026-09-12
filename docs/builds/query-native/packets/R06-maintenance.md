# R06 — Incremental maintenance and coherent publication

Contract: TL-QN-2026-09-12.1
Status: DRAFT
Dependencies: R05 VERIFIED
Owned scope: filing maintenance; projection jobs/generations; source update integration; API status; lifecycle/concurrency tests

## Outcome

Apply insertions, edits and deletions without full-corpus rewarming for each source, and expose honest freshness.

## Implementation work

1. Update weighted vector sums/counts using old and new memberships. Clear empty persistent/cached descriptors.
2. Propagate dependency invalidation through relations, holons and syntheses. Bound work and expose pending closures.
3. Integrate graph, vector and lexical watermarks with current-source snapshots and publication checks.
4. Eliminate application-wide query lockouts through independent readers and immutable index generations.
5. Add backpressure, lease recovery, job-age metrics and generation compaction with storage headroom.

## Acceptance and verification

- Incremental state equals deterministic full rebuild for update/delete sequences within frozen float tolerance.
- Deleted ghost facets cannot occupy routing beams; empty centroid state clears in RAM and storage.
- Each update touches only documented dependencies; no unconditional full matrix rebuild.
- Concurrent queries see compatible versions or explicit partial freshness; never stale structure labeled current.
- Restart during generation publication recovers; old generation cleanup waits for readers.
- Quality and latency under concurrent writes meet R00's fixed workload envelope.

Use [EVALUATION.md](../EVALUATION.md) for common exact gates, metrics and required manifests. New harness commands are deliverables: validate their help/runtime and record exact invocations before review. Verification covers observable behavior, not just matching implementation-shaped tests.

## Delivery

Write `evidence/query-native/R06/delivery.md` and `review.md`, including candidate/diff identity, exact commands, return codes, outputs, failure cases, limits and rollback. The builder does not self-certify independent verification. Update STATE after each actual transition.

## Recovery and limits

Do not promise constant update complexity: dense dependency closures can be large. Invalidate unsafe results immediately, repair with bounded jobs, and report degraded coverage until ready.

