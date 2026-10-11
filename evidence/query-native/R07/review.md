# R07 — Independent review (B3)

Role: B3 independent verifier. Packet R07-dossiers.md, contract TL-QN-2026-09-12.1.
Candidate: git HEAD `0df044b` + R01–R07 integrated working tree.
Date (UTC): 2026-09-12. Interpreter: `.venv/bin/python` (3.11) from repo root.
Builder claims checked, not trusted. Probes in `/tmp` only; no repo files created
except this review.

## Verdict: PASS

All four criteria reproduce independently. No scope violation attributable to R07.
No honesty violation. Full suite green in this session.

## Evidence

### C7-1 Objective term alignment — PASS

- New tests (exit 0): `tests/test_context_compiler.py::test_objective_invariance_identical_ranking`
  uses 4 genuinely different ids (`goal-alpha`, `goal-beta`, `7f3a9c`, `totally/other/id`);
  `test_ranking_follows_objective_not_misleading_id` uses adversarial decoy id
  `sourdough hydration project` with batching objective → top is `batch-note`;
  `test_changing_objective_changes_ranking` is a negative control (same id, WAL vs
  sourdough objective reorders). Not tautological: ids carry misleading text and the
  control proves the objective actually drives ranking.
- Independent probe (`/tmp/r07_verify_probes.py`, exit 0): 5 ids incl. adversarial
  decoy and empty string → identical order, top `batch-note`; sourdough objective
  reorders to `sour-note`. P1_parity=true, P1_negcontrol_reorders=true.
- Builder artifact `evidence/query-native/R07/objective_align.json`: 4 goal_ids with
  byte-identical ranking lists, parity 100%, negative control differs. Matches claim.

### C7-2 Cross-source dossier — PASS

- New test builds a REAL R02 pair (`S.publish` cond-a/cond-b + segmentation views),
  compiles, and asserts every selection `citation_valid`, method `r02_excerpt`, and
  `V.excerpt_span(conn, span_id) == excerpt bytes`. Citation oracle is R02, not self-report.
- Independent probe: 2 sources selected, 2/2 citations `r02_excerpt`-valid AND each
  excerpt verified as a byte sub-slice of the raw `source_revision.text` payload
  (independent resolution, not the compiler's word). P2_citations_verified_vs_payload=true.
- Virtual holon: exactly 1, kind `query_dossier`, status `derived`, with lineage;
  qualification edge retained. Snapshot `generation_id` carried.

### C7-3 Conflict retention — PASS

- Independent probe: `conflicts_with` relation → `support_state=conflicting`, selections
  exactly `{v-a, v-b}`, 1 disagreement with both sides named. P3 all true.
- Builder `conflict_dossier.log` present; test asserts distinct relevant/supported counts.

### C7-4 Mandatory budget guard — PASS

- Budget tests overflow with REAL mandatory material, not toy budgets: compiler side
  3 invariants of `"NEVER drop this invariant " * 60` (~600+ tokens each) vs reservation
  100; dossier side 3 × (`...` * 80) vs reservation 50. Both → `insufficient_budget`
  with every invariant named (`inv-0..2` / `m-0..2`), zero selections, zero silent drops.
- Independent probe accounting: 3 mandatory in, 3 named in omissions → 0 unaccounted.
  Atomic-group overflow → `coverage_failure` naming group G (test asserts).
- Off-corpus NL query → `insufficient`, `supported_count == 0` (probe P5 true; nothing fabricated).
- Namespace/access/version filter probe: only `ok-1` visible, SECRET/RESTRICTED/superseded
  text absent, coverage counts exact `{namespace:1, access:1, version:1}`; wipeout →
  coverage-gap reason, not absence claim.

### Honesty — PASS

- Support checker is NAMED `lexical-heuristic-v1` (`SUPPORT_METHOD`), checker note says
  "lexical overlap does not prove sufficiency; not NLI". No file markets NLI
  (grepped cordis + api; only "explicitly NOT NLI" occurrences).
- `TIER1_GATE_LABEL = "heuristic"`; `adapter_report` returns `schema_conformance=True`
  with `harness_loading_verified=False` — conformance separated from harness loading.
- `gate.py` zero diff vs HEAD (`git diff HEAD --stat` empty): Tier-1 authority semantics
  untouched; still deterministic `_contradicts` keyword screen.
- Synthesis always `derived` with lineage (probe P7 true); "memory provenance never
  authorizes execution" stated in dossier honesty block; no provenance→execute path found.
- Dossier carries snapshot+generation watermarks, candidate routes (`lexical`/`holon`
  observed), budget breakdown, and token accounting
  (`mandatory_cost`/`goal_cost`/`evidence_cost`/`total`, synthesis-derived).

### Execution scope

- New tests: 23 passed (exit 0). Full suite: 220 passed (exit 0), 1 pre-existing
  Starlette deprecation warning only. Matches builder's 197+23 claim.
- R07-attributable changes are within allowance: `cordis/dossier.py` (new),
  `cordis/compiler.py` (objective ranking + explicit overflow + anchored excerpts),
  `cordis/models.py` (defaulted additive fields only), `cordis/__init__.py`
  (additive exports), `api/app.py` hunks limited to `DossierRequest` model +
  `POST /api/dossier` read-only route over `route_pinned`/`freshness` (route bodies
  not modified; honest `lexical|beam|hybrid` tier labels, never `holon`), 2 new test
  files, `evidence/query-native/R07/` (delivery, readiness, gen_evidence.py,
  objective_align.json, cross_dossier.json, conflict_dossier.log, budget_guard.log).

## Findings

1. (Scope note, not a violation) `git status --short` / `git diff HEAD --stat` show a
   large uncommitted tree (filing, store, router, obsidian, docs, other tests) because
   the candidate is HEAD + R01–R07 integrated and nothing is committed per-packet.
   R07 cannot be separated by commit; attribution above is by hunk inspection. R07
   hunks touch nothing outside the allowed set; `gate.py` untouched.
2. (Known limitation, builder-declared, confirmed) Support thresholds
   (`relevance 0.05`, `support_coverage 0.5`) are frozen engineering defaults for R09
   calibration, not validated mandates. Ranking is lexical+trigram; no learned encoder.
   No live LLM calls. API-path `atom_self` citations are self-consistent by
   construction; R02-span citations are the proven path.
3. (Minor) Minimal single-candidate probe returned empty `candidate_routes` mapping
   while multi-route dossiers record routes correctly; cosmetic only, routes verified
   present in P2/API paths.

## Next action

R07 may proceed to R08 (reconciliation/holons) on the frozen dossier contract in
delivery.md §Frozen dossier contract. R09 must calibrate the support thresholds and
run model-judge checks; quantitative qualification remains INCONCLUSIVE per
EVALUATION.md until R00 workload/gates exist.
