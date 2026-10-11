# R00 — Delivery (B2)

Record type: builder delivery
Author role and date: coordinator (acting B2; no product-code scope) / 2026-09-12
Packet and contract revision: R00-baseline.md / TL-QN-2026-09-12.1
Base/candidate identity: 0df044b + dirty prompt-text files only (`COORDINATOR_LAUNCH_PROMPT.md`, `docs/builds/query-native/COORDINATOR.md`); `src/`, `tests/`, `benchmarks/` untouched
Environment: WSL2, `.venv/bin/python` 3.11.15, SQLite 3.53.1 + FTS5, 16 threads / 15GiB RAM, repo v9fs, scale-DB root /tmp ext4

## Delivery section

- Behavior/artifact changed and why: New R00 evidence only — reproduced baseline scores into fresh paths and froze workload/gates. No product behavior changed.
- Files or outputs changed: `evidence/query-native/R00/{readiness.md,quality_repro.json,beir/{scifact,nfcorpus,fiqa}.json,profile.txt,workload.json,gates.json,fixtures.json,delivery.md}` (this file).
- Material implementation decisions: Used actual CLI flags (`--tag/--out`, `--dataset/--out-dir`); BEIR run per-dataset (not `--dataset all`) for bounded time; profile via TestClient with lifespan (20× ingest + 1 query + status); fixture families as DEV data with explicit non-qualification label.
- Known limitations and checks not run: `POST /api/sync` vault path not exercised (watcher needs live vault; R01/R06 cover it); no FULL-durability comparison (R09); no new harness built (none required).

## Evidence

| Criterion | Command or evaluation method | Observed result | Evidence path | Scope/limitations |
| --- | --- | --- | --- | --- |
| C0-1 Baseline reproduction | `.venv/bin/python -m pytest tests/ -x -q` (exit 0) | 53 passed in 15.36s; historical claim was 39/39 — suite grew, no failures | session log; rerun by verifier | Pre-existing growth, not regression |
| C0-1 BEIR scifact | `.venv/bin/python benchmarks/run_beir_unified.py --dataset scifact --out-dir evidence/query-native/R00/beir/` (exit 0) | nDCG@10 0.6589, R@10 0.7765, CI [0.6136,0.7029], checksums all true; clears floor 0.65 | `evidence/query-native/R00/beir/scifact.json` | Exact match to saved artifact |
| C0-1 BEIR nfcorpus | same harness `--dataset nfcorpus` (exit 0) | nDCG@10 0.3188, R@10 0.1446, CI [0.2836,0.3539]; clears_floor=false vs 0.32 floor | `evidence/query-native/R00/beir/nfcorpus.json` | Exact match; floor miss documented (was already so in saved artifact) |
| C0-1 BEIR fiqa | same harness `--dataset fiqa` (exit 0) | nDCG@10 0.2495, R@10 0.3085, CI [0.2247,0.2740]; below 0.38 floor | `evidence/query-native/R00/beir/fiqa.json` | Exact match; 5.3s ingest, 57k docs |
| C0-1 quality harness | `.venv/bin/python benchmarks/run_quality_eval.py --tag r00-repro --out evidence/query-native/R00/quality_repro.json` (exit 0, pass=true) | nDCG 1.0, mrr 0.5, abstention 1.0, selective err 0.0 | `evidence/query-native/R00/quality_repro.json` | DISCREPANCY: historical quality-2k (same 2400 docs) recorded nDCG 0.911/mrr 0.9167 — harness deterministic, so drift comes from product-code fixes since; flagged for verifier |
| C0-2 Profiling | `.venv/bin/python /tmp/r00_profile.py` (exit 0; probe kept outside repo per policy) | 20× POST /api/ingest: 0.110s total, 5.51ms/doc; query 19.7ms; db 112KB; status hardcodes `index_trusted:true` (confirms RESEARCH finding) | `evidence/query-native/R00/profile.txt` | Host-CPU profile, TestClient (no network); sync watcher path not timed |
| C0-3 Workload freeze | schema inspection + sha256 audit | `workload.json` pins candidate, runtime, host, durability (WAL/NORMAL), ranking (DENSE_POOL=2000, RRF, seed-42 bootstrap), corpus checksums, baselines, span math | `evidence/query-native/R00/workload.json` | Covers 1M-span projection rule; R02 measures actual spans/source |
| C0-3 Gates freeze | review | proposed regression (±0.01), quality, correctness (exact §4), resource/admission gates labeled engineering decisions | `evidence/query-native/R00/gates.json` | Frozen for R09 comparison |
| C0-3 Fixtures | inspection | 5 families × conditions/negation/ambiguity/unfiled/abstention | `evidence/query-native/R00/fixtures.json` | DEV only, not judgments |
| C0-4 Test-set isolation | directory + checksum audit | BEIR `qrels/test.tsv` checksummed, labeled FINAL/held-out; isolation policy in workload.json; no tuning ran against qrels | `evidence/query-native/R00/workload.json` | Policy + structure; enforcement is process |

## Independent findings

(none — builder role; verifier uses review.md)
