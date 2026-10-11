# R04 — Delivery (B2)

Record type: builder delivery
Author role and date: R04 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R04-local-graphs.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01 set
+ R02 set (VERIFIED) + R04 owned files below. R03 builds concurrently in the
same checkout (disjoint `router/` set, 16 tests, all green — untouched).
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5, repo
v9fs, scratch/evidence DBs under /tmp ext4

## Delivery section

- Behavior/artifact changed and why: evidence-linked source-local graphs
  (C4-1..C4-4). New `graph` package owns typed assertion frames, the
  deterministic structural extractor, span-anchored relations, and dependency
  records; migration-003 adds the six ARCH §2 graph tables; two durable test
  files pin fidelity, provenance, conditions, ambiguity, and fallback.
- Files changed:
  - `src/trace_lite/graph/__init__.py` (new: frozen exports)
  - `src/trace_lite/graph/frames.py` (new: `AssertionFrame`, `EntityMention`,
    `RelationEdge`, epistemic/polarity/modality/status constants, frozen
    `REL_TYPES`/`MENTION_ROLES` vocabularies)
  - `src/trace_lite/graph/extractor.py` (new: structural extractor —
    causal keywords improves/degrades/restores/requires/conflicts-with,
    comparison, definitions, reporting/reference hooks, under/unless/only/
    during/when conditions; per-span bounded windows + top-3 token-overlap
    candidate retrieval for long-range links, no all-pairs; never raises)
  - `src/trace_lite/graph/store.py` (new: `create_extraction_run`,
    `persist_graph`, `check_integrity` zero-orphan audit, `get_graph`)
  - `src/trace_lite/store/schema.sql` (additive ONLY: six 003 tables +
    indexes; no version-row insert — static DDL still carries only version 1)
  - `src/trace_lite/store/migrate.py` (additive ONLY: `GRAPH_TABLES`,
    `backup_path_for_graph`, `migrate_graph_003` + `.bak-003` snapshot;
    migration-001/002 bodies byte-untouched)
  - `tests/test_assertion_extractor.py` (new, 15 tests),
    `tests/test_local_graph.py` (new, 12 tests); all pre-existing tests
    unmodified
  - `evidence/query-native/R04/{readiness.md,delivery.md,gen_evidence.py,
    relation_fidelity.json,provenance_check.log,condition_test.json,
    fallback_test.log}` (this packet; generator retained for re-verification)
- Material implementation decisions:
  - `epistemic_status` is `source_asserted` on every emitted row; the store
    rejects `validated`/`hypothesized` without lineage, and the extractor
    cannot produce them. Source-asserted causation ("improves") is labeled
    asserted, never validated.
  - Unknown fields stay `NULL`/unknown: cond-a's "(fewer syscalls per item)"
    is a verbatim qualifier argument, never a causal mechanism; bare
    pronouns and unqualified names with qualified variants in the input
    ("Chen" beside "Chen (storage team)") persist as `status='unresolved'`
    with bounded retrieval candidates and NULL endpoints — never forced.
    Name qualifiers in parentheses stay inline as subject identity.
  - Similarity never emits edges: the off-topic salad yields zero frames;
    the contradictory pair (improves-positive vs disappears-negative)
    coexists as two rows with no merge/supports/contradicts edge (that
    vocabulary does not exist in R04).
  - Earliest surface verb wins (reporting beats later copulas); trailing
    `does NOT`-style auxiliaries are stripped from subjects while keeping
    negative polarity; `during`-clauses map to `temporal_scope`, `on NVMe`
    to `version_scope`; trailing `with ...` phrases are kept verbatim in
    the object AND stored as qualifier arguments (no loss, no invention).
  - Relation endpoints use separate nullable FK columns with a one-endpoint-
    per-side CHECK: orphan edges are unwritable (proven by an
    `IntegrityError` test), not just detectable. `dependency` rows cover
    every derived record (span+run) with a reverse index for R06.
- Known limitations and checks not run: structural patterns only — verbs
  outside the frozen vocabulary yield no frame (e.g. bare "fails"); no
  open-domain/LLM extraction (memory-blowup non-goal); cross-source identity
  is R05's job (same-source `canonical_key` is provisional only); bounded
  fixtures, no benchmark-scale loads; extractor input caps (200
  sentences/span, 50k chars) truncate with warnings, never crash.

## Evidence

| Criterion | Command or evaluation method | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C4-1 Relational fidelity | `.venv/bin/python evidence/query-native/R04/gen_evidence.py` (exit 0) + `tests/test_assertion_extractor.py` + `test_condition_graph_persisted_with_qualifier_edge`, `test_opposing_assertions_coexist_unmerged` | 25/25 role checks, accuracy 1.0; zero invented terms; opposing pair + ambiguity preserved | `evidence/query-native/R04/relation_fidelity.json` | bounded mechanism suite |
| C4-2 Provenance anchoring | same script (exit 0) + `test_provenance_every_relation_resolves`, `test_referential_integrity_rejects_orphan_edge` | 18/18 relations resolve via `excerpt_span`; 0 orphan/unanchored; FK rejects bogus endpoints | `evidence/query-native/R04/provenance_check.log` | per-run audit |
| C4-3 Condition retention | same script (exit 0) + `test_restores_probe_keeps_condition` | restores frame keeps `under high contention` + qualifies edge to the assertion | `evidence/query-native/R04/condition_test.json` | queue probe text |
| C4-4 Extraction fallback | same script battery (exit 0) + `test_fallback_malformed_inputs_never_raise`, `test_fallback_partial_run_persists_without_crash` | partial status, 0 unhandled exceptions; partial run persists + audits ok | `evidence/query-native/R04/fallback_test.log` | hostile battery, not power loss |
| Migration-003 additive | `test_migration_003_additive_and_rerunnable`, `test_migration_003_preserves_001_002_version_observations` | 6 tables added, version 1→2→3 chain intact, rerun safe, `.bak-003` written, pre-003 writers work | new test file | bounded fixture |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 155 passed (128 pre-existing + concurrent-R03 unmodified + 27 new) in ~20 s | session log | pre-existing Starlette warning only |
| New tests | `.venv/bin/python -m pytest tests/test_assertion_extractor.py tests/test_local_graph.py -q` (exit 0) | 27 passed | new test files | — |

## Frozen graph contract (for R05; schema-freeze note)

Tables (all `IF NOT EXISTS`; FKs enforced; `schema_version = 3` written only
by `migrate_graph_003`): `extraction_run(run_id, revision_id→source_revision,
view_id→segmentation_view, extractor_name/version, schema_version
'r04-graph-v1', config_hash, status complete|partial, error, span/
assertion/relation_count)`; `entity_mention(mention_id, run_id, revision_id,
span_id→evidence_span, start/end_byte, surface, role, canonical_key NULLABLE
provisional, mapping_version, status)`; `assertion(assertion_id, version=1,
run_id, revision_id, predicate, polarity, modality, epistemic_status,
subject/object/condition_text, units, temporal/version_scope,
span_id→evidence_span, start/end_byte, status)`; `assertion_argument
(argument_id, assertion_id→assertion, role, target_mention_id→entity_mention,
target_text, position)`; `relation(relation_id, run_id, revision_id,
rel_type, subject_mention_id|subject_assertion_id NULLABLE exclusive,
object_mention_id|object_assertion_id NULLABLE exclusive, subject/object_text,
polarity, condition_text, epistemic_status, span_id→evidence_span, start/end
_byte, status, candidates_json)`; `dependency(dependency_id, derived_kind,
derived_id, requires_kind, requires_id, dependency_kind)` + derived/requires
indexes. Local `rel_type` vocabulary: improves, degrades, restores, requires,
conflicts_with, compares, reports, allows, uses, dominates, defines,
qualifies, note — every value means "source text structurally asserts this".
R05 may ADD tables/columns but must not alter these six tables' columns,
must not reinterpret local types as validated entailment, must keep opposing
assertions unmerged and `unresolved` rows unbound without new lineage, and
must keep writing `dependency` rows for anything it derives.

## Independent findings

(none — builder role; verifier uses review.md)
