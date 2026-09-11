"""P07 e2e: vault ingest -> facets -> 3-tier queries -> Cordis step gate."""

import pytest

from trace_lite.api import sync_vault
from trace_lite.cordis import (
    ActionProposal,
    AuthorityLevel,
    CommitmentRecord,
    JustificationReceipt,
    StepAcceptanceGate,
    TokenBudget,
    TraceLiteContextCompiler,
    ModularContractBoundaryTMS,
)
from trace_lite.filing import FilingEngine, HolonStore, Taxonomy
from trace_lite.router import CascadeRouter
from trace_lite.store import Database

NOTES = {
    "Systems/WAL.md": """---
tags: [wal, sqlite]
type: Spec
---

# WAL Governor

The checkpoint governor folds WAL frames every 5000 documents post-commit.
See [[Checkpoint Tuning]] for thresholds.
""",
    "Systems/Checkpoint Tuning.md": """---
tags: [wal]
type: Note
---

# Checkpoint Tuning

PASSIVE checkpoints avoid locking the writer during bulk ingest of filings.
Related: [[WAL Governor]] and #sqlite durability.
""",
    "Projects/Cordis.md": """---
tags: [cordis, memory]
type: Paper
---

# Cordis Memory Substrate

Authority lattice USER ARCH_SPEC AGENT_DECISION TOOL_OUTPUT governs revisions.
""",
}


@pytest.fixture()
def vault(tmp_path):
    root = tmp_path / "vault"
    for rel, text in NOTES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_full_chain_vault_to_retrieval(tmp_path, vault):
    """P07.1: ingest vault -> facets -> sub-50ms 3-tier queries over holons."""
    db = Database(tmp_path / "e2e.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        holons = HolonStore(db.conn)
        synced = sync_vault(db, taxonomy, engine, vault)
        assert synced == 3 and db.count_atoms() == 3
        # Every note landed in Topics + Sources facets.
        dims = {r[0] for r in db.conn.execute("SELECT DISTINCT dimension FROM facets").fetchall()}
        assert {"Topics", "Sources"} <= dims
        assert db.conn.execute("SELECT COUNT(*) FROM memberships").fetchone()[0] >= 6
        router = CascadeRouter(db.conn, engine)
        router.warm()
        answer = router.route("WAL governor checkpoint folding")
        assert answer.verdict == "answerable" and answer.elapsed_ms < 50.0
        assert any("WAL" in a["doc_id"] for a in answer.anchors)
        miss = router.route("xqzt blorpt wqkj")
        assert miss.verdict == "insufficient_evidence" and miss.anchors == []
        # Holon over a multi-atom doc stays intra-document.
        doc_atoms = [r[0] for r in db.conn.execute(
            "SELECT id FROM atom WHERE doc_id='Systems/WAL.md' ORDER BY id").fetchall()]
        assert len(doc_atoms) == 1  # one atom per note in vault sync
        assert holons.holons_of_doc("Systems/WAL.md") == []
    finally:
        db.close()


def test_cordis_micro_step_interception():
    """P07.2: compile under budget -> gate vetoes lattice violation -> RPE updates."""
    tms = ModularContractBoundaryTMS()
    tms.register_commitment(CommitmentRecord(
        id="e2e-law", target_scopes=["global/vault"], authority=AuthorityLevel.USER,
        statement="NEVER delete vault notes without user approval"))
    compiler = TraceLiteContextCompiler(tms)
    gate = StepAcceptanceGate(tms)
    budget = TokenBudget()
    ctx = compiler.compile_context("e2e-1", "vault review", ["global/vault"], "read",
                                   budget, [{"artifact_id": "n1", "content": "wal notes"}], 0)
    assert (budget.system_prompt_tokens + ctx.budget_profile.mandatory_invariant_tokens
            + budget.goal_state_tokens) <= 3500
    assert any(r.id == "e2e-law" for r in ctx.mandatory_invariants)
    bad = ActionProposal(target_scopes=["global/vault"], proposed_operation="file_delete",
                         operation_payload={"delete": "global/vault/Systems/WAL.md"})
    verdict = gate.intercept_proposal(bad, ctx)
    assert verdict.verdict.value == "VETO"
    assert gate.rpe_delta == pytest.approx(-1.0)
    # Agent cannot legislate around the user law either.
    with pytest.raises(Exception):
        tms.supersede_commitment(
            "e2e-law",
            CommitmentRecord(id="e2e-sneak", target_scopes=["global/vault"],
                             authority=AuthorityLevel.AGENT_DECISION, statement="allow deletes"),
            JustificationReceipt(commitment_id="e2e-law",
                                 authority=AuthorityLevel.AGENT_DECISION,
                                 evidence_hash="0" * 64, rationale="sneak"),
        )
