# Praxist Mandate Pre-Spec (draft, coordinator-owned)

Date: 2026-09-12. Status: AUTHORING IN PARALLEL (S2/S3). Full `praxist-task-init` runs only post-S4 commit+push.

## Precondition check (per praxist-task-initialization skill)

1. Source code: YES — trace-lite worktree, candidate pinned in S0 manifest.
2. Runs locally: YES — WSL `.venv`, S1 native-Linux run 20/20 GREEN.
3. Data/fixtures: YES — BEIR subsets + blackbox harness in-repo.
4. BLOCKER for full init: candidate not yet committed/pushed post-S4. This file is the draft mandate only.

## Locked synthesis targets (user-authorized)

- (A) S3/S4: non-blocking background queue + lock-free generation-swap architecture.
- (B) S5: cheap semantic-structure extractor replacing dead 14-verb regexes with high-yield syntactic parsing.
- NON-GOAL: S4 parameter tuning (batch sizes, drain limits = trivial grid search, never Praxist).

## Evaluator concept (fast harness, S2-gated)

- Mature protocol: `benchmarks/blackbox_native_check.py` (native server lifecycle: ingest/query/edit/replay,
  restart, stale-worker, crash recovery, audit, sustained load) + `benchmarks/run_integrated_qualification.py`
  (2k/10k integrated gates incl. dossier wording, C9-3/C9-4).
- Staged protocol: smoke = blackbox subset (lifecycle only); mature = full blackbox + 2k qualification.
  10k ladder stays diagnostic (R09 FAIL preserved), not a promotion gate.
- S2 exit criterion MET (verified 2026-09-12): query-path freshness 6.5s → ~1ms at 10k pending
  (3407x, test-enforced <1.0s budget; evidence/.../_integration/S2/). Per-tick freshness cost is now
  ~1ms, leaving headroom for 10x growth inside a sub-minute eval loop. Praxist launch still gated on
  post-S4 commit+push, not on evaluator speed.

## Metric families (draft)

- Correctness gates (must-pass, direction: pass-rate maximize): lifecycle checks, restart/crash/audit checks.
- Quality (maximize): dossier citation precision, retrieval quality deltas vs frozen S0 baseline.
- Cost/latency (minimize): p95 query latency, eval-loop wall time, index-build time.
- Target-specific: (A) zero query-blocking during background drain (binary gate + blocked-ms metric);
  (B) extractor yield (relations+spans per doc) vs regex baseline at fixed precision.
- Ranking: raw gate pass first, then quality; cost/latency as incubator Pareto axes.

## Diversity axes (draft)

- Mechanism family: queue design, swap protocol, extractor formalism.
- Intervention surface: store layer, filing engine, obsidian parser, API app.
- Intent: latency elimination, yield gain, simplicity/revert-risk reduction.

## Lanes sketch

- confirmed: mature-protocol pass + quality ≥ baseline, strict.
- incubator: Pareto/new-high on any axis family (latency, yield, quality), protocol-passed, needs repair/validation.
- task_candidate: smoke-only or partial evidence. diagnostic: falsifiers, ablations, negative evidence.

## Next step

Post-S4 push: run full task-init (inventory script, resource plan from measured eval times, baseline records
from S0/S2 evidence, `praxist resolve`), then launch (A); (B) follows once S5 scaffolding exists.
