# R00 — Baseline and frozen evaluation contract

Status: READY
Kind: artifact
Contract revision: TL-QN-2026-09-12.1
Owner/session: pending coordinator dispatch

## Outcome

Pin the real TL candidate, runtime, and existing baseline behavior. Establish a reproducible comparison before architectural changes occur.

## Inputs and dependencies

- Required prior packets: None; design is READY.
- Relevant contract sections: `PROJECT.md`, `ARCHITECTURE.md`, `EVALUATION.md`.
- Existing baseline: Current checkout `c8e6b701dbd86a412ffec228afde319e122d337e` with 39/39 passing unit tests.
- Missing facts and readiness checks: Confirm Python 3.11+ venv, SQLite FTS5 extension, and BEIR corpus paths.

## Scope and interfaces

- Owned scope: `benchmarks/`, `evidence/query-native/R00/`, `EVALUATION.md` target register.
- Shared surfaces: None (read-only baseline evaluation).
- Non-goals: Do not modify product code (`src/trace_lite/`) or invalidate historical evidence.

## Suggested approach

1. Record HEAD commit, diff, Python/SQLite build versions, FTS5 capabilities, filesystem, and artifact checksums.
2. Reproduce bounded existing quality and latency behavior in fresh output paths (`evidence/query-native/R00/`).
3. Construct test fixture families covering conditions, negation, entity ambiguity, unfiled evidence, and off-topic abstention.
4. Freeze corpus splits, metric definitions, regression margins, and resource envelopes in `workload.json` and `gates.json`.

## Acceptance

| Criterion | Observable outcome | Check and baseline | Required evidence | Limits |
|---|---|---|---|---|
| C0-1 Baseline reproduction | Saved scores re-verified or discrepancies documented | Run BEIR adapter & quality eval | `evidence/query-native/R00/baseline_eval.json` | Deterministic tolerance ±0.01 |
| C0-2 Profiling validation | Ingestion commit, warm, and centroid bottlenecks measured | Profile `POST /api/ingest` and vault sync | `evidence/query-native/R00/profile.txt` | CPU profile on host |
| C0-3 Workload freeze | `workload.json` and `gates.json` committed with evaluation splits | Schema inspection and checksum validation | `evidence/query-native/R00/workload.json` | Must cover 1M span projections |
| C0-4 Test-set isolation | Test judgments held out from tuning/training | Directory structure and checksum audit | `evidence/query-native/R00/delivery.md` | Zero test leakage |

## Execution and evidence

- Python Venv: `/mnt/c/Users/anshu/OneDrive/Documents/Code/Utilities/trace-lite/.venv/bin/python`
- Commands:
  - `python -m pytest tests/ -v`
  - `python benchmarks/run_quality_eval.py --output evidence/query-native/R00/quality_repro.json`
  - `python benchmarks/run_beir_unified.py --output-dir evidence/query-native/R00/beir/`
- Evidence Directory: `evidence/query-native/R00/`
- Delivery Record: `evidence/query-native/R00/delivery.md`
- Independent Review: `evidence/query-native/R00/review.md`

## Recovery and escalation

- If datasets or runtime are unavailable, preserve the manifest and mark affected comparisons INCONCLUSIVE.
- Do not invent numerical gates from old synthetic document-count benchmarks.
- Stop dependent execution if basic test suite or SQLite FTS5 fails to execute in `.venv`.

