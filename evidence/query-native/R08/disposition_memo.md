# R08 — Disposition memo: PARK (experimental, not promoted)

Date: 2026-09-12. Contract TL-QN-2026-09-12.1, packet R08-prediction.
Base: `0df044b51fb3b40ca3360ea7abd78ac36924b9f8` + R01–R07 integrated.
Author role: R08 experimenter. Kind: experiment — this PARK is a success.

## Verdict: PARK

Neither predictive candidate clears the promotion bar (nDCG ≥ +0.03 over the
structural baseline at ≤ 10 ms CPU per source, with zero severed qualifier
conditions). The evidence fails the bar on three independent grounds, so
there is no close call to adjudicate.

## Numbers cited (paired ablation, same splits, same retrieval)

Frozen single-run TEST scoring (`test.tsv`, never iterated; thresholds were
per-document adaptive, so nothing was selected on any qrels):

| Corpus (test n) | +surprise ΔnDCG [95% CI] | +shift ΔnDCG [95% CI] |
|---|---|---|
| scifact (300) | −0.0295 [−0.0546, −0.0040], p=0.026 | −0.0163 [−0.0416, +0.0095], p=0.205 |
| nfcorpus (323) | −0.0144 [−0.0261, −0.0030], p=0.013 | −0.0171 [−0.0302, −0.0044], p=0.009 |
| fiqa (648) | −0.0280 [−0.0393, −0.0167], p<0.001 | −0.0289 [−0.0411, −0.0168], p<0.001 |

DEV (train splits) agrees in sign everywhere: scifact-train −0.0440/−0.0557
(both CI-negative), nfcorpus-train and fiqa-train neutral-to-negative.
Synthetic DEV diagnostic: structural 0.50, +surprise 0.50, +shift 0.375
hit@3 (8 queries — diagnostic only, not a claim).

No corpus shows a positive mean for either candidate, let alone +0.03 with
a CI above zero. Four of six test deltas are significantly *negative*.
Sentence-splitting paragraphs measurably harms this BM25-led hybrid:
shorter spans dilute term-frequency/length-norm signals while adding no
compensating precision.

## C8-2 qualifier preservation (promotion requires zero severance)

+surprise severed 12/12 qualifier fixtures; +shift severed 10/12
(negation, unless/only-if, contention, actor, number kinds). The structural
baseline severs 0/12. Predictive sentence cuts split antecedent conditions
from their claims — exactly the failure EVALUATION §6 warns against.

## C8-3 cost (per source, 200-doc sample, `time.process_time`)

structural: mean 0.24 ms, p95 0.47 ms. +surprise: mean 19.3 ms,
p95 31.2 ms (fails the ≤10 ms promotion ceiling outright).
+shift: mean 5.9 ms, p95 10.6 ms (borderline over the ceiling).
Memory deltas are negligible (tracemalloc peak 25/137/41 KB).

## §6 selective repair (deterministic closure vs residual predictor)

Deterministic R06-style closure: 0 missed meaningful changes (7 edits),
4 false invalidations (conservative over-invalidation, the known price).
Residual predictor: at θ=0.02 it misses nothing but touches 6/7 edits
(no savings vs deterministic); at θ=0.05 it misses the negation micro-edit
(residual 0.047); at θ=0.10 it additionally misses actor-reversal and the
number/unit change (3 missed meaningful, touched 3/7). No threshold both
saves work and catches meaning micro-changes — the §6 failure mode is
confirmed: a predictor that avoids work misses negations. Deterministic
invalidation stays.

## What could reopen this

Qualifier-constrained signals (never cut inside a detected condition
span), a learned (non-hashbow) space where shift deltas carry real
semantics, or a repair predictor keyed on semantic diffs rather than
embedding residuals. Any revival reruns this exact paired protocol from
DEV — test.tsv stays frozen.

## Instruction to R09

Proceed on the qualified structural baseline. Leave
`src/trace_lite/experimental/` parked and unreferenced by production paths.
