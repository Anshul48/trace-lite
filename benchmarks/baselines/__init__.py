"""Baseline retrievers for benchmark evaluation."""

from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate
from benchmarks.baselines.bm25_retriever import BM25Retriever
from benchmarks.baselines.dense_retriever import DenseRetriever
from benchmarks.baselines.hybrid_rrf_retriever import HybridRRFRetriever
from benchmarks.baselines.flat_hierarchy_retriever import FlatHierarchyRetriever
from benchmarks.baselines.trace_retriever import TraceLiteRetriever
from benchmarks.baselines.hipporag_retriever import HippoRagPPRRetriever

__all__ = [
    "BaseRetriever",
    "IndexedDocument",
    "RetrievalCandidate",
    "BM25Retriever",
    "DenseRetriever",
    "HybridRRFRetriever",
    "FlatHierarchyRetriever",
    "TraceLiteRetriever",
    "HippoRagPPRRetriever",
]
