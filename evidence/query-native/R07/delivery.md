# R07 — Delivery (B2)

Record type: builder delivery
Author role and date: R07 builder (B1 readiness + B2 build) / 2026-09-12
Packet and contract revision: R07-dossiers.md / TL-QN-2026-09-12.1
Base/candidate identity: `0df044b` + R01–R06 INTEGRATED (197/197 green at B1)
+ R07 owned files below. No other writer active.
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5,
scratch/evidence DBs under /tmp ext4.
Interpreter: `.venv/bin/python` from repo root (contract).

## Delivery section

- Behavior/artifact changed and why: query-generated evidence dossiers +
  honest Cordis adapter (C7-1..C7-4). Ranking now follows objective TEXT
  (`goal_state['objective']`), never the opaque `goal_id`; dossiers carry
  query/objective/scope, snapshot+generation watermarks, byte-verified
  evidence selections, relations+status, omissions, support states,
  candidate routes, budgets, and consumer-tokenizer accounting; mandatory
  overflow and atomic-group overflow are explicit failures with zero silent
  drops; cross-source virtual holons assemble transiently at query time
  with derived status + lineage; the adapter reports schema conformance
  separately from harness loading and keeps the Tier-1 gate labeled
  heuristic.
- Files changed (owned set ONLY):
  - `src/trace_lite/cordis/dossier.py` (new: `EvidenceSelection`,
    `VirtualHolon`, `EvidenceDossier`, `DossierStatus`, `SupportState`,
    `compile_dossier`, `objective_relevance`/`rank_candidates`,
    `verify_citation`, `anchored_excerpt`, `adapter_report`;
    `SUPPORT_METHOD = "lexical-heuristic-v1"` named checker, explicitly
    not NLI; `TIER1_GATE_LABEL = "heuristic"`)
  - `src/trace_lite/cordis/compiler.py` (fix: `compile_context` gains
    `goal_state`; ranking uses objective text with legacy goal_id-as-text
    fallback; mandatory overflow → `status='insufficient_budget'` naming
    every omitted invariant; arbitrary `content[:cost*4]` prefix replaced
    by query-anchored excerpts with byte coordinates)
  - `src/trace_lite/cordis/models.py` (additive ONLY: defaulted
    `status`/`omitted_invariant_ids`/`goal_objective` on
    `CompiledContext`; defaulted `excerpt_start_byte`/`excerpt_end_byte`/
    `is_excerpt`/`selection_reason` on `EvidencePacket`; formal-spec
    conformance cases unaffected — neither model is in the case map)
  - `src/trace_lite/cordis/__init__.py` (additive exports only, incl.
    `estimate_tokens` re-export)
  - `src/trace_lite/api/app.py` (additive ONLY: `DossierRequest` model +
    `POST /api/dossier` read-only route over `route_pinned`/`freshness`;
    no writer/lock/WAL/generation/status body touched — router tier
    labeled honestly `lexical|beam|hybrid`, never `holon`)
  - `tests/test_context_compiler.py` (new, 10 tests),
    `tests/test_dossier_compilation.py` (new, 13 tests); all pre-existing
    tests unmodified
  - `evidence/query-native/R07/{readiness.md,delivery.md,gen_evidence.py,
    objective_align.json,cross_dossier.json,conflict_dossier.log,
    budget_guard.log}` (this packet; generator retained for re-verification)
- Material implementation decisions:
  - Zero-reservation budgets mean "remainder under the ceiling" (mandatory
    AND salient): `TokenBudget()` defaults carry no explicit partition, so
    pre-R07 callers (`test_cordis.py`, `test_e2e.py` gate paths) keep
    working with invariants riding along — pinned by
    `test_default_budget_carries_invariants_legacy_contract`. Explicit
    positive reservations are enforced strictly (C7-4 probes use them).
    First full-suite run caught this contract (2 failures); fixed, not
    worked around — no pre-existing test touched.
  - Union semantics with honest scores: holon-route candidates are
    retained even at ~zero lexical score (the zero-word-overlap
    paraphrase path), score stays low, route recorded. Lexical-only
    off-corpus queries still resolve to `insufficient` with
    `supported_count == 0` — nothing fabricated.
  - Citation ladder recorded per selection: `r02_excerpt` (hash-verified
    via R02 `excerpt_span`) > `source_slice` (payload byte slice) >
    `atom_self` (self-consistent bounds, weakest, labeled). Excerpts of
    R02 spans verify as byte sub-slices of the span. Invalid citations
    become named `citation_invalid` omissions, never inclusions.
  - `relevant_candidates` (admitted by relevance/route) and `supported`
    (judged by the named heuristic) are separate fields; conflict
    relations force `conflicting` with both sides retained plus an
    explicit `disagreements` section and carried `qualifies` edges.
  - Token accounting: `total = system + mandatory + goal + evidence`;
    every evidence cost includes its citation wrapper + relation
    metadata via the consumer tokenizer (`len//4`).
  - Synthesis (API currently sends none; compiler accepts optional text)
    is stored `derived` with lineage — never ground truth. Memory
    provenance never authorizes execution (stated in every honesty
    block, not just docs).
- Known limitations and checks not run: support/heuristic thresholds
  (`relevance_threshold=0.05`, `support_coverage=0.5`) are engineering
  defaults frozen here for R09 calibration, not user mandates; ranking is
  lexical+trigram (no learned encoder — R03 hybrid stays the vector
  path, dossier consumes its routes); `atom_self` citations on the API
  path are self-consistent by construction (atom rows carry their own
  bounds) — R02-span citations are proven in tests/evidence, API-row
  provenance join to `source_revision` namespaces is future work (API
  scope filters default to `default` and say so); no live LLM calls
  anywhere (model-judge checks are R09); bounded fixtures only.

## Evidence

| Criterion | Command or evaluation method (exit) | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C7-1 Objective term alignment | `.venv/bin/python evidence/query-native/R07/gen_evidence.py` (exit 0) + `test_objective_invariance_identical_ranking`, `test_ranking_follows_objective_not_misleading_id`, `test_changing_objective_changes_ranking` | 4 goal_ids → byte-identical rankings, 100% parity; negative control (WAL objective) reorders | `evidence/query-native/R07/objective_align.json` | bounded 4-candidate probe |
| C7-2 Cross-source dossier | same script (exit 0) + `test_cross_source_dossier_exact_byte_citations` | cond-a + cond-b spans selected, 2/2 citations `r02_excerpt`-resolved (rate 1.0), 1 transient `query_dossier` virtual holon (derived + lineage), qualification edge retained | `evidence/query-native/R07/cross_dossier.json` | bounded R00 conditions pair |
| C7-3 Conflict retention | same script (exit 0) + `test_conflict_retention_both_sides_coexist` | `support_state=conflicting`, both sides selected, 1 explicit disagreement, relevant/supported reported distinctly | `evidence/query-native/R07/conflict_dossier.log` | batching/contention fixture |
| C7-4 Mandatory budget guard | same script (exit 0) + `test_mandatory_overflow_explicit_insufficient_budget`, `test_mandatory_overflow_is_explicit_never_silent` | `insufficient_budget`, 3/3 invariants named, 0 selections, 0 silent drops | `evidence/query-native/R07/budget_guard.log` | explicit-reservation budgets |
| C7-4 Coverage failure | same script (exit 0) + `test_atomic_group_overflow_is_coverage_failure` | atomic group G → `coverage_failure` naming G (excerpts, not prefixes) | `evidence/query-native/R07/budget_guard.log` | bounded oversized group |
| Paraphrase counterexample | `test_paraphrase_no_lexical_overlap_retrievable_via_holon` | zero-shared-word paraphrase retained via holon route, route recorded | new test file | single probe |
| Off-corpus counterexample | `test_off_corpus_query_is_insufficient_never_fabricated` | `insufficient`, `supported_count == 0` | new test file | single probe |
| Scope/filter honesty | `test_scope_filters_apply_before_reveal_with_coverage_report`, `test_filter_wipeout_reports_coverage_gap_not_absence` | filtered text absent, counts exact, wipeout → coverage-gap reason | new test file | candidate-level filters |
| Adapter honesty | `test_adapter_honesty_conformance_is_not_harness_loading`, `test_synthesis_always_derived_with_lineage` | conformance True / harness False, gate `heuristic`, synthesis `derived` | new test files | labels, not runtime proof |
| API additive route | `test_api_dossier_route_end_to_end` | 2-note vault → dossier with generation snapshot, valid citations, honesty blocks | new test file | TestClient, bounded vault |
| Full suite (no regressions) | `.venv/bin/python -m pytest tests/ -q` (exit 0) | 220 passed (197 pre-existing + 23 new), 1 pre-existing warning | session log | pre-existing Starlette warning only |
| New tests | `.venv/bin/python -m pytest tests/test_context_compiler.py tests/test_dossier_compilation.py -q` (exit 0) | 23 passed | new test files | — |

## Frozen dossier contract (for R08/R09; freeze note)

`compile_dossier(query, objective, goal_id-opaque, scope{namespaces,
access_scopes, current_only}, candidates, relations, disagreements,
mandatory, budget, snapshot{generation_id, watermark}, synthesis+lineage)`
→ `EvidenceDossier(status ok|insufficient_budget|coverage_failure,
support_state supported|partial|conflicting|insufficient,
selections[EvidenceSelection{candidate_id, doc/source/revision/span ids,
start/end bytes, excerpt+hash, citation_valid+method, is_excerpt, route,
relevance_score, reason, token_cost, relations}], virtual_holons
(query_dossier, derived, lineage), relations, disagreements, omissions
(everything excluded is named), relevant/supported counts (distinct),
candidate_routes, coverage_report{pre/post/filtered_out/method/gap},
budget breakdown, token_accounting{mandatory/goal/evidence/total +
synthesis-derived}, checker{method=lexical-heuristic-v1, not-NLI note},
adapter_honesty{conformance≠harness, tier1=heuristic,
provenance≠authorization, synthesis=derived})`. R08/R09 may ADD optional
fields but must not re-point ranking at `goal_id`, must not silently drop
(omissions stay exhaustive), must not market the heuristic as NLI, and
must keep synthesis derived. Zero-reservation = remainder rule is part of
the contract (legacy default budgets fit under the ceiling).

## Independent findings

(none — builder role; verifier uses review.md)
