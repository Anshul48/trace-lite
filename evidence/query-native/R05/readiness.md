# R05 — Readiness (B1)

Role: R05 builder (B1 readiness + B2 build)
Packet and contract revision: R05-holons.md / TL-QN-2026-09-12.1
Date (UTC): 2026-09-12
Base commit: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` (verified via `git log --oneline -1`)

## 1. Ownership and isolation

Owned (create/modify only these):

- `src/trace_lite/filing/**` (holon store refactor + reconciliation engine)
- `src/trace_lite/store/` additive migration-004 step ONLY (holon/holon_member/
  holon_version DDL in `schema.sql` + `migrate_holon_004()` in `migrate.py`;
  migration-001/002/003 bodies untouched; SOLE migration owner)
- `tests/test_holon_reconciliation.py`, `tests/test_cross_source_grouping.py`
  (new, durable)
- `evidence/query-native/R05/**`

Prohibited and untouched: `graph/`, `router/`, `source/`, `store/` files except
the 004 step, `api/`, `cordis/`, `obsidian/`, pre-existing tests
(`tests/test_filing.py` stays green unmodified), `benchmarks/`, R00–R04
evidence, `docs/`. No other writer is active (R01–R04 integrated in tree).

## 2. Base and prerequisite check (R03 + R04 VERIFIED)

- `git log --oneline -1` shows base `0df044b`; `git status --short` shows only
  the R01–R04 owned sets on top (no foreign drift, no other builder files).
- Full suite green on this tree before R05 edits: 155 passed
  (`.venv/bin/python -m pytest tests/ -q`, exit 0, one pre-existing Starlette
  warning only).
- R04-prereq check (frozen graph-table contract — consumed, not changed):
  `evidence/query-native/R04/delivery.md` §Frozen graph contract freezes
  `extraction_run`, `entity_mention`, `assertion`, `assertion_argument`,
  `relation`, `dependency` (local `rel_type` vocabulary asserted-not-validated,
  opposing assertions unmerged, `unresolved` rows unbound, dependency rows for
  everything derived). R05 adds holon tables/columns only; writes `dependency`
  rows for derived holons; never reinterprets local types.
- R03-prereq check (retrieval interfaces — consumed, not changed):
  `evidence/query-native/R03/delivery.md` ships the hybrid retrieval plane
  (BM25 + numpy-IVF ANN + abstention gate). R05 holons feed the holon-guided
  retrieval candidate path; no router file touched.
- `evidence/query-native/R00/fixtures.json` conflict families present
  (`conditions` batching pair, `negation`, `entity-ambiguity`); the
  conditions pair is the C5-1/C5-2 core fixture.
- Interpreter: `.venv/bin/python` (Python 3.11.15), SQLite 3.53.1 + FTS5.
  No new dependency (stdlib + existing `filing/engine.py` hashed vectors).

## 3. Legacy-holon compatibility notes

Baseline `src/trace_lite/filing/holon.py`: `HolonStore.create_holon(atom_ids,
tier="nav")` enforces `len(doc_ids) == 1` plus per-document contiguity;
`tests/test_filing.py::test_holon_grouping_and_boundaries` pins that behavior
(cross-doc rejected, gappy slice rejected, unknown atom → KeyError).

Refactor plan (replaces the rigid check with kind-aware validation, keeps the
legacy path for discourse kinds):

- Default kind `discourse_span` preserves the exact legacy contract
  (single-source contiguous, ≥2 atoms), so the unmodified `test_filing.py`
  path keeps passing positionally.
- New kinds relax only what the packet allows: `thematic_cluster`
  (multi-source overlapping, no contiguity), `claim_cluster` (+`argument`
  alias, explicit premise/claim/qualification/counterevidence roles),
  `query_synthesis` (+`query_dossier`/`derived_synthesis` aliases, virtual,
  dependency-pinned).
- Reconciliation ops kept structurally distinct: `link` (typed edge row in a
  new table, closed vocabulary — similarity scores can never produce
  identity/support/contradiction/causation), `group` (holon membership rows),
  `canonicalize` (versioned mapping rows, reversible/splittable).
- Containment DAG in a new edge table with cycle rejection, depth bound,
  visited-set + budgeted traversals; support deletion invalidates dependent
  synthesis via `dependency` reverse-index + containment ancestors.
- Incremental `S_h/W_h/count` + representative refs per holon (O(k*d)
  delta updates, exact-recompute drift check, empty clears descriptors).
- NO text fusion: membership rows only, never merged strings.

## 4. Migration-004 shape (additive-only rollback)

- `schema.sql`: new `CREATE TABLE IF NOT EXISTS` blocks only (`holon_state`,
  `holon_version`, `holon_containment`, `holon_link`, `holon_canonical`) +
  indexes. No `schema_version` insert (static DDL still carries only the
  version-1 row). `holons`/`holon_members` DDL stays owned by
  `filing/holon.py` (`HOLON_SCHEMA`), extended with additive columns only
  (`weight`, `role`, `member_kind`, `member_version` with defaults).
- `migrate.py`: new `migrate_holon_004(db_path)` step (snapshot `.bak-004`
  via `VACUUM INTO` before any write, apply DDL, `ADD COLUMN` for pre-004
  `holon_members`, backfill `holon_state` for legacy holons with IDs intact,
  record `schema_version = 4`, verify; rerun-safe). Bodies of
  `migrate_legacy` / `migrate_spans_002` / `migrate_graph_003` untouched.
- Rollback = additive-only: close handles, restore `.bak-004` over the live
  file (or keep using the pre-004 file — no existing table is altered or
  dropped, legacy holon IDs byte-preserved).
- Escalation triggers: any existing-test regression, any cycle leakage, or
  false-consensus pressure (response: keep contradictory roles coexisting,
  never merge).

## 5. Verdict

NOT BLOCKED. Proceeding to B2 build.
