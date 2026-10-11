"""Pre-commit step acceptance gate: 3-tier cascade + RPE feedback."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
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
_RPE_KEY = "rpe_delta"


def _receipt(action_id: str, verdict: GateVerdictEnum, tier: VerificationTier) -> str:
    return hmac.new(_SECRET, f"{action_id}:{verdict.value}:{tier.value}".encode(), hashlib.sha256).hexdigest()


class StepAcceptanceGate:
    """Tier 0 substrate → Tier 1 invariant/inhibitor screen → Tier 2 formal re-check."""

    def __init__(
        self, tms: ModularContractBoundaryTMS,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Durable gate state lives on ``conn`` (a Database-backed connection,
        whose open path runs schema.sql so the gate_* tables exist).

        ``conn=None`` keeps the legacy purely in-memory behavior.
        ``current_step`` stays session-local by design: the gate body never
        advances it, so every instance restarts at 0.
        """
        self.tms = tms
        self._conn = conn
        self.rpe_delta = 0.0
        self.inhibitors: list[NegativeInhibitorGene] = []
        self.committed: dict[str, ActionProposal] = {}
        self.current_step = 0
        if conn is not None:
            self._load()

    # -- durability ----------------------------------------------------
    def _load(self) -> None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM gate_state WHERE key = ?", (_RPE_KEY,)).fetchone()
        if row is not None:
            self.rpe_delta = float(row[0])
        for r in self._conn.execute(
                "SELECT gene_id, proposal_hash, violated_invariant_id,"
                " violating_operation, diagnostic, status, created_at_step"
                " FROM gate_inhibitor ORDER BY rowid").fetchall():
            self.inhibitors.append(NegativeInhibitorGene(
                gene_id=r[0], proposal_hash=r[1], violated_invariant_id=r[2],
                violating_operation=r[3], diagnostic=r[4], status=r[5],
                created_at_step=r[6]))
        for r in self._conn.execute(
                "SELECT action_id, proposal_json FROM gate_committed").fetchall():
            self.committed[r[0]] = ActionProposal.model_validate_json(r[1])

    def _outer_txn_open(self) -> bool:
        return self._conn is not None and self._conn.in_transaction

    def _flush(self, outer_txn: bool) -> None:
        # Txn discipline: join the caller's transaction when one is already
        # open on entry (commit stays with the caller); otherwise commit with
        # the write so every gate mutation is durable on return.
        if self._conn is not None and not outer_txn:
            self._conn.commit()

    def _store_rpe(self) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO gate_state(key, value) VALUES (?, ?)",
            (_RPE_KEY, repr(self.rpe_delta)))

    def _store_inhibitor(self, gene: NegativeInhibitorGene) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO gate_inhibitor(gene_id, proposal_hash,"
            " violated_invariant_id, violating_operation, diagnostic, status,"
            " created_at_step) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (gene.gene_id, gene.proposal_hash, gene.violated_invariant_id,
             gene.violating_operation, gene.diagnostic, gene.status,
             gene.created_at_step))

    def _store_commit(self, proposal: ActionProposal) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO gate_committed(action_id, proposal_json)"
            " VALUES (?, ?)", (proposal.action_id, proposal.model_dump_json()))

    def _store_uncommit(self, action_id: str) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "DELETE FROM gate_committed WHERE action_id = ?", (action_id,))

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
        outer = self._outer_txn_open()
        tier0 = self.execute_tier0_substrate(proposal)
        if not tier0.passed:
            verdict = self._veto(proposal, [], VerificationTier.TIER_0_SUBSTRATE,
                                 f"substrate rejection: {tier0.diagnostics}")
            self._flush(outer)
            return verdict
        tier1 = self.execute_tier1_nli(proposal, context)
        if not tier1.passed:
            violated = list(tier1.diagnostics.get("violated", []))
            verdict = self._veto(proposal, violated, VerificationTier.TIER_1_NLI,
                                 "proposal contradicts active invariants")
            self._flush(outer)
            return verdict
        tier2 = self.execute_tier2_judge(proposal, context)
        if not tier2.passed:
            verdict = self._veto(proposal, list(tier2.diagnostics.get("violated", [])),
                                 VerificationTier.TIER_2_JUDGE, "formal predicate violated")
            self._flush(outer)
            return verdict
        self.rpe_delta += _RPE_ACCEPT
        self.committed[proposal.action_id] = proposal
        self._store_rpe()
        self._store_commit(proposal)
        self._flush(outer)
        return GateVerdict(
            action_id=proposal.action_id, verdict=GateVerdictEnum.ACCEPT,
            tier_reached=VerificationTier.TIER_2_JUDGE,
            cryptographic_receipt=_receipt(proposal.action_id, GateVerdictEnum.ACCEPT,
                                           VerificationTier.TIER_2_JUDGE),
        )

    def _veto(self, proposal: ActionProposal, violated: list[str],
              tier: VerificationTier, reason: str) -> GateVerdict:
        # No commit here: intercept_proposal owns the flush boundary, so the
        # RPE write + inhibitor write land in one transaction.
        self.rpe_delta += _RPE_REJECT
        self._store_rpe()
        if violated:
            self.emit_negative_inhibitor({
                "proposal": proposal, "invariant_id": violated[0], "diagnostic": reason,
            }, _outer_txn=True)
        return GateVerdict(
            action_id=proposal.action_id, verdict=GateVerdictEnum.VETO,
            tier_reached=tier, violated_invariants=violated, repair_instruction=reason,
            cryptographic_receipt=_receipt(proposal.action_id, GateVerdictEnum.VETO, tier),
        )

    def emit_negative_inhibitor(
        self, violation: dict[str, Any], _outer_txn: bool | None = None
    ) -> NegativeInhibitorGene:
        outer = self._outer_txn_open() if _outer_txn is None else _outer_txn
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
        self._store_inhibitor(gene)
        self._flush(outer)
        return gene

    def rollback_transaction(self, action_id: str) -> bool:
        outer = self._outer_txn_open()
        popped = self.committed.pop(action_id, None) is not None
        if popped:
            self._store_uncommit(action_id)
            self._flush(outer)
        return popped
