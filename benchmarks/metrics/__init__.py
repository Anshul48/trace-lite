"""4-Dimension metric calculation and aggregation engine."""

from benchmarks.metrics.retrieval import (
    recall_at_k,
    complete_gold_coverage,
    citation_precision,
    ndcg_at_k,
    mrr_at_k,
    multihop_coverage,
    abstention_accuracy,
    CaseRetrievalMetrics,
)
from benchmarks.metrics.organization import (
    source_atom_coverage,
    orphan_count,
    duplicate_membership_count,
    routing_purity,
    OrganizationMetrics,
)
from benchmarks.metrics.operations import (
    LatencyTracker,
    MemoryTracker,
    StorageTracker,
    OpsProfile,
)
from benchmarks.metrics.aggregator import (
    BenchmarkRunResult,
    BenchmarkAggregator,
    generate_comparison_markdown,
)

__all__ = [
    "recall_at_k",
    "complete_gold_coverage",
    "citation_precision",
    "ndcg_at_k",
    "mrr_at_k",
    "multihop_coverage",
    "abstention_accuracy",
    "CaseRetrievalMetrics",
    "source_atom_coverage",
    "orphan_count",
    "duplicate_membership_count",
    "routing_purity",
    "OrganizationMetrics",
    "LatencyTracker",
    "MemoryTracker",
    "StorageTracker",
    "OpsProfile",
    "BenchmarkRunResult",
    "BenchmarkAggregator",
    "generate_comparison_markdown",
]
