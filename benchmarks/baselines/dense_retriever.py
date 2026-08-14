"""Flat Dense Vector Baseline Retriever using SentenceTransformers."""

import numpy as np
from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate


class DenseRetriever(BaseRetriever):
    """
    Flat dense nearest-neighbor retriever using sentence-transformers embeddings.
    Defaults to all-MiniLM-L6-v2, supports any SentenceTransformer model.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.documents: list[IndexedDocument] = []
        self.embeddings: np.ndarray | None = None
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def name(self) -> str:
        clean_model = self.model_name.split("/")[-1].replace("-", "_")
        return f"dense_{clean_model}"

    def index(self, documents: list[IndexedDocument]) -> None:
        self.documents = list(documents)
        if not self.documents:
            self.embeddings = None
            return

        model = self._get_model()
        texts = [f"{d.title} {d.text}".strip() for d in self.documents]
        raw_embs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        self.embeddings = np.ascontiguousarray(raw_embs, dtype=np.float32)

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        if self.embeddings is None or len(self.documents) == 0:
            return []

        model = self._get_model()
        q_emb = model.encode([query], convert_to_numpy=True, normalize_embeddings=True)[0]
        q_emb = np.ascontiguousarray(q_emb, dtype=np.float32)

        # Cosine similarity on unit vectors is simply dot product
        similarities = np.dot(self.embeddings, q_emb)
        top_indices = np.argsort(-similarities)[:top_k]

        results: list[RetrievalCandidate] = []
        for idx in top_indices:
            doc = self.documents[idx]
            results.append(
                RetrievalCandidate(
                    doc_id=doc.doc_id,
                    score=float(similarities[idx]),
                    text=doc.text,
                    metadata=doc.metadata,
                )
            )
        return results
