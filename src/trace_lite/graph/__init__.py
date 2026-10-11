"""Source-local evidence graphs (R04): typed assertion frames over R02 spans.

Every record is source-local and evidence-anchored: assertions and relations
resolve to R02 ``evidence_span`` IDs with half-open UTF-8 byte ranges plus the
``extraction_run`` that produced them. Nothing here is a global fact and
nothing is validated — ``epistemic_status`` is ``source_asserted`` unless real
validation lineage exists (it does not in R04), in which case the run records
it explicitly. Ambiguous bindings persist as ``status='unresolved'``.
"""

from .extractor import (
    SCHEMA_VERSION,
    ExtractResult,
    extracted_assertion_to_row,
    extract_spans,
    find_candidates,
)
from .frames import (
    EPISTEMIC_HYPOTHESIZED,
    EPISTEMIC_SOURCE_ASSERTED,
    EPISTEMIC_VALIDATED,
    MODALITY_ASSERTED,
    MODALITY_POSSIBLE,
    MODALITY_REQUIRED,
    POLARITY_NEGATIVE,
    POLARITY_POSITIVE,
    STATUS_ASSERTED,
    STATUS_UNRESOLVED,
    AssertionFrame,
    EntityMention,
    RelationEdge,
)
from .store import (
    check_integrity,
    create_extraction_run,
    get_graph,
    persist_graph,
)

__all__ = [
    "SCHEMA_VERSION",
    "EPISTEMIC_HYPOTHESIZED",
    "EPISTEMIC_SOURCE_ASSERTED",
    "EPISTEMIC_VALIDATED",
    "MODALITY_ASSERTED",
    "MODALITY_POSSIBLE",
    "MODALITY_REQUIRED",
    "POLARITY_NEGATIVE",
    "POLARITY_POSITIVE",
    "STATUS_ASSERTED",
    "STATUS_UNRESOLVED",
    "AssertionFrame",
    "EntityMention",
    "RelationEdge",
    "ExtractResult",
    "extract_spans",
    "extracted_assertion_to_row",
    "find_candidates",
    "check_integrity",
    "create_extraction_run",
    "get_graph",
    "persist_graph",
]
