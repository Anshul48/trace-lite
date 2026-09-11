# Execution Plan & Dependency Graph: Trace-Lite Smart Filing Cabinet

Revision: 2026-09-11.2
Scope: Staged Execution DAG for Trace-Lite

---

## 1. Dependency Graph (DAG)

```text
[Master Coordinator]
         │
         ├─── Wave 1 (Clean Foundation & Packaging)
         │       └── P01: Clean Foundation Reset via bury_and_reset.sh (pyproject.toml, CLI, tests)
         │
         ├─── Wave 2 (Core Canonical Storage)
         │       └── P02: Canonical Atom Filing Storage & WAL Governor (database.py, schema.sql)
         │
         ├─── Wave 3 (Filing & Router)
         │       ├── P03: Hearst Multi-Parent Faceted Engine (filing/engine.py, holon.py)
         │       └── P04: Sub-50ms 3-Tier Dual-Dispatch Router (router/cascade.py, lexical.py)
         │
         ├─── Wave 4 (Ecosystem Integrations)
         │       ├── P05: DeepSeek Harness (Cordis / DSH) Plugin Suite (cordis/plugin.py, tms.py)
         │       └── P06: Obsidian Vault Sync Engine & REST API (obsidian/watcher.py, api/app.py)
         │
         └─── Wave 5 (Final Integration & Benchmarks)
                 └── P07: End-to-End Integration & Master Benchmark Suite
```

---

## 2. Wave Scheduling & Parallelism Rules

| Wave | Packets | Pre-conditions | Concurrency | Expected Artifacts |
|---|---|---|---|---|
| **Wave 1** | P01 | Freedom directive authorized | Serial | Clean working tree; `archive/v1-legacy-scaffold` tagged; `tl version` passes |
| **Wave 2** | P02 | Wave 1 verified | Serial | SQLite database; WAL governor; canonical atoms inserted and retrieved |
| **Wave 3** | P03, P04 | Wave 2 verified | Parallel (Disjoint modules: filing vs router) | Multi-parent facet DAG; 3-tier router achieving P95 <= 50ms |
| **Wave 4** | P05, P06 | Wave 3 verified | Parallel (Disjoint modules: cordis vs obsidian) | Formal Cordis protocols verified; Obsidian debounced sync on :8420 |
| **Wave 5** | P07 | Waves 1–4 verified | Serial | Full test suite passes; benchmark report certified |
