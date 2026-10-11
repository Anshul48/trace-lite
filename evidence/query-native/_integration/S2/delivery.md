# S2 bounded reads — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + STEERING S2 row. Interpreter: `.venv/bin/python`.

## Problem (measured)

Query-path freshness ran correlated `EXISTS` scans over all in-scope
revisions × segmentation/extraction/grouping/job rows on every query.
`scan_audit.log` (10k-pending fixture): plan opens with `SCAN s` plus
four `CORRELATED SCALAR SUBQUERY` arms; wall time **6.5 s per read**
(machine in `scan_audit.log`; contract note cited ~15 s — same order,
same bottleneck).

## Design (routine details resolved autonomously)

- `src/trace_lite/source/coverage_state.py` (new, owned): `revision_coverage`
  (per-revision stage flags + scope + derived pending/missing/reason),
  `coverage_hole` (explicit hole set w/ reasons, synced by triggers on the
  coverage table itself), `coverage_meta` (derivation version + watermark).
  SQLite triggers maintain flags incrementally per affected revision —
  O(1) per write, never recomputation. Counts use partial indexes
  (`pending=1`; live job states): O(pending), drift-free.
- `pending_coverage` internals → materialized page (contract unchanged,
  incl. 64-cap + order); `drain_outbox` → pre/post ensure + version heal
  (write path only; post-drain ensure covers grouping tables created
  lazily mid-drain — found via oracle disagreement during testing).
- `freshness()`/`status` read path → indexed lookups + bounded hole page
  (cap 64 + truncated flags); existing keys byte-identical in semantics;
  `coverage_*` keys additive. Version mismatch forces `pending` — a high
  watermark never implies completeness.
- Dossier `_served_relations`: N per-candidate revision probes → one
  indexed `IN` join (cap 2000 atoms) + the pre-existing `LIMIT 200`.
- Migration `migrate_coverage_005` (additive, sole owner this wave):
  `.bak-005` snapshot, scratch-copy backfill + oracle check, then live
  backfill + check, version row only here; reruns re-verify.
- The exact pre-S2 scan is kept as `recompute_pending_coverage` oracle.

## Before / after (10k pending; `scan_audit.log`)

| read | before | after |
|---|---|---|
| pending page (64) | 6.509 s (full scan) | 0.5 ms (`SEARCH … USING INDEX idx_cov_pending`) |
| full `freshness()` | 6.5 s+ | 1.1 ms |
| plan | `SCAN s` + correlated subqueries | zero base-table scans |

Budget: query-path freshness **< 1.0 s** at 10k pending (measured ~1 ms,
test-enforced). Praxist implication: per-tick freshness drops from the
eval-loop-dominant seconds to ~1 ms — fits a sub-minute eval loop with
headroom for 10× growth.

## Verification

- `tests/test_bounded_reads.py` (10): fuzz-equivalence (60 seeded
  transitions), lone-atom/failure/retract/supersede/grouping hole cases,
  EXPLAIN boundedness, 10k perf, version gate, migration 005.
- `coverage_equivalence.log`: 30-step transition log, all mismatches=0;
  migration + rerun verified.
- Full suite: **271 passed, 6 skipped** (5 S1 native-run skips + 1
  pre-existing skip), zero regressions.

## Owned files touched

`source/coverage_state.py` (new), `source/project.py` (internals only),
`api/app.py` (read path only), `store/migrate.py` (+005 only),
`tests/test_bounded_reads.py` (new), `evidence/…/S2/**`. Nothing else.
