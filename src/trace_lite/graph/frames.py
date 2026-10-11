"""Typed assertion frames (ARCHITECTURE §2 logical records, R04 local scope).

An :class:`AssertionFrame` is one source-asserted interpretation of a single
span-anchored sentence: subject/predicate/object plus polarity, modality,
conditions, units, temporal/version scope and epistemic status. N-ary detail
(subject, object, condition, temporal, unit, qualifier, reporter) rides on
:class:`EntityMention` rows linked by role; :class:`RelationEdge` rows carry
the typed directed edge (causal, comparison, reporting, qualification) with
its own span anchor. Unknown fields stay ``None`` — never invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EPISTEMIC_SOURCE_ASSERTED = "source_asserted"
EPISTEMIC_VALIDATED = "validated"
EPISTEMIC_HYPOTHESIZED = "hypothesized"

POLARITY_POSITIVE = "positive"
POLARITY_NEGATIVE = "negative"

MODALITY_ASSERTED = "asserted"
MODALITY_REQUIRED = "required"
MODALITY_POSSIBLE = "possible"

STATUS_ASSERTED = "asserted"
STATUS_UNRESOLVED = "unresolved"

#: Frozen local relation vocabulary (R04). R05 may add cross-source types but
#: must not reinterpret these: every value means "the source text structurally
#: asserts this", never a validated entailment. Lexical similarity alone never
#: produces any of these rows.
REL_TYPES = frozenset(
    {
        "improves",
        "degrades",
        "restores",
        "requires",
        "conflicts_with",
        "compares",
        "reports",
        "allows",
        "uses",
        "dominates",
        "defines",
        "qualifies",
        "note",
    }
)

#: Mention roles for n-ary assertion arguments.
MENTION_ROLES = frozenset(
    {
        "subject",
        "object",
        "condition",
        "temporal",
        "unit",
        "qualifier",
        "reporter",
    }
)


@dataclass
class EntityMention:
    """One entity/qualifier surface form anchored to span bytes."""

    mention_id: str
    surface: str
    role: str
    span_id: str
    start_byte: int
    end_byte: int
    canonical_key: str | None = None
    mapping_version: str = ""
    status: str = STATUS_ASSERTED


@dataclass
class AssertionFrame:
    """One typed assertion with span provenance and scope fields."""

    assertion_id: str
    ordinal: int
    predicate: str
    polarity: str = POLARITY_POSITIVE
    modality: str = MODALITY_ASSERTED
    epistemic_status: str = EPISTEMIC_SOURCE_ASSERTED
    subject_text: str | None = None
    object_text: str | None = None
    condition_text: str | None = None
    units: str | None = None
    temporal_scope: str | None = None
    version_scope: str | None = None
    span_id: str = ""
    start_byte: int = 0
    end_byte: int = 0
    status: str = STATUS_ASSERTED
    mentions: list[EntityMention] = field(default_factory=list)
    #: Extra verbatim qualifier surfaces (parentheticals, "with ... jitter"
    #: phrases): stored as arguments, never promoted to causal claims.
    qualifier_texts: list[str] = field(default_factory=list)


@dataclass
class RelationEdge:
    """One typed directed edge between assertion surfaces, span-anchored."""

    relation_id: str
    rel_type: str
    subject_text: str | None
    object_text: str | None
    subject_mention_id: str | None = None
    subject_assertion_id: str | None = None
    object_mention_id: str | None = None
    object_assertion_id: str | None = None
    polarity: str = POLARITY_POSITIVE
    condition_text: str | None = None
    epistemic_status: str = EPISTEMIC_SOURCE_ASSERTED
    span_id: str = ""
    start_byte: int = 0
    end_byte: int = 0
    status: str = STATUS_ASSERTED
    #: Bounded retrieval candidates recorded when (and only when) the binding
    #: was left unresolved: evidence of the attempt, not a forced link.
    candidates: list[str] = field(default_factory=list)
