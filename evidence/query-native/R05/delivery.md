# R05 — Delivery (B2)

Record type: builder delivery
Author role and date: R05 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R05-holons.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01–R04
sets (155/155 green at B1) + R05 owned files below. No other writer active.
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5, scratch/
evidence DBs under /tmp ext4

## Delivery section

- Behavior/artifact changed and why: overlapping cross-source holons with
  reconciliation (C5-1..C5-4). `filing/holon.py` refactors the legacy
  `len(doc_ids) == 1` gate into kind-aware validation (discourse path
  verbatim); new `filing/reconcile.py` keeps link/group/canonicalize
  structurally distinct; migration-004 adds the five holon tables; two
  durable test files pin grouping, conflict preservation, discourse
  protection, cycle prevention, accumulators, and invalidation.
- Files changed (owned set ONLY):
  - `src/trace_lite/filing/holon.py` (refactor: `HOLON_KINDS` with
    `discourse_span` / `thematic_cluster` / `claim_cluster`+`argument` /
    `query_synthesis`+`query_dossier`+`derived_synthesis`; `CLAIM_ROLES`;
    multi-source `thematic`/`claim`/`synthesis` grouping; containment DAG
    with cycle + depth-8 + budget enforcement; incremental S_h/W_h/count +
    representatives; empty-clears-descriptors; dependency-pinned synthesis;
    tombstone deletes; `check_drift` / `check_holon_integrity`)
  - `src/trace_lite/filing/reconcile.py` (new: closed `HOLON_LINK_TYPES`
    vocabulary, `FORBIDDEN_AUTO_RELATIONS` rejection, read-only
    `suggest_candidates`, validated `create_link` + `audit_links`,
    versioned `canonicalize` + `split_canonical` + `restore_version`)
  - `src/trace_lite/filing/__init__.py` (additive exports only)
  - `src/trace_lite/store/schema.sql` (additive ONLY: five 004 tables +
    indexes; no version-row insert — static DDL still carries only version 1)
  - `src/trace_lite/store/migrate.py` (additive ONLY: `HOLON_TABLES`,
    `backup_path_for_holon`, `migrate_holon_004` + `.bak-004` snapshot;
    migration-001/002/003 bodies byte-untouched)
  - `tests/test_holon_reconciliation.py` (new, 12 tests),
    `tests/test_cross_source_grouping.py` (new, 15 tests); all pre-existing
    tests unmodified (`tests/test_filing.py` green as-is)
  - `evidence/query-native/R05/{readiness.md,delivery.md,gen_evidence.py,
    cross_doc_holon.json,conflict_preservation.log,discourse_guard.log,
    cycle_detection.log}` (this packet; generator retained for re-verification)
- Material implementation decisions:
  - Legacy `doc_id` column kept `NOT NULL`: single-source holons of any kind
    store their real doc_id; only multi-source rows store `''`
    (`CROSS_SOURCE_DOC`), so `holons_of_doc` never leaks cross-source rows
    into single-doc queries and never shows a fake doc.
  - `discourse_span` is the default kind and keeps the exact legacy contract
    (single-source, contiguous slice, ≥2 atoms) — the unmodified legacy test
    passes positionally through the new code.
  - Similarity is a dead end by construction: `suggest_candidates` returns
    scores with no `rel_type` key and writes nothing; `create_link` rejects
    identity/support/contradiction/causation (and unknown types) even for
    byte-identical inputs (proven at score 1.0). Membership rows, link rows,
    and canonical rows are separate tables — no op can masquerade as another.
  - Canonicalize versions forward (old version superseded, history kept);
    split partitions the live target set into new entities; restore replays a
    historical set as a new version. Aliases survive as mapping rows.
  - Accumulators live in `holon_state` (S_h blob, W_h, count, top-3
    centroid-nearest representatives, token-csv descriptor, member digest);
    every mutation appends a `holon_version` row. Drift check recomputes
    exactly (observed drift 0.0 across the suite).
  - Invalidation is BFS over the `dependency` reverse index plus containment
    parents (visited set, 10k budget): the edited holon stays active while
    dependent synthesis and ancestors flip to `invalidated` and disappear
    from active-only readers until `revalidate`. Deletes tombstone (ID kept).
  - Polymorphic link/canonical endpoints are existence-checked at write and
    re-checked by `audit_links`; all single-typed edges carry real FKs.
- Known limitations and checks not run: member rows are `atom`-typed in R05
  (columns + semantics frozen for future `assertion`/`mention` rows);
  representatives recompute from live members per mutation (O(k*d), bounded
  fixtures — no benchmark-scale profiling); no power-loss test
  (injected-logic faults only via rollback paths); encoder-space change
  still requires a new generation per ARCH §5 (accumulators assume the
  pinned 128-d hashed space).

## Evidence

| Criterion | Command or evaluation method (exit) | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C5-1 Cross-source grouping | `.venv/bin/python evidence/query-native/R05/gen_evidence.py` (exit 0) + `test_c51_*`, `test_overlapping_*` | cond-a+cond-b atoms join one `thematic_cluster`; drift 0.0; texts byte-intact, no merged-text column | `evidence/query-native/R05/cross_doc_holon.json` | bounded conditions pair |
| C5-2 Conflict preservation | same script (exit 0) + `test_c52_*` | claim+qualification+counterevidence coexist, status active, similarity 0.3223 yields scores only | `evidence/query-native/R05/conflict_preservation.log` | zero false consensus |
| C5-3 Discourse protection | same script (exit 0) + `test_c53_*` | multi-source and gappy discourse rejected with legacy messages | `evidence/query-native/R05/discourse_guard.log` | strict single-source |
| C5-4 Cycle prevention | same script (exit 0) + `test_c54_*`, depth/budget tests | cycle + self-containment raise; depth 8 stands, 9th rejected; budget overruns raise; zero leakage | `evidence/query-native/R05/cycle_detection.log` | bounded DAG fixtures |
| Migration-004 additive | `test_migration_004_*` (3 tests) | 5 tables added, 1→2→3→4 chain intact, rerun safe, `.bak-004` written, legacy IDs preserved + backfilled | new test files | bounded fixture |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 182 passed (155 pre-existing + 27 new), 1 pre-existing warning | session log | pre-existing Starlette warning only |
| New tests | `.venv/bin/python -m pytest tests/test_holon_reconciliation.py tests/test_cross_source_grouping.py -q` (exit 0) | 27 passed | new test files | — |

## Frozen holon contract (for R06/R07; schema-freeze note)

Tables (all `IF NOT EXISTS`; FKs enforced; `schema_version = 4` written only
by `migrate_holon_004`): `holons(holon_id, doc_id, tier, start_byte,
end_byte)` — legacy columns unchanged; `doc_id` is the single source for
single-source holons, `''` for multi-source (never a fake doc).
`holon_members(holon_id→holons, atom_id→atom, position, weight REAL > 0,
role NULLABLE, member_kind, member_version)` PK `(holon_id, atom_id)` —
weight differs from confidence; role is REQUIRED in `CLAIM_ROLES`
(premise/claim/qualification/counterevidence) for `claim_cluster`/`argument`
and FORBIDDEN otherwise; R05 writes `member_kind='atom', member_version=1`
(future `assertion`/`mention` rows keep these semantics: typed ref + pinned
version, never bare strings). `holon_state(holon_id PK→holons, kind, status
active|invalidated|deleted, descriptor token-csv, derivation lineage,
vector_sum BLOB S_h, weight_sum W_h, member_count, representatives_json
≤3 centroid-nearest atom ids, member_digest, updated_at)` — one live row per
holon; empty/deleted holons clear vector/descriptor/representatives/digest.
`holon_version(version_id, holon_id, version, kind, status, descriptor,
derivation, weight_sum, member_count, representatives_json, member_digest,
created_at)` UNIQUE `(holon_id, version)` — append-only snapshot per
mutation/status change; current = MAX(version). `holon_containment
(parent_holon_id→holons, child_holon_id→holons, created_at)` PK pair,
self-check — acyclic, depth ≤ 8, budget-guarded walks. `holon_link(link_id,
src_kind, src_id, dst_kind, dst_id, rel_type ∈ {topic_overlap,
shared_entity, temporal_sequence, references, elaborates}, evidence_json,
created_at)` — never identity/support/contradiction/causation.
`holon_canonical(canonical_id, version, target_kind, target_id, label,
status active|superseded, derivation, created_at)` PK full tuple — aliases
preserved as rows; split/restore create versions, never rewrite history.
R05 keeps writing R04 `dependency` rows for everything it derives
(`member` per holon→atom, `contains` per containment, `derived_from` per
synthesis lineage) with the reverse index serving R06 invalidation. R06/R07
may ADD tables/columns but must not alter these tables' columns, must not
merge member texts, must not treat membership as agreement, and must keep
opposing roles unmerged.

## Independent findings

(none — builder role; verifier uses review.md)
