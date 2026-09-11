"""Sub-50ms 3-tier dual-dispatch retrieval router."""

from .beam import FacetedBeam
from .cascade import CascadeRouter, QueryResult, THETA_FLOOR
from .fusion import K as RRF_K
from .fusion import rrf_fuse
from .hybrid import FlatHybrid
from .lexical import extract_terms, has_lexical_support, is_syntax_dense, lexical_search

__all__ = [
    "CascadeRouter",
    "FacetedBeam",
    "FlatHybrid",
    "QueryResult",
    "THETA_FLOOR",
    "RRF_K",
    "extract_terms",
    "has_lexical_support",
    "is_syntax_dense",
    "lexical_search",
    "rrf_fuse",
]
