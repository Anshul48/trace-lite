# Trace-Lite SOTA Benchmark Suite

Independent, reproducible, and production-grade evaluation harness for `trace-lite`.

## Architecture & Layout

```
benchmarks/
├── README.md               # Intake contract & evaluation documentation
├── runner.py               # CLI entrypoint (python -m benchmarks.runner)
├── datasets/               # Schemas, loaders, and development fixtures (NO private data)
│   ├── schema.py           # Pydantic models for manifests, cases & categories
│   ├── loader.py           # SHA-256 verification and manifest loading
│   └── technical_docs_dev.json  # 6-case deterministic dev fixture (release_authority: false)
├── adapters/               # Public benchmark loaders (offline-first caching)
│   ├── beir_adapter.py     # BEIR suite (SciFact, NFCorpus, FiQA, etc.)
│   ├── bright_adapter.py   # BRIGHT reasoning-intensive coding & domain tasks
│   ├── multihop_adapter.py # MultiHop-RAG / HotpotQA multi-source evidence
│   ├── hichunk_adapter.py  # HiCBench / HiChunk hierarchical chunk retrieval
│   └── longmemeval_adapter.py # LongMemEval temporal & multi-session memory
├── baselines/              # Baselines for fair comparison
│   ├── bm25_retriever.py   # Okapi BM25 lexical retriever
│   ├── dense_retriever.py  # Flat Dense SentenceTransformers (MiniLM / BGE)
│   ├── hybrid_rrf_retriever.py # Lexical + Dense Reciprocal Rank Fusion
│   ├── flat_hierarchy_retriever.py # Structure-only parent/child hierarchy (no LLMs)
│   └── trace_retriever.py  # TraceLite runner in isolated temporary --data-dir
├── metrics/                # 4-Dimension Metric Engine
│   ├── retrieval.py        # Recall@K, nDCG@K, Complete Gold Coverage, Precision, Abstention
│   ├── organization.py     # Source coverage, orphan checks, duplicate counts, purity
│   ├── operations.py       # Latency (p50/p95/p99), throughput, RSS memory, storage
│   └── aggregator.py       # Per-category slicing, statistics, JSON & Markdown diff reports
├── generators/             # Deterministic synthetic scale ladders
│   └── scale_corpus.py     # 1K, 10K, 100K, and 1M atom corpus & query generator
└── test_benchmark_harness.py # Automated test suite for the benchmark harness
```

---

## 1. Quickstart

### Run the Development Fixture
```bash
python -m benchmarks.runner run --dataset benchmarks/datasets/technical_docs_dev.json --baselines bm25,dense,hybrid_rrf,trace_flat
```

### Run All Available Baselines
```bash
python -m benchmarks.runner run --dataset benchmarks/datasets/technical_docs_dev.json --baselines all
```

### Run Synthetic Scale Ladder Stress Benchmark
```bash
python -m benchmarks.runner scale --tier 1k --baselines bm25,dense,hybrid_rrf
```

### Compare Run Results
```bash
python -m benchmarks.runner compare --current benchmarks/results/run_current.json --baseline benchmarks/results/run_baseline.json --output report.md
```

### Setup Public Datasets
```bash
python -m benchmarks.runner setup --dataset scifact
```

---

## 2. Release Benchmark Intake Contract

The checked-in fixture `technical_docs_dev.json` is explicitly marked `release_authority: false` and is intended solely for developer testing and regression prevention.

A valid **Production Release Benchmark** must satisfy:
1. **Private In-Domain Corpus**: At least 320 reviewed queries (40 queries each across 8 categories).
2. **8 Query Categories**:
   - `CAT_01` (Direct Lookup): Needle-in-haystack factual retrieval.
   - `CAT_02` (Chronology & Order): Sequential before/after temporal order.
   - `CAT_03` (Contradiction & Revision): Newer architectural updates overriding older state.
   - `CAT_04` (Cross-Domain): Concepts linking disparate domains.
   - `CAT_05` (Global Context): High-level thematic queries requiring hierarchy roots.
   - `CAT_06` (Multi-Hop Evidence): 2–4 distinct evidence hops across documents.
   - `CAT_07` (Historical & Versioned): Prototype vs. current system state.
   - `CAT_08` (Insufficient Evidence / Abstention): Out-of-scope queries requiring explicit abstention.
3. **Immutability Checksum**: Each corpus snapshot must have an exact SHA-256 hash verified by `benchmarks.datasets.loader`.
4. **Target Release Gates**:
   - Complete Gold Evidence Coverage: $\ge 80\%$
   - Citation Precision: $\ge 90\%$
   - Source Preservation Coverage: $100\%$ (zero lost atoms)
   - Abstention Accuracy: $\ge 95\%$

---

## 3. Pytest Isolation & Development

Standard development test commands continue to run only the core unit and integration tests:
```bash
pytest
```

To run the automated test suite for the benchmark harness:
```bash
pytest benchmarks/test_benchmark_harness.py
```
