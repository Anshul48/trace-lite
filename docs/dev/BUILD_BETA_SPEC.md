# Build Beta Specification: HippoRAG 2 Graph Activation Engine

**Target Branch**: `feature/graph-hipporag`  
**Prerequisites**: Build Alpha (`feature/tri-channel-parity`) merged and verified.

---

## 1. Objective
Add horizontal associative multi-hop retrieval to `trace-lite` via **Personalized PageRank (PPR) Graph Activation** on embedded SQLite infrastructure, without adding external graph database dependencies or write-path LLM overhead.

---

## 2. Component Specifications & Exact Code Diffs

### Step 1: SQLite Graph Edge Schema in `cortex/forest.py`

#### Changes:
1. In `ForestIndex._init_db()`, create the `graph_edges` table:
```sql
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id             TEXT PRIMARY KEY,
    source_atom_id      TEXT NOT NULL,
    target_atom_id      TEXT NOT NULL,
    relation_type       TEXT NOT NULL, -- 'NEXT', 'PREV', 'CHILD_OF', 'PARENT_OF', 'CO_OCCURS', 'SUPPORTS'
    weight              REAL NOT NULL DEFAULT 1.0,
    confidence          REAL NOT NULL DEFAULT 1.0,
    created_at          TEXT NOT NULL,
    FOREIGN KEY(source_atom_id) REFERENCES tree_nodes(node_id)
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(source_atom_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON graph_edges(target_atom_id);
CREATE INDEX IF NOT EXISTS idx_graph_edges_rel ON graph_edges(relation_type);
```
2. Implement methods:
   - `store_edges(edges: list[dict]) -> None`
   - `get_neighbor_edges(node_ids: list[str], limit: int = 2000) -> list[dict]`

---

### Step 2: Personalized PageRank Engine in `engines/graph.py`

#### [NEW] `src/trace_lite/engines/graph.py`
```python
"""Personalized PageRank Graph Activation Engine for Trace-Lite."""

from collections import defaultdict
from typing import Any, Sequence


class GraphActivationEngine:
    """Spreads activation energy from query seed matches across typed graph edges."""

    def __init__(self, damping: float = 0.85, max_iterations: int = 20, convergence_epsilon: float = 1e-8):
        self.damping = damping
        self.max_iterations = max_iterations
        self.convergence_epsilon = convergence_epsilon

    def personalized_pagerank(
        self,
        seeds: dict[str, float],      # {node_id: initial_normalized_score}
        adjacency: dict[str, list[tuple[str, float]]],  # {source: [(target, weight)]}
    ) -> dict[str, float]:
        """Compute Personalized PageRank scores via sparse power-iteration."""
        if not seeds or not adjacency:
            return {}

        total_seed = sum(seeds.values()) or 1.0
        personalization = {node: score / total_seed for node, score in seeds.items()}
        current = dict(personalization)
        nodes = set(adjacency.keys()) | set(seeds.keys())

        for _ in range(self.max_iterations):
            following = {node: (1.0 - self.damping) * personalization.get(node, 0.0) for node in nodes}
            for src, val in current.items():
                edges = adjacency.get(src, [])
                if not edges:
                    following[src] += self.damping * val
                    continue
                normalizer = sum(w for _, w in edges) or 1.0
                for dst, w in edges:
                    following[dst] = following.get(dst, 0.0) + (self.damping * val * (w / normalizer))

            delta = sum(abs(following[node] - current.get(node, 0.0)) for node in nodes)
            current = following
            if delta < self.convergence_epsilon:
                break

        return current
```

---

### Step 3: Zero-LLM Deterministic Edge Generation

#### Changes:
1. **Structural Sequential Edges** (`NEXT` / `PREV`):
   - In `SpineStore.store_atoms()`, automatically link consecutive atoms within the same `SourceArtifact`:
     $$\text{Atom}_i \xrightarrow{\text{NEXT}} \text{Atom}_{i+1}, \quad \text{Atom}_{i+1} \xrightarrow{\text{PREV}} \text{Atom}_i$$
2. **Hierarchical Edges** (`PARENT_OF` / `CHILD_OF`):
   - In `RaptorEngine._build_candidate()`, automatically create bi-directional parent-child edges between cluster summary nodes and their constituent child nodes.
3. **Term Co-occurrence Edges** (`CO_OCCURS`):
   - Link atoms sharing distinct non-stopword technical keywords or named entities.

---

### Step 4: Adaptive 3-Tier Gating & Quad-Channel Retrieval in `engines/lattice.py`

#### Changes:
1. **Adaptive Gating Rules**:
   - **Gate 1 (Abstention Gate)**: If `max(flat_scores, bm25_scores) < 0.20` $\rightarrow$ Skip PPR, return `insufficient_evidence`.
   - **Gate 2 (Direct Hit Short-Circuit)**: If `max(flat_scores, bm25_scores) >= 0.90` and `delta >= 0.30` $\rightarrow$ Set `graph_weight = 0.0`, return exact needle immediately.
   - **Gate 3 (Multi-Hop Activation)**: Otherwise, run `GraphActivationEngine.personalized_pagerank(seeds=direct_hits, adjacency=subgraph)`.
2. **Dynamic Quad-Channel Scoring**:
   $$\text{Final Score} = 0.35 \times \text{flat} + 0.25 \times \text{tree} + 0.25 \times \text{bm25} + 0.15 \times \text{graph\_ppr}$$

---

## 3. Verification Protocol

```bash
# 1. Run unit tests including multi-hop test suite
uv run pytest tests/test_engines.py -o pythonpath=.
uv run pytest -o pythonpath=.

# 2. Run benchmark suite and lock report
uv run tl benchmark --fixture benchmarks/fixtures/private_indomain_v1.json --report-output ./report_beta.json

# 3. Compare Alpha vs Beta
python scripts/compare_benchmarks.py ./report_alpha.json ./report_beta.json
```
