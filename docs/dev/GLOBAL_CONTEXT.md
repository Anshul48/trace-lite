# Trace-Lite: Global Context & System Invariants

## 1. System Architecture Overview

Trace-Lite (`tl`) is a headless, source-first cognitive database designed for high-accuracy, low-cost hierarchical retrieval.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 Interfaces                                      │
│               CLI (`tl`)  │  Python SDK  │  REST API  │  Obsidian Plugin        │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │
┌────────────────────────────────────────▼────────────────────────────────────────┐
│                              TraceLite Engine (db.py)                           │
│     Orchestrates ingestion, staging, validation, activation, and queries        │
└──────────────┬─────────────────────────┬─────────────────────────┬──────────────┘
               │                         │                         │
┌──────────────▼──────────┐   ┌──────────▼──────────┐   ┌──────────▼──────────────┐
│       SpineStore        │   │     ForestIndex     │   │      VectorStore        │
│    (spine.sqlite3)      │   │   (cortex.sqlite3)  │   │       (LanceDB)         │
│  - source_artifacts     │   │  - trees            │   │  - node embeddings      │
│  - atoms (immutable)    │   │  - tree_nodes       │   │  - versioned table      │
│  - atom_fts (FTS5 BM25) │   │  - index_builds     │   │    collections          │
│  - events ledger        │   │  - pending queues   │   │  - leaf + summary vecs  │
└─────────────────────────┘   └─────────────────────┘   └─────────────────────────┘
```

---

## 2. Core Invariants (Non-Negotiable)

1. **Source Evidence is the Only Ground Truth**:
   - The Spine (`spine.sqlite3`) is an append-only ledger. Source documents and atoms are never mutated or destructively deleted.
   - Summaries, trees, clusters, and vector indices are *disposable, rebuildable projections* (Cortex).
2. **Fail-Closed Indexing**:
   - If an LLM provider fails during summarization/routing, the candidate build is discarded. Untrusted/legacy fallback summaries are never activated into the search path.
   - Searches refuse to run if unindexed pending captures exist or if validation fails (unless explicitly overridden with `--force` for flat search).
3. **Zero-LLM Ingestion Overhead**:
   - Ingestion is decoupled from organization. Calling `db.ingest()` writes raw text and atoms to the Spine in milliseconds without blocking on LLM calls or vector embeddings.
4. **Exact Provenance & Character Offsets**:
   - Every `Atom` tracks `char_offset_start`, `char_offset_end`, and `content_hash` relative to the raw source document.
   - Retrieved evidence must present these offsets and hashes as structured citations.
5. **Deterministic Validation Gates**:
   - Every LLM-generated summary must pass deterministic quality gates (minimum length, non-empty, no reasoning/think tags, non-echo, source lexical overlap).

---

## 3. Data Models & Typings

### Spine Models (`trace_lite.spine.models`)

```python
@dataclass(frozen=True)
class Atom:
    atom_id: str
    content: str
    content_hash: str
    source_artifact_id: str
    sequence_index: int
    char_offset_start: int
    char_offset_end: int
    created_at: str  # ISO 8601 UTC
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceArtifact:
    artifact_id: str
    document_name: str | None
    source_uri: str | None
    content_hash: str
    created_at: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SpineEvent:
    event_id: str
    event_type: str
    timestamp: str
    payload: dict = field(default_factory=dict)
```

### Cortex Models (`trace_lite.cortex.forest`)

```python
@dataclass
class TreeNode:
    node_id: str
    tree_id: str
    level: int  # 0 = leaf, 1+ = summary
    node_type: str  # "leaf" | "cluster_summary" | "root_summary"
    atom_ids: list[str]  # Atom IDs covered by this node
    summary_text: str | None
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_count: int = 1
    summary_provenance: str = "source"  # "source" | "llm" | "retry"


@dataclass
class Tree:
    tree_id: str
    name: str
    description: str
    root_node_id: str | None = None
    node_count: int = 0
    leaf_count: int = 0
    depth: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_consolidated: str | None = None
```

### Retrieval Models (`trace_lite.engines.lattice`)

```python
@dataclass
class EvidenceItem:
    atom: Atom
    score: float
    tree_id: str | None
    tree_name: str | None
    source_artifact: SourceArtifact | None
    traversal_path: list[str]
    source_location: dict = field(default_factory=dict)  # {char_start, char_end, content_hash, ...}
    channel_scores: dict = field(default_factory=dict)   # {flat, tree, bm25, graph_ppr, fused}


@dataclass
class QueryResult:
    query_text: str
    items: list[EvidenceItem]
    traversal_paths: dict[str, list[str]]
    mode: str
    timestamp: str
    warnings: list[str] = field(default_factory=list)
    sufficiency_state: str = "answerable"  # "answerable" | "ambiguous" | "insufficient_evidence"
```

---

## 4. Key Directory Layout

- `src/trace_lite/db.py`: Main `TraceLite` class, staged builds, candidate validation, query dispatch.
- `src/trace_lite/spine/`: Storage for source artifacts, atoms, and lexical indexes.
  - `atomizer.py`: Text chunking into paragraph/bullet atoms with exact offsets.
  - `models.py`: Immutable Spine data classes.
  - `store.py`: `SpineStore` SQLite manager (`spine.sqlite3`).
- `src/trace_lite/cortex/`: Derived hierarchical indexes and vector storage.
  - `forest.py`: `ForestIndex` SQLite manager (`cortex.sqlite3`).
  - `clustering.py`: UMAP + HDBSCAN clustering pipeline.
  - `vector_store.py`: `LanceDBStore` / `MockVectorStore` vector backend.
  - `energy.py`: `EnergyModel` for recency/frequency activation decay.
- `src/trace_lite/engines/`:
  - `raptor.py`: `RaptorEngine` bottom-up tree builder.
  - `lattice.py`: `LatticeEngine` multi-channel retrieval & tree traversal.
  - `router.py`: `ForestRouter` side-effect-free semantic atom assignment.
  - `summary.py`: LLM prompt normalizers and deterministic quality validators.
- `src/trace_lite/ui/`:
  - `server.py`: FastAPI daemon backend.
- `tests/`: 20 dedicated test files covering CLI, engines, security, and integration.

---

## 5. Development & Testing Commands

```bash
# Run entire test suite
uv run pytest -o pythonpath=.

# Run specific module tests
uv run pytest tests/test_engines.py -o pythonpath=.

# Run benchmarks
uv run tl benchmark --fixture benchmarks/fixtures/private_indomain_v1.json --json
```
