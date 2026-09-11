# P07 Delivery — End-to-End Integration & Master Benchmark Suite

- Packet: `docs/builds/filing-cabinet/packets/P07-e2e-integration-and-benchmarks.md`
- Candidate commit: (this commit — Wave 5)

## Files
- `tests/test_e2e.py` — full chain (vault→sync→facets→3-tier queries→abstention→holon
  invariants) + Cordis micro-step (budget→veto→RPE→anti-circumvention). 2 tests.
- `benchmarks/run_trace_lite_benchmark.py` — `--docs/--queries/--out` qualification harness:
  chunked bulk ingest + facet wiring + warm, 1k mixed queries, Cordis probe, RSS, JSON report.
- `evidence/P07/trace_lite_benchmark_report.json` — qualification report (generated, committed).

## Verification (builder-run)
- `.venv/bin/python -m pytest tests/` → **26 passed**.
- `.venv/bin/python benchmarks/run_trace_lite_benchmark.py --docs 10000` → exit 0, all gates true.
- No repairs needed.
