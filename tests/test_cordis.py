"""P05 acceptance: authority lattice, compiler budget, RPE loop, plugin, conformance."""

import importlib.util

import pytest

from trace_lite.cordis import (
    ActionProposal,
    AtomRecord,
    AuthorityLevel,
    CommitmentRecord,
    CommitmentStatus,
    FacetedQuery,
    InvariantVetoError,
    JustificationReceipt,
    ModularContractBoundaryTMS,
    ScopeExemption,
    StepAcceptanceGate,
    TokenBudget,
    TraceEvent,
    TraceLiteContextCompiler,
    TraceLiteMemoryPlugin,
)

SPEC_PATH = (
    "/mnt/c/Users/anshu/OneDrive/Desktop/trace lite and trillion dreams"
    "/research_specifications/schemas/interfaces.py"
)


def _user_invariant(cid="inv-user-001", scope="global/spec.md"):
    return CommitmentRecord(
        id=cid, target_scopes=[scope], authority=AuthorityLevel.USER,
        statement="NEVER delete protected specifications without user approval",
    )


def test_authority_lattice_veto_and_dominance():
    """C01: lower authority cannot revoke higher; poset ordering holds."""
    assert AuthorityLevel.USER.strictly_dominates(AuthorityLevel.ARCH_SPEC)
    assert AuthorityLevel.ARCH_SPEC.strictly_dominates(AuthorityLevel.AGENT_DECISION)
    assert AuthorityLevel.AGENT_DECISION.strictly_dominates(AuthorityLevel.TOOL_OUTPUT)
    assert AuthorityLevel.USER.dominates(AuthorityLevel.USER)
    assert not AuthorityLevel.TOOL_OUTPUT.dominates(AuthorityLevel.USER)

    tms = ModularContractBoundaryTMS()
    tms.register_commitment(_user_invariant())
    hostile = CommitmentRecord(
        id="inv-agent-override", target_scopes=["global/spec.md"],
        authority=AuthorityLevel.AGENT_DECISION, statement="agent may delete specs",
    )
    with pytest.raises(InvariantVetoError):
        tms.supersede_commitment(
            "inv-user-001", hostile,
            JustificationReceipt(commitment_id="inv-user-001",
                                 authority=AuthorityLevel.AGENT_DECISION,
                                 evidence_hash="0" * 64, rationale="agent override"),
        )
    # Equal-or-higher authority with a matching receipt succeeds.
    replacement = CommitmentRecord(
        id="inv-user-002", target_scopes=["global/spec.md"],
        authority=AuthorityLevel.USER, statement="revised user specification policy",
    )
    tms.supersede_commitment(
        "inv-user-001", replacement,
        JustificationReceipt(commitment_id="inv-user-001", authority=AuthorityLevel.USER,
                             evidence_hash="1" * 64, rationale="user revision"),
    )
    assert tms.commitments["inv-user-001"].status == CommitmentStatus.SUPERSEDED
    assert tms.get_active_commitments(["global/spec.md"])[0].id == "inv-user-002"


def test_predicate_evaluation_and_exemption():
    tms = ModularContractBoundaryTMS()
    tms.register_commitment(CommitmentRecord(
        id="inv-wal", target_scopes=["global/db"], authority=AuthorityLevel.ARCH_SPEC,
        statement="checkpoint budget respected", formal_predicate="delta.get('frames', 0) <= 100",
    ))
    ok = tms.evaluate_invariants(["global/db"], {"frames": 10})
    assert all(r.verdict.value == "SAT" for r in ok)
    bad = tms.evaluate_invariants(["global/db"], {"frames": 500})
    assert any(r.verdict.value == "VIOLATED" for r in bad)
    tms.register_exemption("inv-wal", ScopeExemption(
        exemption_id="ex-test", sub_scope="global/db", condition_predicate="True",
        authorized_by=AuthorityLevel.ARCH_SPEC, expiry_step=99,
    ))
    assert tms.evaluate_invariants(["global/db"], {"frames": 500})[0].verdict.value == "EXEMPT"
    with pytest.raises(InvariantVetoError):
        tms.register_exemption("inv-wal", ScopeExemption(
            exemption_id="ex-low", sub_scope="global/db", authorized_by=AuthorityLevel.TOOL_OUTPUT,
        ))


def test_compiler_budget_never_exceeded_over_100_steps():
    """C02: 100 compilations stay within 3,500 tokens; invariants ride along."""
    tms = ModularContractBoundaryTMS()
    for i in range(5):
        tms.register_commitment(CommitmentRecord(
            id=f"inv-{i}", target_scopes=["global"], authority=AuthorityLevel.ARCH_SPEC,
            statement=f"architectural invariant number {i} governing checkpoint budgets",
        ))
    compiler = TraceLiteContextCompiler(tms)
    for step in range(100):
        candidates = [
            {"artifact_id": f"a-{step}-{j}",
             "content": f"checkpoint governor evidence packet {j} " * 40,
             "source_uri": f"docs/{j}.md"}
            for j in range(30)
        ]
        budget = TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=600,
                             salient_evidence_budget=1800)
        ctx = compiler.compile_context(
            step_id=str(step), goal_id="checkpoint governor tuning", target_scopes=["global"],
            proposed_op="state_transition", budget=budget, raw_candidates=candidates,
        )
        profile = ctx.budget_profile
        total = (profile.system_prompt_tokens + profile.mandatory_invariant_tokens
                 + profile.goal_state_tokens + profile.allocated_evidence_tokens)
        assert total <= 3500
        assert ctx.mandatory_invariants, "highest-authority invariants must ride along"
        assert ctx.gating_threshold_used == compiler.theta_gate
    assert compiler.update_gating_threshold(1.0) < compiler.theta_gate + 0.06  # gate adapts


def test_gate_rpe_feedback_loop():
    """C03: accept applies +1.0, veto applies -1.0, inhibitor minted, rollback works."""
    tms = ModularContractBoundaryTMS()
    tms.register_commitment(_user_invariant())
    compiler = TraceLiteContextCompiler(tms)
    gate = StepAcceptanceGate(tms)
    budget = TokenBudget()
    ctx = compiler.compile_context("1", "spec review", ["global/spec.md"], "read",
                                   budget, [], current_step=0)

    good = ActionProposal(target_scopes=["global/spec.md"], target_entities=["spec.md"],
                          proposed_operation="state_transition",
                          operation_payload={"review": "read-only summary"})
    verdict = gate.intercept_proposal(good, ctx)
    assert verdict.verdict.value == "ACCEPT" and gate.rpe_delta == pytest.approx(1.0)
    assert gate.rollback_transaction(good.action_id) is True
    assert gate.rollback_transaction(good.action_id) is False

    evil = ActionProposal(target_scopes=["global/spec.md"], target_entities=["spec.md"],
                          proposed_operation="file_delete",
                          operation_payload={"delete": "global/spec.md"})
    veto = gate.intercept_proposal(evil, ctx)
    assert veto.verdict.value == "VETO" and veto.violated_invariants == ["inv-user-001"]
    assert gate.rpe_delta == pytest.approx(0.0)  # +1.0 then -1.0
    assert len(gate.inhibitors) == 1
    assert veto.cryptographic_receipt and len(veto.cryptographic_receipt) == 64


def test_memory_plugin_round_trip(tmp_path):
    plugin = TraceLiteMemoryPlugin()
    assert plugin.on_init({"db_path": str(tmp_path / "cordis.db")}) is True
    eid = plugin.on_record_event(TraceEvent(stream_id="s", event_type="atom.ingested",
                                            payload={"doc": "d"}))
    assert eid
    aid = plugin.ingest_atom(AtomRecord(doc_id="d", text="cordis memory substrate filing"))
    assert aid > 0
    resp = plugin.query_context(FacetedQuery(query="cordis memory substrate"))
    assert resp.sufficiency_state == "answerable" and resp.anchors
    lease = plugin.acquire_rcu_lease(pid=1234)
    assert plugin.release_rcu_lease(lease["lease_id"]) is True
    assert plugin.flush()["total_committed"] >= 1
    plugin.close()


def _load_spec_module():
    """Import the formal Desktop spec; skip when unreachable (no network FS)."""
    spec = importlib.util.spec_from_file_location("tl_interfaces", SPEC_PATH)
    if spec is None or spec.loader is None:
        pytest.skip("formal interfaces.py not reachable")
    try:
        import sys

        module = importlib.util.module_from_spec(spec)
        sys.modules["tl_interfaces"] = module  # pydantic resolves forward refs via sys.modules
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        pytest.skip(f"formal interfaces.py not importable: {exc}")


def test_conformance_against_formal_interfaces():
    """CRIT-03/F6: authority ranks equal; every same-named model accepts FULL spec dumps."""
    import trace_lite.cordis.models as ours

    module = _load_spec_module()
    for name in ("USER", "ARCH_SPEC", "AGENT_DECISION", "TOOL_OUTPUT"):
        assert getattr(module.AuthorityLevel, name).value == name
        assert getattr(module.AuthorityLevel, name).rank == getattr(AuthorityLevel, name).rank
    assert module.AuthorityLevel.USER.dominates(module.AuthorityLevel.ARCH_SPEC)

    cases = {
        "TraceEvent": dict(event_id="e1", stream_id="s", stream_sequence=0, event_type="t",
                           occurred_at="2026-01-01T00:00:00+00:00", actor="agent",
                           payload={}, payload_hash="0" * 64),
        "AtomRecord": dict(doc_id="d", file_id="f.md", start_byte=0, end_byte=3,
                           content_hash="ab" * 32, text="abc"),
        "FacetedQuery": dict(query="q"),
        "EvidenceAnchor": dict(anchor_id="a", atom_version_id="v", file_id="f", doc_id="d",
                               start_byte=0, end_byte=1, content_hash="h", snippet_text="s"),
        "QueryResponse": dict(query="q", tier_used=1, elapsed_ms=1.0,
                              sufficiency_state="answerable"),
        "CommitmentRecord": dict(id="c", target_scopes=["global"], authority="USER",
                                 statement="s", created_at_step=0),
        "ScopeExemption": dict(exemption_id="e", sub_scope="global/db",
                               condition_predicate="True", authorized_by="ARCH_SPEC"),
        "JustificationReceipt": dict(receipt_id="r1", commitment_id="c", authority="USER",
                                     evidence_hash="0" * 64, rationale="r",
                                     timestamp="2026-01-01T00:00:00+00:00"),
        "TokenBudget": dict(mandatory_invariant_tokens=600, salient_evidence_budget=1800,
                            allocated_evidence_tokens=100),
        "ActionProposal": dict(action_id="a1", step_id="s1", task_id="t1",
                               target_scopes=["global/a"],
                               proposed_operation="state_transition",
                               operation_payload={"k": "v"},
                               predicted_postconditions=["p"]),
        "GateVerdict": dict(decision_id="d1", action_id="a", verdict="ACCEPT",
                            tier_reached="TIER_0_SUBSTRATE", cryptographic_receipt="r"),
        "VerificationReceipt": dict(receipt_id="r1", action_id="a", tier="TIER_0_SUBSTRATE",
                                    passed=True, execution_time_ms=1.0),
        "NegativeInhibitorGene": dict(gene_id="g1", proposal_hash="h",
                                      violated_invariant_id="v",
                                      violating_operation="op", diagnostic="d",
                                      created_at_step=0),
        "DependencyNode": dict(node_id="n", node_type="BELIEF", label="l",
                               scope="global/a"),
        "RevocationEvent": dict(revocation_id="r1", target_node_id="n", reason="why",
                                authority="AGENT_DECISION", timestamp_step=0,
                                justification_hash="h"),
        "InvalidationCascadeResult": dict(initiating_event_id="r", tainted_nodes=[],
                                         invalidated_nodes=[], pruned_nodes=[],
                                         tombstones_emitted=[],
                                         propagation_depth_reached=0,
                                         execution_time_ms=0.0),
    }
    ours_map = {name: getattr(ours, name) for name in cases}
    assert set(ours_map) == set(cases)  # no counterpart may silently disappear
    for name, kwargs in cases.items():
        formal = getattr(module, name)(**kwargs)
        ours_map[name](**formal.model_dump())  # unfiltered: extra="forbid" must hold
