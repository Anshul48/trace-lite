"""Base protocol and data models for benchmark dataset adapters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CorpusDocument:
    """A document or passage in a benchmark corpus."""
    doc_id: str
    title: str = ""
    text: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class BenchmarkQuery:
    """A query in a benchmark with ground-truth relevant document IDs."""
    query_id: str
    text: str
    gold_doc_ids: list[str] = field(default_factory=list)
    relevance_scores: dict[str, int] = field(default_factory=dict)
    category: str = "general"
    metadata: dict = field(default_factory=dict)


class BaseDatasetAdapter(ABC):
    """Abstract interface for public benchmark suite loaders."""

    def __init__(self, cache_dir: Path | str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def dataset_name(self) -> str:
        """Name of the dataset (e.g. 'scifact', 'bright-coding', 'multihop-rag')."""
        pass

    @abstractmethod
    def download_or_prepare(self) -> None:
        """Download or extract dataset to cache if not already present."""
        pass

    @abstractmethod
    def load_corpus(self) -> list[CorpusDocument]:
        """Load and return all corpus documents."""
        pass

    @abstractmethod
    def load_queries(self) -> list[BenchmarkQuery]:
        """Load and return evaluation queries with gold document IDs."""
        pass
