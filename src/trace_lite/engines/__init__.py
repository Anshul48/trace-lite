"""Engines module: RAPTOR tree builder, Forest Router, and LATTICE tree traversal."""

from trace_lite.engines.raptor import RaptorEngine, SummaryGenerationError
from trace_lite.engines.router import ForestRouter, TreeNamingError
from trace_lite.engines.lattice import LatticeEngine, QueryResult, EvidenceItem
from trace_lite.engines.graph import (
    GraphActivationEngine,
    generate_sequential_edges,
    generate_hierarchical_edges,
    generate_co_occurrence_edges,
    generate_all_edges,
)
from trace_lite.engines.summary import (
    SummaryResult,
    SummaryValidationResult,
    TitleValidationResult,
    ValidationResult,
    is_meaningful_summary,
    normalize_markdown_for_summary,
    strip_transport_wrappers,
    validate_summary,
    validate_title,
)

from trace_lite.engines.benchmark import (
    BenchmarkRunner,
    BenchmarkReport,
    CaseEvaluationResult,
    CategorySummary,
)

__all__ = [
    "RaptorEngine", "SummaryGenerationError", "ForestRouter", "TreeNamingError", "LatticeEngine", "QueryResult", "EvidenceItem",
    "GraphActivationEngine", "generate_sequential_edges", "generate_hierarchical_edges", "generate_co_occurrence_edges", "generate_all_edges",
    "BenchmarkRunner", "BenchmarkReport", "CaseEvaluationResult", "CategorySummary",
    "SummaryResult", "is_meaningful_summary",
    "normalize_markdown_for_summary", "strip_transport_wrappers",
    "ValidationResult", "SummaryValidationResult", "TitleValidationResult",
    "validate_summary", "validate_title",
]

