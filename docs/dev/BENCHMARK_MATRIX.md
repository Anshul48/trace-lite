# Benchmark Matrix & Evaluation Protocol

## 1. The 320-Query In-Domain Frozen Benchmark Suite

The private in-domain benchmark contains 320 reviewed queries across 8 categories (40 queries each):

| Category ID | Category Name | Description | Key Metric | Target Pass Gate |
|---|---|---|---|:---:|
| `CAT_01` | **Direct Lookup** | Single-hop exact factual needle in a specific atom. | Top-1 Accuracy / Precision@1 | $\ge 92\%$ |
| `CAT_02` | **Chronology & Order** | Queries requiring sequential before/after temporal order. | Sequence Recall@5 | $\ge 85\%$ |
| `CAT_03` | **Contradiction & Revision** | Newer architectural decisions overriding older notes. | Freshness Precision | $\ge 88\%$ |
| `CAT_04` | **Cross-Domain** | Concepts linking two disparate topics/documents. | Cross-tree Recall@5 | $\ge 80\%$ |
| `CAT_05` | **Global Context** | Broad architectural themes requiring high-level summary. | Tree Root nDCG@10 | $\ge 85\%$ |
| `CAT_06` | **Multi-Hop Evidence** | Questions requiring 2–4 distinct hops across documents. | Complete Gold Coverage | $\ge 78\%$ |
| `CAT_07` | **Historical & Versioned** | Distinguishing past prototype state from current V2 state. | Provenance Precision | $\ge 85\%$ |
| `CAT_08` | **Insufficient Evidence** | Out-of-scope or unanswerable queries (Abstention test). | Abstention Accuracy | $\ge 95\%$ |

---

## 2. Evaluation Metrics & Mathematical Formulas

1. **Recall@K**:
   $$\text{Recall@K} = \frac{|\text{Retrieved Top-K Atoms} \cap \text{Gold Atoms}|}{|\text{Gold Atoms}|}$$
2. **Complete Gold Evidence Coverage**:
   $$\text{Complete Coverage} = \frac{1}{N} \sum_{i=1}^N \mathbb{I}\Big(\text{Gold Atoms}_i \subseteq \text{Retrieved Top-K Atoms}_i\Big)$$
3. **Citation Precision**:
   $$\text{Precision} = \frac{|\text{Retrieved Top-K Atoms} \cap \text{Gold Atoms}|}{|\text{Retrieved Top-K Atoms}|}$$
4. **Normalized Discounted Cumulative Gain (nDCG@K)**:
   $$\text{DCG@K} = \sum_{i=1}^K \frac{2^{\text{rel}_i} - 1}{\log_2(i + 1)}, \quad \text{nDCG@K} = \frac{\text{DCG@K}}{\text{IDCG@K}}$$
5. **Abstention Correctness**:
   $$\text{Abstention Accuracy} = \frac{\text{Correctly Abstaining Out-of-Scope Queries}}{\text{Total Out-of-Scope Queries}}$$

---

## 3. Public Benchmark Evaluation Mapping

| Benchmark | Task / Slice | Metric Focus | Target Score |
|---|---|---|:---:|
| **HiCBench / HiChunk** | Hierarchical Chunk Retrieval | Multi-level tree recall & passage boundary precision | SOTA Competitive |
| **MultiHop-RAG** | Multi-hop QA Retrieval | 2–4 hop cross-document evidence recall | $+20\%$ over flat vector |
| **BEIR Subset** | SciFact, FiQA, NFCorpus | Zero-shot nDCG@10 | $\ge \text{BM25 + BGE}$ baseline |
| **BRIGHT** | Reasoning-intensive queries | Multi-candidate nDCG@10 | Superior to pure nearest-neighbor |
