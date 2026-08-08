"""Embedding model adapters."""

from typing import Protocol
import numpy as np


class EmbeddingAdapter(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        ...

    @property
    def dimension(self) -> int:
        ...


class SentenceTransformerEmbedder:
    """Default sentence-transformers embedder (e.g. all-MiniLM-L6-v2)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        self._load_model()
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return np.array(embeddings, dtype=np.float32)

    @property
    def dimension(self) -> int:
        if self.model_name == "all-MiniLM-L6-v2":
            return 384
        self._load_model()
        return self._model.get_sentence_embedding_dimension()


class MockEmbedder:
    """Deterministic mock embedder for testing."""

    def __init__(self, dim: int = 384):
        self._dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self._dim), dtype=np.float32)
        vectors = []
        for text in texts:
            # Hash text to generate deterministic vector
            seed = sum(ord(c) for c in text) % 1000
            np.random.seed(seed)
            vec = np.random.randn(self._dim).astype(np.float32)
            vec = vec / (np.linalg.norm(vec) + 1e-9)
            vectors.append(vec)
        return np.vstack(vectors)

    @property
    def dimension(self) -> int:
        return self._dim
