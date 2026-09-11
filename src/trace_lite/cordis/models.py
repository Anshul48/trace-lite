"""Cordis/DSH contract models: Pydantic 2 mirror of the formal interfaces spec.

Field-compatible with
`research_specifications/schemas/interfaces.py` (see conformance test); the
Desktop path is not a runtime dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CONTEXT_TOKENS = 3500


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuthorityLevel(str, Enum):
    USER = "USER"
    ARCH_SPEC = "ARCH_SPEC"
    AGENT_DECISION = "AGENT_DECISION"
    TOOL_OUTPUT = "TOOL_OUTPUT"

    @property
    def rank(self) -> int:
        return {
            AuthorityLevel.TOOL_OUTPUT: 1,
            AuthorityLevel.AGENT_DECISION: 2,
            AuthorityLevel.ARCH_SPEC: 3,
            AuthorityLevel.USER: 4,
        }[self]

    def dominates(self, other: AuthorityLevel) -> bool:
        return self.rank >= other.rank

    def strictly_dominates(self, other: AuthorityLevel) -> bool:
        return self.rank > other.rank


class CommitmentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    SUSPENDED = "SUSPENDED"
    EXEMPTION = "EXEMPTION"


class EvaluationVerdict(str, Enum):
    SAT = "SAT"
    VIOLATED = "VIOLATED"
    EXEMPT = "EXEMPT"
    INAPPLICABLE = "INAPPLICABLE"


class GateVerdictEnum(str, Enum):
    ACCEPT = "ACCEPT"
    VETO = "VETO"
    REQUEST_REFINEMENT = "REQUEST_REFINEMENT"


class VerificationTier(str, Enum):
    TIER_0_SUBSTRATE = "TIER_0_SUBSTRATE"
    TIER_1_NLI = "TIER_1_NLI"
    TIER_2_JUDGE = "TIER_2_JUDGE"


class DependencyNodeType(str, Enum):
    BELIEF = "BELIEF"
    COMMITMENT = "COMMITMENT"
    DERIVATION = "DERIVATION"
    ARTIFACT = "ARTIFACT"


class NodeStatus(str, Enum):
    VALID = "VALID"
    STALE_PENDING_EVALUATION = "STALE_PENDING_EVALUATION"
    INVALID = "INVALID"
    DIRTY = "DIRTY"


class ProposedOperationType(str, Enum):
    FILE_CREATE = "file_create"
    FILE_EDIT = "file_edit"
    FILE_DELETE = "file_delete"
    SHELL_EXEC = "shell_exec"
    STATE_TRANSITION = "state_transition"
    CONTRACT_MUTATION = "contract_mutation"


class InvariantVetoError(PermissionError):
    """A lower authority attempted to revoke/relax a higher authority invariant."""


class ScopeExemption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    exemption_id: str
    sub_scope: str
    condition_predicate: str = "True"
    authorized_by: AuthorityLevel
    expiry_step: int | None = None


class JustificationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(default_factory=lambda: uuid4().hex)
    commitment_id: str
    authority: AuthorityLevel
    evidence_hash: str
    rationale: str
    timestamp: str = Field(default_factory=utcnow)
    signature: str | None = None


class CommitmentRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    target_scopes: list[str]
    authority: AuthorityLevel
    statement: str
    formal_predicate: str | None = None
    created_at_step: int = 0
    superseded_at_step: int | None = None
    superseded_by: str | None = None
    version: int = 1
    status: CommitmentStatus = CommitmentStatus.ACTIVE
    exemptions: list[ScopeExemption] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_scopes")
    @classmethod
    def _nonempty_scopes(cls, scopes: list[str]) -> list[str]:
        if not scopes:
            raise ValueError("commitment must govern at least one scope")
        return scopes


class InvariantEvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    target_scope: str
    verdict: EvaluationVerdict
    diagnostic_message: str | None = None
    violation_ast_node: str | None = None
    repair_hint: str | None = None


class TraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_offset: int | None = None
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    stream_id: str
    stream_sequence: int = 0
    event_type: str
    occurred_at: str = Field(default_factory=utcnow)
    actor: str = "agent"
    causation_id: str | None = None
    correlation_id: str | None = None
    schema_version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = ""
    idempotency_key: str | None = None


class AtomRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int | None = None
    doc_id: str
    file_id: str = ""
    start_byte: int = 0
    end_byte: int = 0
    content_hash: str = ""
    text: str


class FacetedQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    facets: list[str] | None = None
    as_of_event: int | None = None
    token_budget: int = 2500
    candidate_limit: int = 20
    allowed_visibility: list[str] | None = None


class EvidenceAnchor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_id: str = Field(default_factory=lambda: uuid4().hex)
    atom_version_id: str = ""
    file_id: str = ""
    doc_id: str = ""
    start_byte: int = 0
    end_byte: int = 0
    content_hash: str = ""
    snippet_text: str = ""


class QueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    anchors: list[EvidenceAnchor] = Field(default_factory=list)
    disagreements: list[dict[str, str]] = Field(default_factory=list)
    tier_used: Literal[1, 2, 3] = 3
    elapsed_ms: float = 0.0
    sufficiency_state: Literal["answerable", "insufficient_evidence"] = "insufficient_evidence"


class RCULeaseReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pid: int
    lease_id: str
    epoch: int = 0
    acquired_at: float = 0.0
    expires_at: float = 0.0


class EvidencePacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    source_uri: str = ""
    representation_tier: str = "HOLON_SUMMARY"
    content: str
    token_cost: int = 1
    salience_score: float = 0.0
    provenance_hash: str = ""


class TokenBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_total_tokens: int = MAX_CONTEXT_TOKENS
    system_prompt_tokens: int = 800
    mandatory_invariant_tokens: int = 0
    goal_state_tokens: int = 200
    salient_evidence_budget: int = 0
    allocated_evidence_tokens: int = 0

    @model_validator(mode="after")
    def _ceiling(self) -> TokenBudget:
        total = (
            self.system_prompt_tokens
            + self.mandatory_invariant_tokens
            + self.goal_state_tokens
            + self.allocated_evidence_tokens
        )
        if total > self.max_total_tokens:
            raise ValueError(f"allocated {total} exceeds max {self.max_total_tokens}")
        return self


class CompiledContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    step_id: str
    goal_id: str
    target_scopes: list[str]
    proposed_operation_type: str = ""
    budget_profile: TokenBudget
    mandatory_invariants: list[CommitmentRecord] = Field(default_factory=list)
    compiled_evidence: list[EvidencePacket] = Field(default_factory=list)
    distractor_rejection_count: int = 0
    gating_threshold_used: float = 0.0
    compilation_duration_ms: float = 0.0


class ActionProposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str = Field(default_factory=lambda: uuid4().hex)
    step_id: str = "0"
    task_id: str = ""
    target_scopes: list[str]
    target_entities: list[str] = Field(default_factory=list)
    proposed_operation: ProposedOperationType = ProposedOperationType.STATE_TRANSITION
    operation_payload: dict[str, Any] = Field(default_factory=dict)
    predicted_postconditions: list[str] = Field(default_factory=list)
    invariant_proof_claims: dict[str, str] = Field(default_factory=dict)


class GateVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_id: str = Field(default_factory=lambda: uuid4().hex)
    action_id: str
    verdict: GateVerdictEnum
    tier_reached: VerificationTier = VerificationTier.TIER_0_SUBSTRATE
    violated_invariants: list[str] = Field(default_factory=list)
    repair_instruction: str | None = None
    affected_downstream_scopes: list[str] = Field(default_factory=list)
    cryptographic_receipt: str = ""


class VerificationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipt_id: str = Field(default_factory=lambda: uuid4().hex)
    action_id: str
    tier: VerificationTier
    passed: bool
    execution_time_ms: float = 0.0
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class NegativeInhibitorGene(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gene_id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_hash: str
    violated_invariant_id: str
    violating_operation: str
    diagnostic: str
    status: Literal["REFUTED", "ACTIVE_INHIBITOR"] = "REFUTED"
    created_at_step: int = 0


class DependencyNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str
    node_type: DependencyNodeType = DependencyNodeType.BELIEF
    label: str = ""
    scope: str = "global"
    contract_membrane: dict[str, Any] | None = None
    outgoing_dependencies: list[str] = Field(default_factory=list)
    incoming_dependents: list[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.VALID
    metadata: dict[str, Any] = Field(default_factory=dict)


class RevocationEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    revocation_id: str = Field(default_factory=lambda: uuid4().hex)
    target_node_id: str
    reason: str = ""
    authority: AuthorityLevel = AuthorityLevel.AGENT_DECISION
    timestamp_step: int = 0
    justification_hash: str = ""


class InvalidationCascadeResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initiating_event_id: str
    tainted_nodes: list[str] = Field(default_factory=list)
    invalidated_nodes: list[str] = Field(default_factory=list)
    pruned_nodes: list[str] = Field(default_factory=list)
    tombstones_emitted: list[str] = Field(default_factory=list)
    propagation_depth_reached: int = 0
    execution_time_ms: float = 0.0
