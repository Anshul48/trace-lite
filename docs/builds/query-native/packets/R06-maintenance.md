# R06 — Incremental maintenance and coherent publication

Status: DRAFT
Kind: implementation
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Apply insertions, edits, and deletions without O(M²) full-corpus rewarming or application-wide query lockouts, eliminating ghost facets and exposing honest generation freshness.

## Inputs and dependencies

- Required prior packets: R05 VERIFIED.
- Relevant contract sections: `PROJECT.md` QN-05, QN-07; `ARCHITECTURE.md` Section 7; `EVALUATION.md` Section 4.
- Existing baseline: `src/trace_lite/filing/engine.py` (O(M²) scan), `src/trace_lite/api/app.py` (`guard = threading.Lock()`).
- Missing facts and readiness checks: Measure concurrent read throughput during high-frequency write syncs.

## Scope and interfaces

- Owned scope: `src/trace_lite/filing/engine.py`, `src/trace_lite/api/app.py`, index generation publication, concurrency tests.
- Shared surfaces: Router and API query serving.
- Non-goals: Do not degrade search consistency during background index updates.

## Suggested approach

1. Refactor centroid updates to O(1) running accumulators: persist `(vector_sum, atom_count)` per facet and holon. On delete/edit, subtract old vector and add new vector.
2. Clear empty centroid blobs and exclude zero-member facets from Tier 2 routing beams, eliminating ghost facet beam saturation.
3. Remove global `guard` mutex in `app.py`. Use SQLite WAL reader connections and atomic index generation swaps (`gen_id`).
4. Propagate bounded dependency invalidation: editing a premise marks dependent claims stale without triggering full vault re-indexing.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C6-1 Incremental parity | O(1) running centroid sum matches deterministic full rebuild within float tolerance | Full rebuild diff test | `evidence/query-native/R06/centroid_parity.log` | Tolerance < 1e-5 |
| C6-2 Ghost facet clearance | Deleted notes clear centroid blobs; 0-member facets never occupy routing beam | Ghost facet injection probe | `evidence/query-native/R06/ghost_facet.json` | 0 ghost beam slots |
| C6-3 Concurrency lockout elimination | Query latency remains < 15ms during sustained background note synchronization | Concurrent read/write stress | `evidence/query-native/R06/concurrency_p95.json` | Zero thread deadlock |
| C6-4 Stale dependency invalidation | Modifying a premise cascades `status='stale'` to dependent syntheses | Invalidation graph test | `evidence/query-native/R06/invalidation.log` | Bounded neighborhood |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/test_concurrent_query_sync.py -v`
  - `python -m pytest tests/test_incremental_centroids.py -v`
- Evidence Directory: `evidence/query-native/R06/`
- Delivery Record: `evidence/query-native/R06/delivery.md`
- Independent Review: `evidence/query-native/R06/review.md`

## Recovery and escalation

- If incremental floating point error drifts over 100,000 edits, trigger periodic background reconciliation.
- Threading deadlocks or SQLite busy errors route immediately to B4 repair.

