"""P06 contract: durable gate state (REUSE-VERIFY.md A5).

The 3-tier veto cascade + HMAC receipts are keeps; the in-memory state
(rpe_delta, inhibitors, committed) must survive restart via durable
table(s). current_step is NOT persisted by design: the gate body never
advances it (assigned only in __init__, read in tier-2 + inhibitor mint)
and no in-repo caller ever sets it, so it is a session-local cursor that
restarts at 0.
"""

from __future__ import annotations

import hashlib
import hmac

from trace_lite.cordis import (
    ActionProposal,
    AuthorityLevel,
    CommitmentRecord,
    ModularContractBoundaryTMS,
    StepAcceptanceGate,
    TokenBudget,
    TraceLiteContextCompiler,
)
from trace_lite.store.database import Database

_SECRET = b"trace-lite-step-gate"


def _tms() -> ModularContractBoundaryTMS:
    tms = ModularContractBoundaryTMS()
    tms.register_commitment(CommitmentRecord(
        id="inv-user-001", target_scopes=["global/spec.md"],
        authority=AuthorityLevel.USER,
        statement="NEVER delete protected specifications without user approval",
    ))
    return tms


def _ctx(tms):
    compiler = TraceLiteContextCompiler(tms)
    return compiler.compile_context(
        "1", "spec review", ["global/spec.md"], "read",
        TokenBudget(), [], current_step=0,
    )


def _good(action_id="good-001"):
    return ActionProposal(
        action_id=action_id, target_scopes=["global/spec.md"],
        target_entities=["spec.md"], proposed_operation="state_transition",
        operation_payload={"review": "read-only summary"},
    )


def _evil(action_id="evil-001"):
    return ActionProposal(
        action_id=action_id, target_scopes=["global/spec.md"],
        target_entities=["spec.md"], proposed_operation="file_delete",
        operation_payload={"delete": "global/spec.md"},
    )


def test_restart_persistence(tmp_path):
    """Mutate gate, close/reopen DB, new gate observes prior state."""
    path = tmp_path / "gate.db"
    db = Database(path)
    try:
        tms = _tms()
        gate = StepAcceptanceGate(tms, db.conn)
        ctx = _ctx(tms)
        assert gate.intercept_proposal(_good(), ctx).verdict.value == "ACCEPT"
        veto = gate.intercept_proposal(_evil(), ctx)
        assert veto.verdict.value == "VETO"
        assert gate.rpe_delta == 0.0  # +1.0 then -1.0
        assert len(gate.inhibitors) == 1
        gene = gate.inhibitors[0]
    finally:
        db.close()

    db2 = Database(path)
    try:
        gate2 = StepAcceptanceGate(_tms(), db2.conn)
        assert gate2.rpe_delta == 0.0
        assert list(gate2.committed) == ["good-001"]
        assert gate2.committed["good-001"].model_dump() == _good().model_dump()
        assert len(gate2.inhibitors) == 1
        g2 = gate2.inhibitors[0]
        assert (g2.gene_id, g2.proposal_hash, g2.violated_invariant_id,
                g2.violating_operation, g2.diagnostic, g2.status,
                g2.created_at_step) == (
            gene.gene_id, gene.proposal_hash, gene.violated_invariant_id,
            gene.violating_operation, gene.diagnostic, gene.status,
            gene.created_at_step)
        # current_step is session-local by design: restarts at 0.
        assert gate2.current_step == 0
    finally:
        db2.close()


def test_rollback_pop_persists(tmp_path):
    """A popped committed entry stays gone after reopen."""
    path = tmp_path / "gate.db"
    db = Database(path)
    try:
        tms = _tms()
        gate = StepAcceptanceGate(tms, db.conn)
        gate.intercept_proposal(_good(), _ctx(tms))
        assert gate.rollback_transaction("good-001") is True
    finally:
        db.close()

    db2 = Database(path)
    try:
        gate2 = StepAcceptanceGate(_tms(), db2.conn)
        assert gate2.committed == {}
        assert gate2.rollback_transaction("good-001") is False
        # Rollback only pops; the +1.0 accept RPE is untouched.
        assert gate2.rpe_delta == 1.0
    finally:
        db2.close()


def test_fresh_db_empty_state(tmp_path):
    """Fresh DB starts from documented empty state."""
    db = Database(tmp_path / "fresh.db")
    try:
        gate = StepAcceptanceGate(_tms(), db.conn)
        assert gate.rpe_delta == 0.0
        assert gate.inhibitors == []
        assert gate.committed == {}
        assert gate.current_step == 0
    finally:
        db.close()


def test_veto_receipt_unchanged(tmp_path):
    """Guard: known veto verdict + HMAC receipt byte-identical."""
    db = Database(tmp_path / "gate.db")
    try:
        tms = _tms()
        gate = StepAcceptanceGate(tms, db.conn)
        veto = gate.intercept_proposal(_evil("evil-keep"), _ctx(tms))
        assert veto.verdict.value == "VETO"
        assert veto.tier_reached.value == "TIER_1_NLI"
        assert veto.violated_invariants == ["inv-user-001"]
        expected = hmac.new(
            _SECRET, b"evil-keep:VETO:TIER_1_NLI", hashlib.sha256).hexdigest()
        assert veto.cryptographic_receipt == expected
    finally:
        db.close()
