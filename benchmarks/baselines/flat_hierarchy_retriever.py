"""Structure-only parent/child hierarchy baseline (without LLM-generated summaries)."""

import numpy as np
from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate
from benchmarks.baselines.dense_retriever import DenseRetriever


class FlatHierarchyRetriever(BaseRetriever):
    """
    Evaluates a 2-level structural hierarchy using vector centroid clustering
    WITHOUT LLM-generated text summaries.
    Isolates the performance delta of tree routing vs generated summary faithfulness.
    """

    def __init__(self, dense_model: str = "all-MiniLM-L6-v2", cluster_size: int = 5):
        self.dense_model = dense_model
        self.cluster_size = cluster_size
        self.documents: list[IndexedDocument] = []
        self.dense = DenseRetriever(model_name=dense_model)
        self.cluster_centroids: np.ndarray | None = None
        self.cluster_members: list[list[int]] = []

    def name(self) -> str:
        return "flat_hierarchy_no_llm"

    def index(self, documents: list[IndexedDocument]) -> None:
        self.documents = list(documents)
        if not self.documents:
            self.cluster_centroids = None
            self.cluster_members = []
            return

        self.dense.index(documents)
        embs = self.dense.embeddings
        if embs is None or len(embs) == 0:
            return

        # Simple contiguous/k-means centroid grouping
        n_docs = len(self.documents)
        n_clusters = max(1, (n_docs + self.cluster_size - 1) // self.cluster_size)

        self.cluster_members = []
        centroids = []

        for c_idx in range(n_clusters):
            start = c_idx * self.cluster_size
            end = min(start + self.cluster_size, n_docs)
            members = list(range(start, end))
            self.cluster_members.append(members)
            c_emb = np.mean(embs[members], axis=0)
            norm = np.linalg.norm(c_emb)
            if norm > 0:
                c_emb = c_emb / norm
            centroids.append(c_emb)

        self.cluster_centroids = np.ascontiguousarray(centroids, dtype=np.float32)

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        if self.cluster_centroids is None or not self.documents:
            return []

        model = self.dense._get_model()
        q_emb = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
        q_emb = np.ascontiguousarray(q_emb, dtype=np.float32)

        # 1. Score cluster centroids
        cluster_scores = np.dot(self.cluster_centroids, q_emb)
        top_clusters = np.argsort(-cluster_scores)[:max(2, (top_k // 2))]

        # 2. Score candidate items within top clusters
        candidate_indices = []
        for c_idx in top_clusters:
            candidate_indices.extend(self.cluster_members[c_idx])

        if not candidate_indices:
            return []

        candidate_embs = self.dense.embeddings[candidate_indices]
        item_scores = np.dot(candidate_embs, q_emb)
        ranked = np.argsort(-item_scores)[:top_k]

        results: list[RetrievalCandidate] = []
        for r_idx in ranked:
            doc_idx = candidate_indices[r_idx]
            doc = self.documents[doc_idx]
            results.append(
                RetrievalCandidate(
                    doc_id=doc.doc_id,
                    score=float(item_scores[r_idx]),
                    text=doc.text,
                    metadata=doc.metadata,
                )
            )
        return results
