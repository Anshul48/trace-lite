# R08 — Independent verification review (B3 verifier)

Role: R08 experiment verifier. Date: 2026-09-12.
Contract: TL-QN-2026-09-12.1. Packet: R08-prediction.
Base commit observed: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` (matches delivery).
Method: reran the ablation pipeline independently to `/tmp/r08-verify`
(scifact DEV+TEST full pipeline, exit 0); inspected runner, signals,
repair fixtures, tests; ran `tests/test_prediction_error.py` and the full
suite with `.venv/bin/python`. Modified file: this review only.

## Verdict: PASS — concur with PARK

The experiment is sound and the PARK is honest. Negative deltas reproduce
exactly, CIs exclude the +0.03 bar on every corpus, severance and cost
grounds are independently confirmed, and the repair probe demonstrates the
exact EVALUATION §6 failure mode with all thresholds reported. No
exonerating flaw found: nothing was tuned on test numbers, methods are
labeled as heuristics (never "learned"), and variants were tested
separately.

## Evidence

### C8-1 Controlled ablation — CONFIRMED (rerun to /tmp, exit 0)

Independent scifact rerun reproduces the claimed numbers bit-for-bit
(seed-deterministic pipeline), so bootstrap noise is zero, not merely small:

| Split | Variant delta | Claimed | Rerun (/tmp/r08-verify) |
|---|---|---|---|
| DEV (150) | surprise | −0.0440 [−0.0791, −0.0095] p=0.012 | identical |
| DEV (150) | shift | −0.0557 [−0.0883, −0.0245] p=0.000 | identical |
| TEST (300) | surprise | −0.0295 [−0.0546, −0.0040] p=0.026 | identical |
| TEST (300) | shift | −0.0163 [−0.0416, +0.0095] p=0.205 | identical |

Structural absolute (0.5471 DEV / 0.4968 TEST) also identical. No corpus
shows a positive mean for either candidate, let alone +0.03 with CI above
zero; 4/6 test deltas across corpora are CI-negative per the filed JSON.
Bootstrap machinery cross-checked with an independent numpy percentile
implementation on synthetic data: agreement within 0.0001 (differing RNG
streams). Variants tested SEPARATELY: the runner builds one index per
method (`structural` / `surprise` / `shift`) with identical retrieval; the
only "combined" string in code is the `"separate candidates only; no
combined smart variant"` design note. No combined/smart variant exists.

### C8-2 Qualifier preservation — CONFIRMED (reproduced)

Rerun severance counts match exactly: +surprise 12/12, +shift 10/12,
structural 0/12 (12 fixtures, single-structural-span precondition asserted
both in-run and in `test_qualifier_fixtures_are_single_structural_spans`).
Promotion requires zero severance; both candidates fail independently of C8-1.

### C8-3 Cost accounting — CONFIRMED, method sane

Rerun per-source means: structural 0.25 ms, surprise 19.4 ms, shift
5.7 ms — same order as claimed (0.24 / 19.3 / 5.9 ms; timing-only
differences expected). Method: `time.process_time` per source over a
200-doc scifact sample + tracemalloc peak + `ru_maxrss`, all disclosed in
`cost_telemetry.txt`. Surprise fails the ≤10 ms promotion ceiling outright
(mean and p95); shift p95 (10.6 ms claimed) is borderline over. Minor note:
`ru_maxrss` is process-wide max so it is identical across methods (211712)
— weak as a delta metric, but disclosed raw, not oversold.

### C8-4 Disposition — CONCUR: PARK

Three independent PARK grounds (negative/sig.-negative deltas vs +0.03 bar,
nonzero severance, cost-ceiling breach for surprise) each suffice alone.
The memo's numbers match the JSON exactly.

### Repair half (§6) — PRESENT and PARK-decisive; thin vs §6 letter (see F3)

The repair probe ran inside the same run (exit 0) and reproduces exactly:
deterministic closure misses 0/5 meaningful (4 false invalidations);
predictor at θ=0.02/0.05/0.10 misses 0/1/3 meaningful with touched
fractions 0.857/0.714/0.429. No threshold both saves work and catches the
negation/actor/number micro-edits — the §6 failure mode, confirmed with all
three a-priori thetas reported and none selected. This answers the §6
hypothesis question (cost reduction at frozen accuracy: NO) with the
decisive deterministic-vs-predictive contrast, so it supports PARK.
Gaps vs the full §6 text are recorded as F3, binding on any revival.

### Tests

- `tests/test_prediction_error.py`: 17/17 passed (exit 0), rerun by verifier.
- Full suite: 237 passed + 3 failed in
  `tests/test_write_path_integration.py` (`atoms==2` on the delete step).
  These 3 tests are NOT R08's: that file imports nothing from
  `experimental`/`boundary_signals`/`trace_lite.source`, no production
  module imports `experimental/`, and the failure is write-path/delete
  behavior in INT-1's concurrent area. The delivery's "237/237 green" was
  true for its tree (220+17); the 3 failures arrived with concurrently
  added INT-1 tests. Not an R08 regression. Flagged to coordinator/INT-1,
  not held against R08.

## Split hygiene — CLEAN

- Thresholds: per-document adaptive mean+0.5σ, max 2 cuts/span, 120-char
  floor — hardcoded in `segment.py`, identical for both signals, no
  qrel tuning possible. Repair thetas (0.02/0.05/0.10) all reported.
- Seeds: bootstrap seed 42 fixed; paired deltas use identical draws.
- DEV/test identities: DEV = train.tsv sorted-prefix qids (150/100/60);
  test.tsv scored exactly once in a single post-DEV pass with no selection
  logic on test numbers (code-reviewed control flow). No seed hacking, no
  variant selection on test.
- Promotion bar: +0.03 at ≤10 ms / PARK-if-insignificant-or->50 ms matches
  the committed packet (`c5565b1`) — no post-hoc revision. Runner applies
  it conservatively (requires CI above zero, not just point ≥ +0.03).
- Prior exposure: none evidenced; bench data consumed read-only from the
  pinned path. B1-equivalence gap (harness-relative absolutes, paired
  deltas only) is disclosed in delivery §"readiness gap note".

## Labels — CORRECT

No executed method is called "learned": signals are named
`ngram-surprise-trigram` / `hashbow-shift-cos`; docstrings state "no
learned weights"; delivery states "no learned embeddings (no network)";
the repair policy is labeled "residual-threshold predictor", and the memo
lists a learned space only as future work that could reopen the question.
The packet's "learned selective repair" phrasing is packet language, not
an experimenter claim.

## Findings

- F1 (info): Packet says "held-out split from workload.json (frozen in
  R00)" but the run uses BEIR train/test.tsv splits. Sanctioned by
  EVALUATION §3 (frozen public corpora) and honestly recorded; packet text
  is stale, not the experiment.
- F2 (info): `ru_maxrss` reported per method is a process-wide max
  (identical values) — harmless, disclosed raw.
- F3 (binding thinness, PARK still stands): repair probe is a 7-edit
  miniature covering deterministic vs predictive with missed/false/touched
  only. Missing vs §6 letter: full-rebuild arm, cached lexical/vector
  refresh arm, elapsed/token cost, long-update-sequence accumulation, and
  post-repair query accuracy. These arms could only matter for a PROMOTE
  claim, which is not made — but any revival MUST complete them.
- F4 (not R08): 3 `test_write_path_integration.py` failures from
  concurrent INT-1 work (see Tests). R08 is causally isolated (parked
  package, zero prod importers).
- F5 (scope note): `src/trace_lite/source/` is a new directory shared with
  concurrent INT-1 files (`service.py`, `project.py`, `views.py`,
  `segmentation.py`). R08's footprint inside it is `boundary_signals.py`
  plus the `boundary_signals` export in `__init__.py`; the `signal*` hits
  in `views.py`/`segmentation.py` are unrelated pre-existing
  (`signal_json`, byte-shift) language. No shared behavior touched.

## Scope

R08 touches only: `src/trace_lite/experimental/` (new, 6 modules),
`src/trace_lite/source/boundary_signals.py` (new) + additive export,
`tests/test_prediction_error.py` (new, 17 tests),
`evidence/query-native/R08/` (6 files). Verifier modified only this
review. INT-1 files untouched; full-suite runs read-only.

## Next action

R09 proceeds on the qualified structural baseline with
`src/trace_lite/experimental/` parked and unreferenced (concur with memo).
If R08 is ever revived: qualifier-constrained signals + a truly learned
(non-hashbow) space + full §6 repair arms (rebuild, cached refresh,
sequence accumulation, elapsed cost) rerun under this exact paired
protocol with test.tsv still frozen.
