"""Base interfaces and data structures for benchmark baseline retrievers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IndexedDocument:
    """Document representation passed to indexers."""
    doc_id: str
    text: str
    title: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class RetrievalCandidate:
    """Individual candidate returned from retrieval search."""
    doc_id: str
    score: float
    text: str = ""
    metadata: dict = field(default_factory=dict)


class BaseRetriever(ABC):
    """Abstract interface for baseline and experimental retrieval engines."""

    @abstractmethod
    def name(self) -> str:
        """Name of the baseline engine (e.g. 'bm25', 'dense_minilm', 'hybrid_rrf', 'trace_tree')."""
        pass

    @abstractmethod
    def index(self, documents: list[IndexedDocument]) -> None:
        """Index the collection of documents."""
        pass

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        """Retrieve top_k candidates for the query."""
        pass
