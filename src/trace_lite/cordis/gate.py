"""Pre-commit step acceptance gate: 3-tier cascade + RPE feedback."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from .models import (
    ActionProposal,
    CompiledContext,
    GateVerdict,
    GateVerdictEnum,
    NegativeInhibitorGene,
    ProposedOperationType,
    VerificationReceipt,
    VerificationTier,
    utcnow,
)
from .tms import ModularContractBoundaryTMS

_RPE_ACCEPT = 1.0
_RPE_REJECT = -1.0
_SECRET = b"trace-lite-step-gate"


def _receipt(action_id: str, verdict: GateVerdictEnum, tier: VerificationTier) -> str:
    return hmac.new(_SECRET, f"{action_id}:{verdict.value}:{tier.value}".encode(), hashlib.sha256).hexdigest()


class StepAcceptanceGate:
    """Tier 0 substrate → Tier 1 invariant/inhibitor screen → Tier 2 formal re-check."""

    def __init__(self, tms: ModularContractBoundaryTMS) -> None:
        self.tms = tms
        self.rpe_delta = 0.0
        self.inhibitors: list[NegativeInhibitorGene] = []
        self.committed: dict[str, ActionProposal] = {}
        self.current_step = 0

    # -- tiers ---------------------------------------------------------
    def execute_tier0_substrate(self, proposal: ActionProposal) -> VerificationReceipt:
        start = time.perf_counter()
        diagnostics: dict[str, Any] = {}
        passed = True
        if not proposal.target_scopes:
            passed, diagnostics["empty_scopes"] = False, "proposal governs no scope"
        if not isinstance(proposal.proposed_operation, ProposedOperationType):
            passed, diagnostics["bad_operation"] = False, "unknown operation type"
        if not proposal.operation_payload:
            passed, diagnostics["empty_payload"] = False, "no concrete mutation payload"
        return VerificationReceipt(
            action_id=proposal.action_id, tier=VerificationTier.TIER_0_SUBSTRATE,
            passed=passed, execution_time_ms=(time.perf_counter() - start) * 1000.0,
            diagnostics=diagnostics,
        )

    def execute_tier1_nli(
        self, proposal: ActionProposal, context: CompiledContext
    ) -> VerificationReceipt:
        """Deterministic lexical screen: proposal text vs invariant denials + inhibitor genes."""
        start = time.perf_counter()
        text = " ".join(
            [proposal.proposed_operation.value, str(proposal.operation_payload),
             *proposal.predicted_postconditions]
        ).lower()
        violated = [
            record.id
            for record in context.mandatory_invariants
            if self._contradicts(record.statement.lower(), text)
        ]
        gene_hits = [g.gene_id for g in self.inhibitors if g.violating_operation.lower() in text]
        verdict = not violated and not gene_hits
        return VerificationReceipt(
            action_id=proposal.action_id, tier=VerificationTier.TIER_1_NLI, passed=verdict,
            execution_time_ms=(time.perf_counter() - start) * 1000.0,
            diagnostics={"violated": violated, "inhibitor_hits": gene_hits},
        )

    @staticmethod
    def _contradicts(statement: str, proposal_text: str) -> bool:
        # A "MUST/NEVER/SHALL" invariant is contradicted when the proposal names a
        # forbidden action the invariant guards against.
        guards = [w for w in ("delete", "drop", "revoke", "bypass", "override",
                              "never", "must not", "shall not", "forbidden") if w in statement]
        return bool(guards) and any(w in proposal_text for w in
                                    ("delete", "drop", "revoke", "bypass", "override"))

    def execute_tier2_judge(
        self, proposal: ActionProposal, context: CompiledContext
    ) -> VerificationReceipt:
        """Formal re-check: TMS predicate evaluation over the proposal payload."""
        start = time.perf_counter()
        results = self.tms.evaluate_invariants(
            proposal.target_scopes, dict(proposal.operation_payload),
            current_step=self.current_step,
        )
        violated = [r.record_id for r in results if r.verdict.value == "VIOLATED"]
        return VerificationReceipt(
            action_id=proposal.action_id, tier=VerificationTier.TIER_2_JUDGE,
            passed=not violated, execution_time_ms=(time.perf_counter() - start) * 1000.0,
            diagnostics={"violated": violated},
        )

    # -- gate ------------------------------------------------------------
    def intercept_proposal(
        self, proposal: ActionProposal, context: CompiledContext
    ) -> GateVerdict:
        tier0 = self.execute_tier0_substrate(proposal)
        if not tier0.passed:
            return self._veto(proposal, [], VerificationTier.TIER_0_SUBSTRATE,
                              f"substrate rejection: {tier0.diagnostics}")
        tier1 = self.execute_tier1_nli(proposal, context)
        if not tier1.passed:
            violated = list(tier1.diagnostics.get("violated", []))
            return self._veto(proposal, violated, VerificationTier.TIER_1_NLI,
                              "proposal contradicts active invariants")
        tier2 = self.execute_tier2_judge(proposal, context)
        if not tier2.passed:
            return self._veto(proposal, list(tier2.diagnostics.get("violated", [])),
                              VerificationTier.TIER_2_JUDGE, "formal predicate violated")
        self.rpe_delta += _RPE_ACCEPT
        self.committed[proposal.action_id] = proposal
        return GateVerdict(
            action_id=proposal.action_id, verdict=GateVerdictEnum.ACCEPT,
            tier_reached=VerificationTier.TIER_2_JUDGE,
            cryptographic_receipt=_receipt(proposal.action_id, GateVerdictEnum.ACCEPT,
                                           VerificationTier.TIER_2_JUDGE),
        )

    def _veto(self, proposal: ActionProposal, violated: list[str],
              tier: VerificationTier, reason: str) -> GateVerdict:
        self.rpe_delta += _RPE_REJECT
        if violated:
            self.emit_negative_inhibitor({
                "proposal": proposal, "invariant_id": violated[0], "diagnostic": reason,
            })
        return GateVerdict(
            action_id=proposal.action_id, verdict=GateVerdictEnum.VETO,
            tier_reached=tier, violated_invariants=violated, repair_instruction=reason,
            cryptographic_receipt=_receipt(proposal.action_id, GateVerdictEnum.VETO, tier),
        )

    def emit_negative_inhibitor(self, violation: dict[str, Any]) -> NegativeInhibitorGene:
        proposal = violation["proposal"]
        blob = f"{proposal.action_id}:{violation['invariant_id']}".encode()
        gene = NegativeInhibitorGene(
            proposal_hash=hashlib.sha256(blob).hexdigest(),
            violated_invariant_id=violation["invariant_id"],
            violating_operation=proposal.proposed_operation.value,
            diagnostic=str(violation.get("diagnostic", "")),
            created_at_step=self.current_step,
        )
        self.inhibitors.append(gene)
        return gene

    def rollback_transaction(self, action_id: str) -> bool:
        return self.committed.pop(action_id, None) is not None
