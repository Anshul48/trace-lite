# trace-lite

A lightweight, single-node, high-performance filing cabinet and local memory substrate.

## Core Philosophy
- **Deterministic & Fast**: Zero LLM dependencies for core indexing, atomization, and storage.
- **Append-only Provenance**: Content-addressed atoms with exact source byte spans and SHA-256 hashes.
- **Single-Node Simplicity**: Pure SQLite foundation; no dual-storage desync, no distributed coordination tax.
- **Filing Cabinet Design**: Store each item once; organize into multi-dimensional collections and browsable views.

## Architecture
- `src/trace_lite/store/`: Append-only SQLite event ledger & atom storage.
- `src/trace_lite/filing/`: Local holons, multi-membership indexes, and topic collections.
- `src/trace_lite/retrieval/`: Fast hybrid lexical + sparse activation retrieval.

## Development
```bash
uv sync --all-extras
pytest
```
