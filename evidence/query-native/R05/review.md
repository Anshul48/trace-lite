# R05 — Independent review (B3)

Role: R05 independent verifier. Builder conclusions treated as claims.
Packet and contract revision: R05-holons.md / TL-QN-2026-09-12.1
Candidate: git HEAD `0df044b` + R01–R05 working tree (all uncommitted).
Date (UTC): 2026-09-12. Interpreter: `.venv/bin/python` (3.11.15), SQLite 3.53.1 + FTS5.
This file is the ONLY file written by the verifier.

## Execution scope

- Read: `docs/builds/query-native/packets/R05-holons.md`,
  `docs/builds/query-native/ARCHITECTURE.md` (§5–6),
  `evidence/query-native/R05/delivery.md`, `evidence/query-native/R05/readiness.md`.
- Scope check via `git status --short` + per-file diff attribution against the
  R01–R04 delivery.md owned sets (details under Findings F1).
- Ran myself, exit codes recorded below:
  - `pytest tests/test_holon_reconciliation.py tests/test_cross_source_grouping.py -q` → **exit 0, 27 passed**
  - `pytest tests/ -q` (full suite) → **exit 0, 182 passed**, 1 pre-existing
    Starlette deprecation warning (matches builder claim of 155 + 27).
  - `/tmp/r05_b3_probes.py` (20 independent checks, scratch DBs only) → **exit 0, 20/20**.
  - `/tmp/r05_b3_mig004.py` (migration-004 scratch, incl. genuine legacy-shaped
    fixture built from verbatim pre-R05 `HOLON_SCHEMA` at git HEAD) → **exit 0, ALL PASS**.
  - One inline doc-isolation probe → **exit 0**.
- No repo file created, modified, or deleted except this review.

## Evidence C5-1..C5-4 (independent verdicts)

| Criterion | Verdict | Basis |
|---|---|---|
| C5-1 Cross-source grouping | **CONFIRMED** | Committed `test_c51_*` + `test_overlapping_*` join cond-a/cond-b atoms in one `thematic_cluster` with `doc_id == ''`. Independent P1: members from 2 sources, texts byte-intact, descriptor is a token csv (`'batching,contention,under'`, never prose), `holon_members` carries typed refs only (no `text` column), no merged/fused column in any holon DDL. `cross_doc_holon.json` spot-checked and consistent. |
| C5-2 Conflict preservation | **CONFIRMED** | Committed `test_c52_*` asserts DB-level stored roles `claim/counterevidence/premise/qualification`, status stays `active`. Independent P2: same, plus schema has no verdict/consensus field. `conflict_preservation.log` matches (similarity 0.3223 yields scores only). Zero false consensus observed. |
| C5-3 Discourse protection | **CONFIRMED** | Committed `test_c53_*` rejects multi-source default-kind and gappy slices, accepts the legacy single-source path. Independent P3 reproduced all three, including the exact legacy error message. `discourse_guard.log` matches. |
| C5-4 Cycle prevention | **CONFIRMED** | Committed `test_c54_*` builds a REAL 3-node cycle attempt (A→B→C→A, rejected at close) plus self-containment, then asserts descendants/ancestors/depth and idempotent re-add. Independent P4 reproduced a 2-cycle + self-containment rejection with the DAG intact afterwards. `cycle_detection.log` matches. |

Supporting behavior independently confirmed: incremental S_h/W_h/count matches an
exact recompute done via raw SQL + engine primitives only (l2 = 0.0, W = 3.5 vs 3.5)
after add + weight change + remove, with `check_drift` reporting `ok` and 0.0 drift;
`holons_of_doc` excludes cross-source holons (sentinel `''` never leaks) while
returning single-source discourse holons; legacy holon IDs resolve post-004 with
drift 0 (both simulated-legacy and genuine-legacy fixtures).

## Tautology inspection (committed tests)

No tautologies found. Cycle tests attempt real circular containment (not
pre-broken fixtures). Conflict tests assert explicit disagreement roles read back
from `holon_members` rows, not mere co-membership. Similarity tests use
byte-identical inputs at score 1.0 and still require rejection of every
`FORBIDDEN_AUTO_RELATIONS` member plus unknown types, with zero edge-table
leakage. `suggest_candidates` asserts the exact output key set
(`{atom_id, doc_id, score}`, no `rel_type`) and unchanged table counts.
Invalidation tests assert exact invalidated sets (`{grand, synth}`, parent stays
`active`), active-only reader hiding, and `revalidate` recovery.

## Migration-004 (scratch copies only)

Additive, backup written, rerun-safe, legacy data intact — all confirmed, including
on a genuinely legacy-shaped fixture (verbatim pre-R05 `HOLON_SCHEMA`, 3-column
members, no aux tables): exactly the 5 tables added, the `ADD COLUMN` path fires
for all 4 member columns with weight defaulting to 1.0, 1 state backfilled,
`.bak-004` written and genuinely pre-004 (no 004 tables inside), atoms intact,
`integrity_check` ok, rerun reports version 4 with no duplicate backfill.
Migration-001/002/003 chain intact (1→2→3→4).

## Non-goals

- No merged/fused text anywhere: `grep` over `src/trace_lite/filing/` finds only
  "never merged strings / never fused prose" comments; committed
  `test_no_text_fusion_anywhere` pins descriptor length/shape against concatenation.
- Independent global retrieval untouched: `grep -rni holon src/trace_lite/router/`
  returns no matches; `FlatHybrid.search` answers correctly on a DB with all five
  holon aux tables dropped (independently probed, exit 0).

## Frozen holon contract for R06/R07

Documented in `delivery.md` §"Frozen holon contract" and confirmed against the
code: the five aux tables with columns/PKs/statuses (`active|invalidated|deleted`),
`CLAIM_ROLES` required for `claim_cluster`/`argument` and forbidden otherwise,
`doc_id == ''` multi-source sentinel, `dependency` rows (`member`/`contains`/
`derived_from`) with the reverse index for R06 invalidation, append-only
`holon_version`, DAG bounds (acyclic, depth ≤ 8, budgeted walks), tombstone
deletes, and the R06/R07 constraint (may ADD tables/columns; must not alter these
columns, merge member texts, treat membership as agreement, or merge opposing
roles). A maintenance worker (needs statuses, reverse index, containment parents,
`revalidate`) and a dossier compiler (needs member refs + roles + counterevidence,
representatives, descriptors) both have what they need.

## Findings

- F1 (scope — NO VIOLATION): `git status` shows files outside the R05 owned set,
  but every one attributes to R01–R04 packets or the coordinator:
  `obsidian/parser.py` → R02 (diff self-labels "R02 adapter fields");
  `router/__init__.py` + `encoder/index_adapter/projection.py` → R03;
  `graph/` → R04; `source/`, `store/{outbox,generations}.py`, R01–R03 test files →
  their packets; `store/__init__.py` diff is R01-only (no holon content);
  `COORDINATOR*.md`/`STATE.md` diffs are coordinator bookkeeping (R05 row now
  reads IN_REVIEW). `schema.sql` is +318/−0 (additive; 004 block delimited) and
  `migrate.py` carries R05 content only in the `HOLON_MIGRATION_VERSION` constant
  plus the line-377+ `HOLON_TABLES`/`backup_path_for_holon`/`migrate_holon_004`
  section. `tests/test_filing.py` unmodified. No R05 leakage.
- F2 (observation — NOT BLOCKING): fresh DBs are born from the static DDL with
  empty 004 aux tables already present, so on new-born DBs "backup is pre-004"
  holds for data but not for table-set, and strict table-additivity is observable
  only on old-born DBs. The builder's rollback claim ("restore `.bak-004`")
  remains operationally correct; the genuine-legacy scratch run above proves the
  old-DB path. Suggest one clarifying sentence in R06 readiness, no code change.

## Verdict

**PASS.** All four acceptance criteria confirmed by independent probes against
scratch DBs, the full suite (182) and both new test files (27) re-run green by
the verifier with no tautologies, migration-004 proven additive/backup-safe/
rerun-safe/legacy-preserving on a genuine legacy fixture, non-goals intact, and
the frozen R06/R07 contract documented.

## Next action

Coordinator may mark R05 VERIFIED and unblock R06 (incremental maintenance and
publication) against the frozen holon contract cited above.
