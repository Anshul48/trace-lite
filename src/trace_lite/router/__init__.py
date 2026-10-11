"""Sub-50ms 3-tier dual-dispatch retrieval router."""

from .beam import FacetedBeam
from .cascade import CascadeRouter, QueryResult, THETA_FLOOR
from .encoder import ENCODER_DIM, ENCODER_NAME, EncoderSpec, HashBowEncoder
from .fusion import K as RRF_K
from .fusion import rrf_fuse
from .hybrid import FlatHybrid
from .index_adapter import GENERATION_KIND, VectorIndex
from .learned import (
    ENCODER_CHUNKING,
    MODEL_ID,
    MODEL_REVISION,
    LearnedBgeEncoder,
    WeightsMissingError,
    pinned_spec as learned_pinned_spec,
    served_default,
    set_served_default,
)
from .lexical import extract_terms, has_lexical_support, is_syntax_dense, lexical_search
from .projection import build_and_publish, encode_batches, iter_atom_batches
from .vector_store import (
    S4_DELTA_BOUND,
    EmbeddingCache,
    ServedVectors,
    ensure_served_persistent,
)

__all__ = [
    "CascadeRouter",
    "ENCODER_DIM",
    "ENCODER_NAME",
    "EncoderSpec",
    "FacetedBeam",
    "FlatHybrid",
    "GENERATION_KIND",
    "ENCODER_CHUNKING",
    "HashBowEncoder",
    "LearnedBgeEncoder",
    "MODEL_ID",
    "MODEL_REVISION",
    "QueryResult",
    "THETA_FLOOR",
    "RRF_K",
    "VectorIndex",
    "ServedVectors",
    "EmbeddingCache",
    "S4_DELTA_BOUND",
    "WeightsMissingError",
    "ensure_served_persistent",
    "build_and_publish",
    "encode_batches",
    "extract_terms",
    "has_lexical_support",
    "is_syntax_dense",
    "iter_atom_batches",
    "learned_pinned_spec",
    "lexical_search",
    "rrf_fuse",
    "served_default",
    "set_served_default",
]
