# R04 — Readiness (B1)

Role: R04 builder (B1 readiness + B2 build)
Packet and contract revision: R04-local-graphs.md / TL-QN-2026-09-12.1
Date (UTC): 2026-09-12
Base commit: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` (verified via `git rev-parse HEAD`)

## 1. Ownership and isolation

Owned (create/modify only these):

- `src/trace_lite/graph/` (new package: frames, structural extractor,
  span-anchored relations, dependency records)
- `src/trace_lite/store/` additive migration-003 step ONLY (`assertion`,
  `assertion_argument`, `relation`, `entity_mention`, `extraction_run`,
  `dependency` DDL in `schema.sql` + `migrate_graph_003()` in `migrate.py`;
  migration-001/002 logic untouched)
- `tests/test_local_graph.py`, `tests/test_assertion_extractor.py` (new, durable)
- `evidence/query-native/R04/**`

Prohibited and untouched: `src/trace_lite/router/` (R03 builds concurrently in
the same checkout — disjoint set, no reads needed beyond the freeze below),
`source/`, `store/` files except the 003 step, `api/`, `filing/`, `cordis/`,
`obsidian/`, pre-existing tests, `benchmarks/`, R00–R03 evidence, `docs/`.

## 2. Base and prerequisite check (R02 VERIFIED)

- `git status --short` shows only the R01+R02 owned sets on top of base
  `0df044b` (modified coordinator prompt-text + `obsidian/parser.py` +
  `store/{__init__,schema.sql}`; untracked `source/`,
  `store/{generations,migrate,outbox}.py`, R01/R02 tests,
  `evidence/query-native/`). No `router/` modification is present, so the
  concurrent R03 build has not collided with this checkout state. Expected
  base, not foreign drift.
- `evidence/query-native/R02/review.md` §6 records **PASS** after B4 repair
  (F1 fixed; full suite 112 passed). R02-prereq check below confirms the frozen
  span API this packet consumes.
- R02 frozen span API (consumed, not changed): `span_id_for` /
  `create_segmentation_view` / `get_spans` / `excerpt_span` / `envelope` in
  `src/trace_lite/source/views.py`; span IDs are `sha256(revision:run:ordinal:
  start:end)[:32]`; excerpts are hash-verified half-open UTF-8 byte ranges.
  R04 anchors every relation to these span IDs and resolves them only through
  `excerpt_span` (EVALUATION gate 1 primitive).
- `evidence/query-native/R00/fixtures.json` DEV families present
  (`conditions`, `negation`, `entity-ambiguity`, `unfiled`,
  `off-topic-abstention`); the batching/contention pair is the C4-1/C4-3 core.
- Baseline suite green on this tree before R04 edits: 112 passed
  (`.venv/bin/python -m pytest tests/ -q`, exit 0). No existing test modified.
- Interpreter: `.venv/bin/python`, Python 3.11.15, SQLite 3.53.1 + FTS5.
  No new dependency (stdlib only: `re`, `hashlib`, `json`, `sqlite3`, `uuid`).

## 3. Design notes (ARCHITECTURE §4–§5, bound before building)

- Assertion frames: subject/predicate/object + polarity + modality +
  conditions + units + temporal/version scope + `epistemic_status`
  (`source_asserted` default; `validated`/`hypothesized` are never emitted —
  no validation lineage exists in R04). N-ary via `assertion_argument` role
  links. Unknown fields stay `NULL`/unknown — the parenthetical in cond-a
  ("fewer syscalls per item") is stored verbatim as a qualifier argument,
  never promoted to a causal mechanism claim.
- Provenance: every relation carries `span_id` + byte range + `run_id`;
  ambiguous bindings (bare pronouns, unqualified "Chen") persist with
  `status='unresolved'` plus bounded retrieval candidates, never forced.
  Lexical similarity never emits support/contradiction/causation edges;
  source-asserted causation ("improves") is labeled asserted, not validated.
- Windows: extraction window is one span (≤1200 chars by the R02 cap) plus
  the 1-hop parent-heading envelope; long-range links use bounded top-k
  token-overlap candidate retrieval invoked only for unresolved mentions —
  no exhaustive all-pairs comparison.
- Dependency rows for every derived record (assertion/mention/relation →
  span/run/revision/view) for R06 invalidation; reverse index on
  `(requires_kind, requires_id)`.

## 4. Schema-freeze note for R05 (shared graph contract)

The R04 tables (`extraction_run`, `entity_mention`, `assertion`,
`assertion_argument`, `relation`, `dependency`) are the frozen source-local
graph contract R05 reconciles against. R05 may ADD tables/columns (holons,
cross-source membership) but must not alter these tables' columns or
reinterpret local `rel_type` values: local types keep asserted-not-validated
semantics, opposing assertions stay unmerged rows, and `unresolved` rows stay
unresolved until a future run with real lineage rebinds them. The exact
frozen contract is documented in `delivery.md` §Frozen graph contract.

## 5. Migration-003 shape (additive-only rollback)

- `schema.sql`: six `CREATE TABLE IF NOT EXISTS` blocks + indexes only. No
  `schema_version` insert (static DDL still carries only the version-1 row,
  so migration-001 callers keep observing `MAX == 1` and migration-002
  callers keep observing `MAX == 2` until they opt into 003).
- `migrate.py`: new `migrate_graph_003(db_path)` step (snapshot `.bak-003`
  via `VACUUM INTO` before any write, apply DDL, record
  `schema_version = 3`, verify; rerun-safe). Existing `migrate_legacy` /
  `migrate_spans_002` bodies untouched. Fresh `Database` opens pick up the
  tables via `schema.sql` directly.
- Rollback = additive-only: close handles, restore `.bak-003` over the live
  file (or keep using the pre-003 file — no existing table is altered).
- Escalation triggers: any existing-test regression, any orphan edge
  (target: zero), or ambiguity-forcing pressure (response: keep
  `status='unresolved'`, never invent a binding).

## 6. Verdict

NOT BLOCKED. Proceeding to B2 build.
