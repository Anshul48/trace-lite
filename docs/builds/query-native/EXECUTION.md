# Execution plan

Contract: TL-QN-2026-09-12.1. Start from the current TL checkout; preserve historical implementation and evidence.

## Ordered work

| Wave | Packet | Dependency | Primary ownership |
|---|---|---|---|
| 0 | [R00](packets/R00-baseline.md) | None | Baseline artifacts, benchmark contracts |
| 1 | [R01](packets/R01-storage.md) | R00 | Store, migrations, source service, outbox |
| 2 | [R02](packets/R02-spans.md) | R01 | Source adapters, spans, segmentation views |
| 3 | [R03](packets/R03-retrieval.md) | R02 | Encoders, index adapter, router |
| 3 | [R04](packets/R04-local-graphs.md) | R02 | Extraction and local relation records |
| 4 | [R05](packets/R05-holons.md) | R03, R04 | Reconciliation and filing |
| 5 | [R06](packets/R06-maintenance.md) | R05 | Incremental maintenance and publication integration |
| 6 | [R07](packets/R07-dossiers.md) | R06 | Compiler, API and Cordis adapter |
| 7 | [R08](packets/R08-prediction.md) | R07 | Controlled predictive experiments |
| 8 | [R09](packets/R09-qualification.md) | R07; R08 disposition | Integrated qualification |

R03/R04 may run concurrently only after schema and adapter contracts are frozen and isolated checkouts are available. Shared migrations/fixtures must have a single owner. Serialize integration. No delegation or new run is launched by this planning update.

## Common packet procedure

1. Read PROJECT, ARCHITECTURE, EVALUATION, STATE and the packet. Re-pin HEAD/diff/runtime. Inspect concurrent work and actual commands.
2. Freeze interfaces and an example input/output before implementation. Apply compatible migrations; preserve prior databases.
3. Build only packet-owned files and necessary tests. Integration edits outside ownership require coordinator reconciliation, not concurrent overwrites.
4. Save delivery with candidate identity, exact commands/return codes, evidence paths, known failures and recovery procedure.
5. Obtain a separate verifier for execution qualification as in the earlier builder/verifier workflow. A planning self-review does not substitute for that check.
6. Repair reproduced failures up to three attempts under the existing bounded repair discipline. Stop dependent work if still failing; continue independent ready work.
7. Reverify affected gates after integration. Advance STATE to VERIFIED only for the named candidate and packet scope.

## Command discipline

The existing `.venv` is a Linux-layout environment. On the recorded WSL setup, use `.venv/bin/python -m pytest` from the TL root after checking its interpreter and imports. On Windows, select and record an available compatible interpreter; do not execute Linux binaries as Windows programs or install dependencies merely for this planning review.

Existing harnesses:

- `benchmarks/run_trace_lite_benchmark.py --help`: historical synthetic component benchmark.
- `benchmarks/run_beir_unified.py --help`: frozen BEIR adapter; validate data-root and output paths before a run.
- `benchmarks/run_quality_eval.py --help`: historical synthetic quality harness.

R00 must inspect/help-check the chosen runtime before recording runnable commands. New QN harnesses and test files are deliverables, not existing interfaces. Record their actual CLI once implemented, then use that exact command for independent verification. Keep output outside historical evidence paths.

## Transition and rollback

Take a consistent backup, migrate to a separate destination, run shadow comparisons, and switch a versioned database/index pointer only after verification. Keep legacy ID mappings and prior generation for rollback. Schema mismatch must reject an old writer instead of corrupting new data.

Source acceptance/lexical access must function during model outages. Semantic readiness remains observable. A failed new graph/index generation leaves an explicitly labeled usable baseline, not silently fabricated model output.

## Future coordinator entry point

[COORDINATOR.md](COORDINATOR.md) is the TL-only launch brief. The old root cross-project prompt is historical. Do not execute its reset or TRACE instructions.

