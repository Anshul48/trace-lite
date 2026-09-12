# Remediation Plan — Audit FAIL → 1M Scale-Up

> **Superseded for future TL work — TL-QN-2026-09-12.1.** Read the [current project](../query-native/PROJECT.md), [execution plan](../query-native/EXECUTION.md), and [state](../query-native/STATE.md). The content below is historical context, including its status and authority claims. Do not rerun the reset or launch TRACE from these instructions. Existing delivery/evidence records remain preserved.

Revision: 2026-09-11.3 | Status: PLANNED → self-critiqued → refined (see §7)

## 1. Repro status (all confirmed live, not trusted blindly)

| ID | Claim | Evidence |
|---|---|---|
| F1 | FTS delete corrupts index | probe: post-delete search → `DatabaseError: malformed` |
| F2 | Wire-protocol mismatch | main.ts:262 (`query_text/top_k/mode`), :578 (`/api/ingest`), :333 (`item.atom.content`), :222/:492 (`total_atoms`) |
| F3 | Beam starvation | probe: 10/10 hits from Big facet, 0 from Small |
| F4 | Watcher orphaned | grep: `DebouncedWatcher(` only in tests |
| F5 | CLI gaps | cli.py has status/version/serve only; `status` static |
| F6 | Spec-dump rejected | probe: 6× extra_forbidden on full `TraceEvent` dump |

## 2. Remediation design

- **F1**: `delete_atom` reads `text` first, issues `('delete', rowid, old_text)`. Add
  `test_fts_delete_no_corruption`: insert → delete → search deleted tokens (expect []) →
  search live tokens (expect hits) → `PRAGMA integrity_check` ok. Also cover `sync_vault`
  re-atomization path (it calls `delete_atom` per note).
- **F2**: dual-compat API, server side only (plugin untouched):
  - `POST /api/ingest {text, document_name}` → parse note, replace atoms for doc, facet-map,
    return `{ok, atom_id/doc_id, atoms}`.
  - `POST /api/query` accepts `{query|query_text, limit|top_k, mode?}`; response includes
    BOTH envelopes: legacy `{anchors, tier_used, elapsed_ms, sufficiency_state}` AND plugin
    `{query_text, mode, total_results, items: [{atom_id, score, source_artifact, atom: {atom_id, content}}]}`.
  - `GET /api/status` adds `total_atoms, total_trees, index_trusted, needs_organization,
    pending_atoms` alongside existing keys.
- **F3**: apportion `CANDIDATE_CAP` per facet: `per = max(1, CAP // beam_width)` from each of
  the top-`beam_width` facets (round-robin fill to CAP). Probe asserts Small-facet recall > 0.
- **F4**: lifespan starts `DebouncedWatcher(vault, on_sync=...)` when vault configured;
  stops on shutdown. Sync callback reuses `sync_vault` + router re-warm under the guard lock.
  Test with short debounce via direct `DebouncedWatcher` + a lifespan integration test.
- **F5**: `tl ingest <path> [--db]` (file or vault dir → sync_vault/report) and live
  `tl status` (atom/facet/event counts, WAL frames, DB size). Tests via CliRunner.
- **F6**: add missing optional spec fields to OUR models (keep `extra="forbid"` as a
  strictness asset): `TraceEvent` += event_offset/causation_id/correlation_id/schema_version/
  payload_hash/idempotency_key; `FacetedQuery` += as_of_event/allowed_visibility;
  `QueryResponse` += disagreements; `AtomRecord.content_hash` stays str (spec hex) — plus
  systematic full-dump acceptance test over every spec model with a same-named counterpart.

## 3. 1M scale-up ladder (after remediation + critic loop)

100k → 250k → 1M, each gate: ingest ≥1.2k/s, P95 ≤50ms, RSS ≤500MB, integrity_check ok.
Known risk: warmed dense matrix at 1M×128×f32 = 512MB > RSS budget → pre-planned
mitigations in order: (a) float16 matrix (256MB), (b) facet-sharded search (score only
top-facet members), (c) memmapped matrix. Research-team trigger: any gate red after (a).

## 4. Verification per fix

Repro probe re-run (must flip to PASS) + new durable tests + full suite + critic review.

## 7. Self-critique & dispositions

- C1: "Dual envelope doubles response size." Accepted cost; correctness > bytes on loopback.
  Mitigated: snippets already capped at 300 chars.
- C2: "F4 watcher + F2 ingest overlap." Both kept deliberately: watcher = local vault,
  ingest = remote push from Obsidian. Documented, not duplicated.
- C3: "F6 field-adding churns models." Additive-optional only; existing tests pin behavior.
- C4: "Beam apportioning dilutes top-facet precision." Ranking unchanged (cosine sort);
  only candidate sourcing widens. Precision preserved, recall fixed.
- C5: "1M ladder wastes time if (a) insufficient." float16 check is a 5-line probe run at
  100k first; ladder order follows evidence, not hope.
