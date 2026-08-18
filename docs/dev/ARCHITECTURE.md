# Trace-Lite Architecture: HippoRAG 2 Graph Activation & Quad-Channel Retrieval

## 1. System Architecture Overview

Trace-Lite (`tl`) is a headless, source-first cognitive database designed for high-accuracy, zero-write-LLM, associative multi-hop retrieval on embedded SQLite + LanceDB infrastructure.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                       Interfaces                                       │
│                 CLI (`tl`)  │  Python SDK  │  REST API  │  Obsidian Plugin             │
└───────────────────────────────────────────┬────────────────────────────────────────────┘
                                            │
┌───────────────────────────────────────────▼────────────────────────────────────────────┐
│                                TraceLite Engine (db.py)                                │
│       Orchestrates zero-LLM ingestion, staged builds, validation, gating, & search     │
└───────────────┬───────────────────────────┬───────────────────────────┬────────────────┘
                │                           │                           │
┌───────────────▼───────────┐   ┌───────────▼───────────┐   ┌───────────▼────────────────┐
│        SpineStore         │   │      ForestIndex      │   │        VectorStore         │
│     (spine.sqlite3)       │   │    (cortex.sqlite3)   │   │         (LanceDB)          │
│  - source_artifacts       │   │  - trees              │   │  - leaf & summary vectors  │
│  - atoms (immutable)      │   │  - tree_nodes         │   │  - versioned table         │
│  - atom_fts (FTS5 BM25)   │   │  - graph_edges        │   │    collections             │
│  - events ledger          │   │  - index_builds       │   │  - cosine distance space   │
└───────────────────────────┘   └───────────────────────┘   └────────────────────────────┘
```

---

## 2. Core Architectural Invariants

1. **Source Evidence is the Only Ground Truth**:
   - The Spine (`spine.sqlite3`) is an append-only ledger. Source documents, artifacts, and atoms are immutable. They are never mutated or destructively deleted.
   - Summaries, trees, clusters, graph edges, and vector indices are *disposable, rebuildable projections* (Cortex).
2. **Fail-Closed Indexing**:
   - If an LLM provider fails during summarization/routing, the candidate build is discarded. Untrusted or legacy fallback summaries are never activated into the search path.
   - Searches refuse to run if unindexed pending captures exist or if validation fails (unless explicitly overridden with `--force` for leaf vector search or queried with `--allow-hot-inbox`).
3. **Zero-LLM Ingestion Overhead**:
   - Ingestion is completely decoupled from organization. Calling `db.ingest()` writes raw text, atomic chunks, FTS5 lexical tokens, and sequential graph edges to the Spine in $< 5\text{ ms}$ without blocking on LLM calls or vector embeddings.
4. **Exact Provenance & Byte Offsets**:
   - Every `Atom` tracks `char_offset_start`, `char_offset_end`, and `content_hash` relative to the raw source document.
   - Retrieved evidence presents these exact character offsets and hashes as structured citations.
5. **Deterministic Validation Gates**:
   - Every LLM-generated summary must pass deterministic quality gates (minimum length, non-empty, no reasoning/think tags, non-echo, source lexical overlap).

---

## 3. Quad-Channel Hybrid Retrieval & Adaptive 3-Tier Gating

When a query enters [`LatticeEngine.query()`](file:///src/trace_lite/engines/lattice.py), it evaluates four complementary retrieval channels coordinated by adaptive 3-tier gating:

```mermaid
flowchart TD
    Q[User Query] --> G1{Gate 1: Abstention Gate<br/>max direct score < 0.20?}
    G1 -- Yes --> ABSTAIN[Return: insufficient_evidence<br/>Skip PPR / Zero LLM Overhead]
    G1 -- No --> G2{Gate 2: Direct Hit Short-Circuit<br/>score >= 0.90 & delta >= 0.30?}
    G2 -- Yes --> EXACT[Exact Needle Short-Circuit<br/>graph_weight = 0.0, Return Instant Needle]
    G2 -- No --> G3[Gate 3: Multi-Hop Activation<br/>Seed Top Matches into GraphActivationEngine]
    
    G3 --> PPR[Sparse Power-Iteration PPR<br/>alpha = 0.85, 20 iters over graph_edges]
    PPR --> FUSE[Dynamic Quad-Channel Scoring<br/>0.35 Flat + 0.25 Tree + 0.25 BM25 + 0.15 Graph PPR]
    EXACT --> FUSE
    FUSE --> CIT[Hydrate Structured Citations with Char Spans & Hash Provenance]
```

### Dynamic Quad-Channel Scoring Formula

For any candidate atom $a \in \mathcal{A}$:

$$\text{Final Score}(a) = 0.35 \times \text{flat}(a) + 0.25 \times \text{tree}(a) + 0.25 \times \text{bm25}(a) + 0.15 \times \text{graph\_ppr}(a)$$

### Retrieval Channels:
- **Channel 1 (Flat Vector Search)**: Direct cosine similarity against LanceDB leaf vectors (`node_type = 'leaf'`).
- **Channel 2 (Tree Traversal)**: Top-down hierarchical navigation across RAPTOR cluster summaries.
- **Channel 3 (SQLite FTS5 Lexical Search)**: Porter-stemmed BM25 lexical keyword matching with normalized positive scores $1.0 / (1.0 + \max(0, \text{bm25\_score}))$.
- **Channel 4 (HippoRAG Graph Activation)**: Personalized PageRank spreading activation energy over structural (`NEXT`/`PREV`), hierarchical (`PARENT_OF`/`CHILD_OF`), and term co-occurrence (`CO_OCCURS`) edges.

---

## 4. Retrospective & Negative Results: Why Alpha Failed & How Beta Solved It

### The Limitations of Alpha (Tri-Channel Baseline)

Build Alpha introduced SQLite FTS5 lexical search combined with flat vector and RAPTOR tree traversal. While Alpha achieved parity on direct single-needle queries, comprehensive benchmark auditing exposed major structural failure modes:

1. **Disjointed Multi-Hop Blindspot ($72.5\%$ Coverage)**:
   - *Failure Mode*: In Alpha, evidence that required linking across multiple separate paragraphs or documents (e.g. Protocol A handshake $\rightarrow$ Token validation $\rightarrow$ Resource authorization) could not be retrieved together if intermediate hops lacked high direct lexical or semantic similarity to the prompt.
   - *Impact*: Queries in `CAT_06` (Multi-Hop Evidence) and `CAT_07` (Historical & Versioned) dropped intermediate gold passages, resulting in incomplete context synthesis.
2. **Chronological Fragmentation**:
   - *Failure Mode*: Alpha treated every atom as an isolated bag of words/embeddings. Queries requiring strict before-and-after sequence reconstruction (`CAT_02`) failed because vector similarity lacks temporal or sequential topology.
3. **Keyword Collision & Synonym Blindness in Lexical Scoring**:
   - *Failure Mode*: Pure BM25 without graph association failed when related documents used alternate terminology or versioned identifiers.
4. **Latency Penalty on Trivial Queries**:
   - *Failure Mode*: Alpha executed the full tri-channel pipeline uniformly for every query, incurring redundant scoring overhead on obvious exact lookups.
5. **Out-of-Scope False Positives**:
   - *Failure Mode*: Irrelevant or unanswerable queries were scored across all channels without an early abstention barrier, yielding low-confidence hallucinations.

### How Beta Solved Each Failure Mode

| Alpha Limitation | Beta Architectural Solution | Empirical Improvement (Alpha $\rightarrow$ Beta) |
|:---|:---|:---:|
| Disjointed multi-hop paths | Sparse Power-Iteration **Personalized PageRank (PPR)** over SQLite `graph_edges` | `CAT_06` Recall: $72.5\% \rightarrow \mathbf{77.5\%}$<br/>Complete Coverage@20: $74.1\% \rightarrow \mathbf{82.2\%}$ |
| Chronological blindness | Zero-LLM deterministic **Sequential Edges (`NEXT`/`PREV`)** between adjacent atoms | `CAT_02` Recall@5: $79.2\% \rightarrow \mathbf{83.3\%}$<br/>`CAT_07` Recall@5: $72.5\% \rightarrow \mathbf{83.8\%}$ ($+11.25\%$) |
| Synonym & entity isolation | Deterministic **Co-occurrence Edges (`CO_OCCURS`)** linking shared non-stopword technical terms | Citation Precision@5: $28.8\% \rightarrow \mathbf{36.3\%}$ ($+26.3\%$ relative) |
| Fixed latency overhead | **Adaptive 3-Tier Gating** short-circuiting PPR on direct needle hits | Latency $p50$: $1654\text{ ms} \rightarrow \mathbf{1596\text{ ms}}$ (faster) |
| Out-of-scope false positives | **Abstention Gate** rejecting irrelevant queries before graph spreading | Abstention Accuracy: $87.5\% \rightarrow \mathbf{93.1\%}$ |

---

## 5. Data Models & Schemas

### 1. Spine SQLite Schema (`spine.sqlite3`)
```sql
CREATE TABLE IF NOT EXISTS source_artifacts (
    artifact_id     TEXT PRIMARY KEY,
    document_name   TEXT,
    source_uri      TEXT,
    content_hash    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS atoms (
    atom_id             TEXT PRIMARY KEY,
    content             TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    source_artifact_id  TEXT NOT NULL,
    sequence_index      INTEGER NOT NULL,
    char_offset_start   INTEGER NOT NULL,
    char_offset_end     INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    metadata            TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(source_artifact_id) REFERENCES source_artifacts(artifact_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5(
    atom_id UNINDEXED,
    source_artifact_id UNINDEXED,
    content,
    tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    payload      TEXT NOT NULL
);
```

### 2. Cortex SQLite Schema (`cortex.sqlite3`)
```sql
CREATE TABLE IF NOT EXISTS trees (
    tree_id             TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL,
    root_node_id        TEXT,
    node_count          INTEGER NOT NULL DEFAULT 0,
    leaf_count          INTEGER NOT NULL DEFAULT 0,
    depth               INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    last_consolidated   TEXT
);

CREATE TABLE IF NOT EXISTS tree_nodes (
    node_id         TEXT PRIMARY KEY,
    tree_id         TEXT NOT NULL,
    level           INTEGER NOT NULL,
    node_type       TEXT NOT NULL,
    atom_ids        TEXT NOT NULL, -- JSON list
    summary_text    TEXT,
    summary_provenance TEXT NOT NULL DEFAULT 'source',
    parent_id       TEXT,
    children_ids    TEXT NOT NULL, -- JSON list
    created_at      TEXT NOT NULL,
    last_accessed   TEXT NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(tree_id) REFERENCES trees(tree_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id             TEXT PRIMARY KEY,
    source_atom_id      TEXT NOT NULL,
    target_atom_id      TEXT NOT NULL,
    relation_type       TEXT NOT NULL, -- 'NEXT', 'PREV', 'PARENT_OF', 'CHILD_OF', 'CO_OCCURS'
    weight              REAL NOT NULL DEFAULT 1.0,
    confidence          REAL NOT NULL DEFAULT 1.0,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(source_atom_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON graph_edges(target_atom_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_rel ON graph_edges(relation_type);
```

### 3. Core Python Data Classes

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
    created_at: str
    metadata: dict = field(default_factory=dict)


@dataclass
class TreeNode:
    node_id: str
    tree_id: str
    level: int  # 0 = leaf, 1+ = summary
    node_type: str  # "leaf" | "cluster_summary" | "root_summary"
    atom_ids: list[str]
    summary_text: str | None
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_count: int = 1
    summary_provenance: str = "source"  # "source" | "llm" | "retry"


@dataclass
class EvidenceItem:
    atom: Atom
    score: float
    tree_id: str | None
    tree_name: str | None
    source_artifact: SourceArtifact | None
    traversal_path: list[str]
    source_location: dict = field(default_factory=dict)  # char_start, char_end, content_hash, doc_name, source_uri
    channel_scores: dict = field(default_factory=dict)   # flat, tree, bm25, graph_ppr, fused


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

## 6. Key Directory Layout

- `src/trace_lite/db.py`: Main `TraceLite` class, zero-LLM ingest, staged builds, candidate validation, query dispatch.
- `src/trace_lite/spine/`: Storage for source artifacts, atoms, and lexical indexes.
  - `atomizer.py`: Text chunking into paragraph/bullet atoms with exact offsets.
  - `models.py`: Immutable Spine data classes (`Atom`, `SourceArtifact`, `SpineEvent`).
  - `store.py`: `SpineStore` SQLite manager (`spine.sqlite3`).
- `src/trace_lite/cortex/`: Derived hierarchical indexes and vector storage.
  - `forest.py`: `ForestIndex` SQLite manager (`cortex.sqlite3`) for trees, nodes, manifests, and `graph_edges`.
  - `clustering.py`: UMAP + HDBSCAN clustering pipeline.
  - `vector_store.py`: `LanceDBStore` / `MockVectorStore` vector backend.
  - `energy.py`: `EnergyModel` for recency/frequency activation decay.
- `src/trace_lite/engines/`:
  - `graph.py`: `GraphActivationEngine` (sparse Personalized PageRank) and zero-LLM edge extractors.
  - `lattice.py`: `LatticeEngine` multi-channel retrieval, adaptive 3-tier gating, structured citations.
  - `raptor.py`: `RaptorEngine` bottom-up tree builder.
  - `router.py`: `ForestRouter` side-effect-free semantic atom assignment.
  - `summary.py`: LLM prompt normalizers and deterministic quality validators.
  - `benchmark.py`: `BenchmarkRunner` evaluating recall, coverage, nDCG, and abstention across fixtures.
- `benchmarks/`: Full benchmark evaluation suite, public adapters (HippoRAG, TREC RAG, MTEB), and baselines (BM25, Dense, Hybrid RRF, Flat Hierarchy).

---

## 7. Verification & Benchmark Execution

```bash
# Run unit & integration test suite
uv run pytest -o pythonpath=.

# Run benchmark harness test suite
uv run pytest benchmarks/test_benchmark_harness.py -o pythonpath=.

# Run in-domain benchmark and generate report
uv run tl benchmark --fixture benchmarks/fixtures/private_indomain_v1.json --report-output benchmarks/results/report_beta.json

# Compare Alpha vs. Beta Pareto frontier
uv run python scripts/compare_benchmarks.py benchmarks/results/report_alpha.json benchmarks/results/report_beta.json
```
