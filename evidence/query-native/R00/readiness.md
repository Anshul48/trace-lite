# R00 — Readiness (B1)

Record type: readiness
Author role and date: coordinator / 2026-09-12
Packet and contract revision: R00-baseline.md / TL-QN-2026-09-12.1
Base/candidate identity: 0df044b51fb3b40ca3360ea7abd78ac36924b9f8 + dirty: `M COORDINATOR_LAUNCH_PROMPT.md`, `M docs/builds/query-native/COORDINATOR.md` (prompt-text only, no product code)
Environment: WSL2, `.venv/bin/python` 3.11.15 (uv-managed, project-local), SQLite 3.53.1 + FTS5 verified, 16 threads, 15GiB RAM, repo on v9fs (60G free), /tmp ext4 (919G free)

## Checks

- Objective/scope/authorization: R00 is read-only baseline evaluation; no `src/trace_lite/` modification permitted. Covered by launch authorization. OK.
- Required inputs: BEIR corpora present at `/mnt/c/Users/anshu/AppData/Local/trace-bench-data/beir-{scifact,nfcorpus,fiqa}` with per-dataset `checksums.json`. OK.
- Commands validated via `--help`: `pytest tests/`, `benchmarks/run_quality_eval.py --tag/--out/--docs-per-topic`, `benchmarks/run_beir_unified.py --dataset/--data-root/--out-dir`. Packet's `--output`/`--output-dir evidence/.../quality_repro.json` flags do not exist; actual flags used and recorded in delivery.
- Baseline: `pytest tests/ -x -q` → 53 passed (NOT the historical 39/39; suite grew — recorded as discrepancy, not failure).
- Acceptance feasibility: C0-1 (reproducibility) feasible; C0-2 (profile POST /api/ingest + sync path) feasible via TestClient; C0-3 (workload/gates freeze) feasible; C0-4 (isolation audit) feasible by structure + checksum.
- Rollback: R00 writes only `evidence/query-native/R00/`; no migration, no shared state. Nothing to roll back.

## Verdict

READY. Proceed to B2.
