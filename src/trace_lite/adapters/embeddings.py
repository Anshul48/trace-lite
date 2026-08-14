"""Embedding model adapters."""

import threading
from typing import Protocol
import numpy as np


class EmbeddingAdapter(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:
        ...

    def warm_up(self) -> None:
        ...

    @property
    def dimension(self) -> int:
        ...


class SentenceTransformerEmbedder:
    """Default sentence-transformers embedder (e.g. all-MiniLM-L6-v2)."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._cache: dict[str, np.ndarray] = {}
        # Model construction can happen from a UI warm-up thread while a
        # request is embedding at the same time.  The double-check inside this
        # lock makes construction happen once per adapter instance.
        self._model_lock = threading.Lock()

    def _load_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.model_name)

    def warm_up(self) -> None:
        """Load the local embedding model without invoking an LLM provider."""
        self._load_model()

    @property
    def is_loaded(self) -> bool:
        """Whether the transformer instance has been constructed."""
        return self._model is not None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        uncached_indices: list[int] = []
        uncached_texts: list[str] = []
        results: list[np.ndarray | None] = [None] * len(texts)

        for i, text in enumerate(texts):
            if text in self._cache:
                results[i] = self._cache[text]
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            self._load_model()
            embeddings = self._model.encode(
                uncached_texts,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            embeddings_arr = np.array(embeddings, dtype=np.float32)
            for idx, emb, text in zip(uncached_indices, embeddings_arr, uncached_texts):
                self._cache[text] = emb
                results[idx] = emb

        return np.array(results, dtype=np.float32)

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

    def warm_up(self) -> None:
        """Keep the adapter contract; deterministic mocks have nothing to load."""
        return None

    @property
    def dimension(self) -> int:
        return self._dim
