# S5 Prove semantic value — delivery (2026-09-12)

Contract: TL-QN-2026-09-12.1 + STEERING S5 row (line 76) + line-87
methodology + SUPPLEMENT B2. Interpreter: `.venv/bin/python`.

## Cheap-structure definition (frozen BEFORE the trial)

`src/trace_lite/obsidian/structure.py::CHEAP_STRUCTURE_DEF`, version
`v1`, recorded verbatim in every trial result (`s5_trial.json`):

- structural markdown hierarchy: sections, lists, callouts, tables
  (atomic), code fences (atomic), wikilinks — all byte-exact spans;
- entity co-occurrence clusters: wikilink targets, tags, capitalized
  phrases co-occurring inside ONE structural block, with byte-anchored
  mention spans (singleton bare words excluded until repeated).

Explicitly excluded: open-domain verb-triple regexes (they are the
floor arm to beat, not structure) and a per-sentence local SLM (not
proposed — no false regex-vs-SLM dichotomy, per B2). No verb patterns,
no triples, no model calls anywhere in the module.

## Arm design (matched, same corpus / same top-K=3)

10-note synthetic corpus (digest `b59c95df2ad79d04`), 10 questions, 2
per prescribed category (beyond-top-K, conditional disagreement,
quantities, actor reversal, source updates). Corpus embeds two
lexical decoy notes so beyond-top-K gold is genuinely outside flat
top-3, a paraphrased wikilink-alias note, a superseded/current note
pair with explicit `Status:` syntax, and two distinctly qualified
`Chen (… team)` reporters.

- **flat**: lexical overlap + hashed-BoW dense cosine, RRF top-3.
- **structural**: flat seeds (stale-filtered) + cheap-structure
  expansion — same-section mates, shared-wikilink targets, cluster
  overlap — bounded (3/seed, 8 total), link/cluster candidates ranked
  by query overlap, every addition reason-coded.
- **graph**: structural seeds + selective evidence-linked groups
  (cross-block entity groups with explicit-syntax roles:
  counterevidence / qualification / premise). Costly groups
  materialize only via `PromotionLedger`: promoted iff used by ≥2
  distinct questions AND useful ≥ once. Round 2 resolves promoted
  groups by lookup, scans only the unpromoted remainder.
- **interpret**: bounded (≤8 sentences) query-time rules over the
  structural pool — actor (`Name (qual) verb`), quantity (NUMBER UNIT
  verbatim, condition-carrying citations preferred), disagreement
  (both sides + conditions), current-version (`Status: current`).
  Interrogatives never answer; pronoun subjects, bare ambiguous names,
  and marker-less sentences abstain with reason codes.

## Measured table (`run.log`, `s5_trial.json`)

| arm | comp | qual-ret | miss-CE | unsup-rel | correct | stale | edges | groups | sents | rels |
|---|---|---|---|---|---|---|---|---|---|---|
| flat | 0.8500 | 0.8500 | 0.6667 | 0.0000 | 0.6000 | 0.2000 | 0 | 0 | 0 | 0 |
| structural | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 1363 | 0 | 0 | 0 |
| graph | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 1.0000 | 0.0000 | 50 | 40 | 0 | 50 |
| interpret | 0.9500 | 1.0000 | 0.3333 | 0.0000 | 1.0000 | 0.0000 | 0 | 0 | 80 | 0 |

Flat fails Q1 (beyond-top-K, comp 0.0), Q3 (drops counterevidence),
Q9/Q10 (serves the superseded note). Utility is measured as
unsupported-relation rate (0.0 for every structure arm; a bare-`Chen`
merge control proves the metric fires via abstention) and
missing-counterevidence — not relation counts.

## Verdict per arm (computed in code from the table, not asserted)

- **structural: adopt.** Completeness 0.85 → 1.0, CE-miss 0.667 → 0,
  stale 0.2 → 0, at correctness 1.0 ≥ 0.6. Cost: 1363 candidate-walks.
- **graph: adopt.** Same quality as structural with role-prioritized
  counterevidence surfacing; 50 supported byte-anchored relations, 0
  unsupported.
- **selective-promotion: adopt.** Only `group:Batching` promoted
  (reused ×4 questions, useful ×2; single-use/never-useful groups
  correctly held back). Round 2: 40 → 36 index scans at identical
  completeness (10.0 = 10.0). Small honest win.
- **interpret: adopt-bounded.** Exact answers (40ms/400ms/64 pages/10x,
  both actors, both conflicts) at retention 1.0, 0 unsupported, 80
  sentences, abstains on all 3 uncertain probes. NOT a replacement:
  its minimal-evidence exactness misses counterevidence on Q7
  (CE-miss 0.333) — it stays an optional layer over the structural
  pool, which carries the counterevidence.

Q2 is a documented non-differentiating control (short distinctive
gold block the baseline happens to hit); expansion degrades nothing
there. ms column is informational only — budgets are enforced in
operation counts (edge/group/sentence caps), never wall time.

## Verification

- `tests/test_s5_semantics.py` (17): definition freeze, byte-exactness,
  category coverage, beyond-top-K miss/recover, expansion bounds +
  reasons, stale exclusion, counterevidence retention, exact quantities,
  actor attribution + merge control, 3 abstention probes, sentence
  budget, promotion selectivity, round-2 work reduction, verdict gates,
  relation anchor slicing, artifact schema.
- `run.log`: table + 17 passed. Full suite: **305 passed, 6 skipped**
  (288 + 17 new; same 6 pre-existing skips), zero regressions.

## Owned files touched

`obsidian/structure.py` (new), `obsidian/semantics.py` (new),
`obsidian/__init__.py` (additive re-export only),
`tests/test_s5_semantics.py` (new), `evidence/…/S5/**`. Nothing else —
`filing/*`, `graph/*`, `router/*`, `cordis/*`, `experimental/*`
untouched (read-only imports only). The `parser.py` working-tree diff
is a sibling builder's CRLF change, not mine.

## Gates at risk

None. No gate weakened: S0/S1/S2/S3/S4/R09 evidence and suites
untouched and green; no restarts; no mock PASS. R08 predictive work
stays parked (no prediction/segmentation/repair code touched or added).
Follow-ups owned elsewhere: real-corpus replication of the trial,
promotion-ledger persistence across queries, tuning per-seed/total caps
(explicitly untuned constants here).
