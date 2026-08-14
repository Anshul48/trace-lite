"""Hybrid Reciprocal Rank Fusion (BM25 + Dense) Baseline Retriever."""

from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate
from benchmarks.baselines.bm25_retriever import BM25Retriever
from benchmarks.baselines.dense_retriever import DenseRetriever


class HybridRRFRetriever(BaseRetriever):
    """
    Combines lexical BM25 and dense vector search via Reciprocal Rank Fusion (RRF).
    RRF score = sum(1.0 / (rrf_k + rank)) across ranking channels.
    """

    def __init__(self, dense_model: str = "all-MiniLM-L6-v2", rrf_k: int = 60):
        self.bm25 = BM25Retriever()
        self.dense = DenseRetriever(model_name=dense_model)
        self.rrf_k = rrf_k
        self.doc_map: dict[str, IndexedDocument] = {}

    def name(self) -> str:
        return f"hybrid_rrf_{self.dense.name()}"

    def index(self, documents: list[IndexedDocument]) -> None:
        self.doc_map = {d.doc_id: d for d in documents}
        self.bm25.index(documents)
        self.dense.index(documents)

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        fetch_k = max(top_k * 3, 50)
        bm25_results = self.bm25.retrieve(query, top_k=fetch_k)
        dense_results = self.dense.retrieve(query, top_k=fetch_k)

        rrf_scores: dict[str, float] = {}

        for rank, cand in enumerate(bm25_results, start=1):
            rrf_scores[cand.doc_id] = rrf_scores.get(cand.doc_id, 0.0) + (1.0 / (self.rrf_k + rank))

        for rank, cand in enumerate(dense_results, start=1):
            rrf_scores[cand.doc_id] = rrf_scores.get(cand.doc_id, 0.0) + (1.0 / (self.rrf_k + rank))

        sorted_ids = sorted(rrf_scores.keys(), key=lambda doc_id: rrf_scores[doc_id], reverse=True)

        candidates: list[RetrievalCandidate] = []
        for doc_id in sorted_ids[:top_k]:
            doc = self.doc_map.get(doc_id)
            candidates.append(
                RetrievalCandidate(
                    doc_id=doc_id,
                    score=rrf_scores[doc_id],
                    text=doc.text if doc else "",
                    metadata=doc.metadata if doc else {},
                )
            )
        return candidates
