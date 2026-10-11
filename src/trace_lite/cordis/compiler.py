"""Micro-step context compiler: budget-bounded prompt assembly, highest authority first."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from .models import (
    CommitmentRecord,
    CompiledContext,
    EvidencePacket,
    TokenBudget,
    estimate_tokens,
)
from .tms import ModularContractBoundaryTMS

DEFAULT_THRESHOLD = 0.25
RPE_STEP = 0.05
THRESHOLD_FLOOR = 0.05
THRESHOLD_CEILING = 0.80


def _salience(candidate: dict[str, Any], goal_terms: set[str]) -> float:
    text = f"{candidate.get('title', '')} {candidate.get('content', '')}".lower()
    if not text.strip():
        return 0.0
    terms = {t for t in text.replace("/", " ").split() if len(t) > 2}
    if not terms or not goal_terms:
        return 0.0
    overlap = len(terms & goal_terms) / len(terms | goal_terms)
    authority_boost = float(candidate.get("authority_weight", 0.0))
    return min(1.0, overlap + authority_boost)


class TraceLiteContextCompiler:
    """CBGTC-style D1-go/D2-no-go gating + greedy budget packing under 3,500 tokens."""

    def __init__(self, tms: ModularContractBoundaryTMS | None = None) -> None:
        self.tms = tms if tms is not None else ModularContractBoundaryTMS()
        self.theta_gate = DEFAULT_THRESHOLD

    def evaluate_salience(
        self, evidence_candidates: list[dict[str, Any]], goal_state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        goal_terms = {
            t.lower() for t in str(goal_state.get("objective", "")).split() if len(t) > 2
        }
        signals = []
        for candidate in evidence_candidates:
            score = _salience(candidate, goal_terms)
            pathway = "D1_GO" if score >= self.theta_gate else "D2_NO_GO"
            signals.append(
                {
                    "candidate_id": str(candidate.get("artifact_id", "?")),
                    "pathway": pathway,
                    "computed_salience": score,
                    "threshold": self.theta_gate,
                }
            )
        return signals

    def update_gating_threshold(self, rpe_signal: float) -> float:
        """Dopaminergic analog: positive surprise lowers the gate, negative raises it."""
        self.theta_gate = min(
            THRESHOLD_CEILING,
            max(THRESHOLD_FLOOR, self.theta_gate - RPE_STEP * rpe_signal),
        )
        return self.theta_gate

    def compile_context(
        self,
        step_id: str,
        goal_id: str,
        target_scopes: list[str],
        proposed_op: str,
        budget: TokenBudget,
        raw_candidates: list[dict[str, Any]],
        current_step: int = 0,
        goal_state: dict[str, Any] | None = None,
    ) -> CompiledContext:
        """Assemble a budget-bounded prompt context.

        R07: ranking uses the objective TEXT — ``goal_state['objective']``
        when supplied, else the legacy ``goal_id`` value treated as the
        objective (pre-R07 callers pass objective-like ids). The opaque
        ``goal_id`` label NEVER feeds ranking on its own: the same
        objective under different ids ranks identically (C7-1).
        """
        from .dossier import anchored_excerpt

        start = time.perf_counter()
        objective = (
            str(goal_state.get("objective", ""))
            if goal_state is not None
            else str(goal_id)
        )
        invariants = self.tms.get_active_commitments(target_scopes, current_step)
        # Mandatory invariants first: highest authority wins the reservation.
        # Overflow is an explicit failure naming every omitted invariant —
        # applicable invariants are never silently dropped (ARCH §7).
        mandatory: list[CommitmentRecord] = []
        used = 0
        mandatory_costs: list[int] = []
        for record in invariants:
            cost = estimate_tokens(f"[invariant {record.id}] {record.statement}")
            mandatory_costs.append(cost)
            mandatory.append(record)
            used += cost
        goal_terms = {
            t.lower() for t in objective.replace("/", " ").split() if len(t) > 2
        }
        elapsed = lambda: (time.perf_counter() - start) * 1000.0
        # A zero mandatory reservation means "fit under the ceiling" (legacy
        # default budgets carry no explicit partition); an explicit positive
        # reservation is enforced strictly with named omissions on overflow.
        reservation = budget.mandatory_invariant_tokens
        if (reservation > 0 and used > reservation) or (
            budget.system_prompt_tokens + used + estimate_tokens(objective)
            > budget.max_total_tokens
        ):
            return CompiledContext(
                step_id=step_id,
                goal_id=goal_id,
                target_scopes=target_scopes,
                proposed_operation_type=proposed_op,
                budget_profile=budget,
                mandatory_invariants=[],
                compiled_evidence=[],
                distractor_rejection_count=len(raw_candidates),
                gating_threshold_used=self.theta_gate,
                compilation_duration_ms=elapsed(),
                status="insufficient_budget",
                omitted_invariant_ids=[r.id for r in invariants],
                goal_objective=objective,
            )
        scored = sorted(
            raw_candidates,
            key=lambda c: -_salience(c, goal_terms),
        )
        # Zero salient budget = remainder (same rule as the dossier compiler).
        salient = budget.salient_evidence_budget
        allowance = (
            min(
                salient,
                budget.max_total_tokens
                - (budget.system_prompt_tokens + used + estimate_tokens(objective)),
            )
            if salient > 0
            else budget.max_total_tokens
            - (budget.system_prompt_tokens + used + estimate_tokens(objective))
        )
        evidence: list[EvidencePacket] = []
        allocated = 0
        rejected = 0
        for rank, candidate in enumerate(scored):
            content = str(candidate.get("content", ""))
            remainder = allowance - allocated
            excerpt, sb, eb, is_ex = anchored_excerpt(content, objective, remainder)
            cost = estimate_tokens(
                f"[{candidate.get('artifact_id', '?')} {sb}:{eb}] {excerpt}"
            )
            if allocated + cost > allowance:
                rejected += 1
                continue
            evidence.append(
                EvidencePacket(
                    artifact_id=str(candidate.get("artifact_id", "?")),
                    source_uri=str(candidate.get("source_uri", "")),
                    content=excerpt,
                    token_cost=cost,
                    salience_score=_salience(candidate, goal_terms),
                    provenance_hash=hashlib.sha256(content.encode()).hexdigest(),
                    excerpt_start_byte=sb,
                    excerpt_end_byte=eb,
                    is_excerpt=is_ex,
                    selection_reason=(
                        f"ranked@{rank} objective-text relevance; "
                        f"query-anchored excerpt, never prefix truncation"
                    ),
                )
            )
            allocated += cost
        profile = budget.model_copy(update={"allocated_evidence_tokens": allocated})
        return CompiledContext(
            step_id=step_id,
            goal_id=goal_id,
            target_scopes=target_scopes,
            proposed_operation_type=proposed_op,
            budget_profile=profile,
            mandatory_invariants=mandatory,
            compiled_evidence=evidence,
            distractor_rejection_count=rejected,
            gating_threshold_used=self.theta_gate,
            compilation_duration_ms=elapsed(),
            status="ok",
            omitted_invariant_ids=[],
            goal_objective=objective,
        )
