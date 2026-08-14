# Build Alpha Specification: Tri-Channel Baseline Parity

**Target Branch**: `feature/tri-channel-parity`  
**Prerequisites**: Clean `main` branch, all existing 121 tests passing.

---

## 1. Objective
Achieve complete accuracy, provenance, and fail-closed safety parity with `Trace` on embedded SQLite + LanceDB infrastructure.

---

## 2. Component Specifications & Exact Code Diffs

### Step 1: SQLite FTS5 Lexical Layer in `spine/store.py`

#### Changes:
1. In `SpineStore._init_db()`, create the FTS5 virtual table:
```sql
CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5(
    atom_id UNINDEXED,
    source_artifact_id UNINDEXED,
    content,
    tokenize='porter unicode61'
);
```
2. In `SpineStore.store_atoms(atoms)`:
   - Insert rows into `atom_fts` inside the same SQLite transaction.
3. Add `SpineStore.search_lexical(query_text: str, top_k: int = 20) -> list[tuple[str, float]]`:
   - Execute:
     ```sql
     SELECT atom_id, bm25(atom_fts) as score
     FROM atom_fts
     WHERE atom_fts MATCH ?
     ORDER BY score ASC
     LIMIT ?
     ```
   - Transform negative BM25 score to normalized positive relevance: $1.0 / (1.0 + \text{max}(0.0, \text{bm25\_score}))$.
   - Return list of `(atom_id, normalized_score)`.

---

### Step 2: Tri-Channel Hybrid Fusion in `engines/lattice.py`

#### Changes:
1. In `LatticeEngine.query()`, execute three parallel retrieval paths:
   - **Path A**: Top-down RAPTOR tree traversal (`_lattice_traverse`) $\rightarrow$ `tree_atom_scores`
   - **Path B**: Flat vector search in `VectorStore` (filter: `node_type='leaf'`) $\rightarrow$ `flat_atom_scores`
   - **Path C**: Lexical search in `SpineStore.search_lexical()` $\rightarrow$ `bm25_atom_scores`
2. **Scoring Formula** (`mode == "hybrid"`):
   $$\text{Final Score}(a) = 0.40 \times \text{flat\_score}(a) + 0.30 \times \text{tree\_score}(a) + 0.30 \times \text{bm25\_score}(a)$$
3. If `mode == "tree"` $\rightarrow$ score is `tree_score`.
4. If `mode == "flat"` $\rightarrow$ score is `flat_score`.
5. If `mode == "lexical"` $\rightarrow$ score is `bm25_score`.

---

### Step 3: Sufficiency Gating & Citation Offsets in `engines/lattice.py`

#### Changes:
1. Calculate `sufficiency_state`:
   - If `not sorted_atom_ids` or `final_atom_scores[sorted_atom_ids[0]] < 0.20`:
     - `sufficiency_state = "insufficient_evidence"`
   - Else if `len(sorted_atom_ids) >= 2` and `(final_atom_scores[sorted_atom_ids[0]] - final_atom_scores[sorted_atom_ids[1]]) < 0.015`:
     - `sufficiency_state = "ambiguous"`
   - Else:
     - `sufficiency_state = "answerable"`
2. Surface Structured Citation Offsets:
   - For each hydrated `EvidenceItem`:
     ```python
     source_location = {
         "char_start": atom.char_offset_start,
         "char_end": atom.char_offset_end,
         "content_hash": atom.content_hash,
         "document_name": artifact.document_name if artifact else None,
         "source_uri": artifact.source_uri if artifact else None,
     }
     channel_scores = {
         "flat": flat_atom_scores.get(aid, 0.0),
         "tree": tree_atom_scores.get(aid, 0.0),
         "lexical": bm25_atom_scores.get(aid, 0.0),
         "fused": score,
     }
     ```

---

### Step 4: Hot Inbox Immediate Search in `db.py`

#### Changes:
1. Newly ingested atoms are written directly to `atom_fts` in `SpineStore` during `db.ingest()`.
2. In `db.query()`, if `force=False` and pending unorganized atoms exist:
   - Normal queries remain fail-closed unless `allow_hot_inbox=True` or `mode="hybrid"`.
   - If querying with hot inbox, include hits from unorganized atoms, tagged with warning: `"Contains unorganized hot-inbox evidence"`.

---

### Step 5: Incremental Tree Organization in `db.py`

#### Changes:
1. In `db.organize()`:
   - Identify pending source atoms via `forest.get_pending_source_atom_ids()`.
   - Use `router.route_plan()` to map pending atoms to target tree IDs.
   - For existing trees that only receive incremental leaf additions ($< \text{threshold}$ atoms):
     - Cluster and summarize only the new/dirty cluster branches.
     - Upsert newly generated nodes and vectors without tearing down unaffected trees.
   - For completely new topic domains, build the new tree.
   - `tl consolidate` and `tl reindex --all` remain the explicit full global rebuild commands.

---

### Step 6: Benchmark Runner Engine in `engines/benchmark.py` & `cli.py`

#### Changes:
1. Create `src/trace_lite/engines/benchmark.py` implementing `BenchmarkRunner`:
   - Computes: `Recall@5`, `Recall@20`, `nDCG@10`, `Complete Gold Coverage (%)`, `Citation Precision (%)`, `Abstention Accuracy (%)`, `Latency p50/p95 (ms)`.
2. Add CLI command in `src/trace_lite/cli.py`:
   ```bash
   tl benchmark --fixture <path> [--report-output <path>] [--json]
   ```

---

## 3. Verification Protocol

```bash
# 1. Run unit tests
uv run pytest tests/test_engines.py -o pythonpath=.
uv run pytest -o pythonpath=.

# 2. Run benchmark suite and lock report
uv run tl benchmark --fixture benchmarks/fixtures/private_indomain_v1.json --report-output ./report_alpha.json
```
