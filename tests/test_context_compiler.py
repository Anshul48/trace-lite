"""R07 context compiler: objective-text ranking + explicit budget behavior.

C7-1: ranking follows ``goal_state['objective']`` text, never the opaque
``goal_id`` — same objective under different ids ranks identically (100%
parity). Mandatory overflow is an explicit ``insufficient_budget`` failure
(zero silent drops); oversized evidence is reduced via query-anchored,
source-linked excerpts, never arbitrary prefixes. Deterministic; no LLM.
"""

import pytest

from trace_lite.cordis import (
    AuthorityLevel,
    CommitmentRecord,
    ModularContractBoundaryTMS,
    TokenBudget,
    TraceLiteContextCompiler,
    estimate_tokens,
)


def _tms_with(*statements):
    tms = ModularContractBoundaryTMS()
    for i, stmt in enumerate(statements):
        tms.register_commitment(
            CommitmentRecord(
                id=f"inv-{i}",
                target_scopes=["global"],
                authority=AuthorityLevel.ARCH_SPEC,
                statement=stmt,
            )
        )
    return tms


CANDIDATES = [
    {"artifact_id": "wal-note", "content": "WAL mode allows one writer with concurrent readers.", "source_uri": "w.md"},
    {"artifact_id": "batch-note", "content": "Batching improves throughput by 30% under low contention.", "source_uri": "b.md"},
    {"artifact_id": "sourdough-note", "content": "Sourdough starter hydration ratios near seventy percent.", "source_uri": "s.md"},
]

OBJECTIVE = "Does batching improve throughput?"


def _compile(compiler, goal_id, goal_state, candidates=None):
    budget = TokenBudget(
        max_total_tokens=3500,
        mandatory_invariant_tokens=600,
        salient_evidence_budget=1800,
    )
    return compiler.compile_context(
        step_id="s1",
        goal_id=goal_id,
        target_scopes=["global"],
        proposed_op="state_transition",
        budget=budget,
        raw_candidates=list(CANDIDATES if candidates is None else candidates),
        goal_state=goal_state,
    )


def test_objective_invariance_identical_ranking():
    """C7-1: same objective + different goal_id → identical ranking (100%)."""
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    orders = []
    for gid in ("goal-alpha", "goal-beta", "7f3a9c", "totally/other/id"):
        ctx = _compile(compiler, gid, {"objective": OBJECTIVE})
        orders.append([e.artifact_id for e in ctx.compiled_evidence])
    assert orders[0], "expected non-empty ranking"
    for order in orders[1:]:
        assert order == orders[0]
    # Scores identical too, not just order.
    scores = []
    for gid in ("goal-alpha", "goal-beta"):
        ctx = _compile(compiler, gid, {"objective": OBJECTIVE})
        scores.append([e.salience_score for e in ctx.compiled_evidence])
    assert scores[0] == scores[1]


def test_ranking_follows_objective_not_misleading_id():
    """A misleading goal_id must not drag ranking off the objective text."""
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    ctx = _compile(
        compiler,
        "sourdough hydration project",  # decoy id text
        {"objective": OBJECTIVE},
    )
    top = [e.artifact_id for e in ctx.compiled_evidence][0]
    assert top == "batch-note"


def test_changing_objective_changes_ranking():
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    ctx_a = _compile(compiler, "same-id", {"objective": OBJECTIVE})
    ctx_b = _compile(
        compiler, "same-id", {"objective": "What are sourdough starter hydration ratios?"}
    )
    assert [e.artifact_id for e in ctx_a.compiled_evidence][0] == "batch-note"
    assert [e.artifact_id for e in ctx_b.compiled_evidence][0] == "sourdough-note"


def test_legacy_goal_id_fallback_keeps_old_callers_working():
    """No goal_state → the goal_id value is treated as the objective text."""
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    ctx = _compile(compiler, OBJECTIVE, None)
    assert ctx.goal_objective == OBJECTIVE
    assert [e.artifact_id for e in ctx.compiled_evidence][0] == "batch-note"


def test_mandatory_overflow_is_explicit_never_silent():
    """C7-4 (compiler side): oversized invariants → insufficient_budget."""
    big = "NEVER drop this invariant " * 60  # ~600+ tokens each
    compiler = TraceLiteContextCompiler(_tms_with(big, big, big))
    budget = TokenBudget(
        max_total_tokens=3500,
        mandatory_invariant_tokens=100,
        salient_evidence_budget=1800,
    )
    ctx = compiler.compile_context(
        "s1", "gid", ["global"], "state_transition", budget, list(CANDIDATES),
        goal_state={"objective": OBJECTIVE},
    )
    assert ctx.status == "insufficient_budget"
    assert ctx.mandatory_invariants == []
    assert ctx.compiled_evidence == []
    # Every applicable invariant is NAMED — zero silent drops.
    assert sorted(ctx.omitted_invariant_ids) == ["inv-0", "inv-1", "inv-2"]
    assert ctx.distractor_rejection_count == len(CANDIDATES)


def test_default_budget_carries_invariants_legacy_contract():
    """Zero-reservation default budget = fit under the ceiling (no partition).

    Pins the pre-R07 contract relied on by test_cordis.py / test_e2e.py:
    a default TokenBudget() still carries active invariants.
    """
    compiler = TraceLiteContextCompiler(
        _tms_with("NEVER delete protected specifications without user approval")
    )
    ctx = compiler.compile_context(
        "1", "spec review", ["global"], "read", TokenBudget(), [],
        current_step=0,
    )
    assert ctx.status == "ok"
    assert [r.id for r in ctx.mandatory_invariants] == ["inv-0"]


def test_fitting_mandatory_invariants_all_ride_along():
    compiler = TraceLiteContextCompiler(
        _tms_with("keep checkpoints bounded", "never bypass the gate")
    )
    ctx = _compile(compiler, "gid", {"objective": OBJECTIVE})
    assert ctx.status == "ok"
    assert sorted(r.id for r in ctx.mandatory_invariants) == ["inv-0", "inv-1"]
    assert ctx.omitted_invariant_ids == []


def test_anchored_excerpt_keeps_query_terms_not_prefix():
    """Oversized evidence keeps the query-anchored region, not [:n]."""
    filler = "unrelated filler sentence about nothing. " * 120
    content = filler + "Batching improves throughput dramatically at the tail."
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    budget = TokenBudget(
        max_total_tokens=3500,
        mandatory_invariant_tokens=600,
        salient_evidence_budget=120,
    )
    ctx = compiler.compile_context(
        "s1", "gid", ["global"], "state_transition", budget,
        [{"artifact_id": "long", "content": content, "source_uri": "l.md"}],
        goal_state={"objective": OBJECTIVE},
    )
    assert ctx.status == "ok" and len(ctx.compiled_evidence) == 1
    packet = ctx.compiled_evidence[0]
    assert packet.is_excerpt is True
    assert "throughput" in packet.content  # tail terms survive
    assert not content.startswith(packet.content)  # not a prefix cut
    blob = content.encode("utf-8")
    assert blob[packet.excerpt_start_byte : packet.excerpt_end_byte] == packet.content.encode("utf-8")
    assert 0 <= packet.excerpt_start_byte < packet.excerpt_end_byte <= len(blob)


def test_token_ceiling_holds_with_wrappers():
    compiler = TraceLiteContextCompiler(_tms_with("keep checkpoints bounded"))
    many = [
        {"artifact_id": f"a-{i}", "content": f"batching throughput evidence {i} " * 60,
         "source_uri": f"{i}.md"}
        for i in range(30)
    ]
    ctx = _compile(compiler, "gid", {"objective": OBJECTIVE}, candidates=many)
    profile = ctx.budget_profile
    total = (
        profile.system_prompt_tokens
        + profile.mandatory_invariant_tokens
        + profile.goal_state_tokens
        + profile.allocated_evidence_tokens
    )
    assert total <= 3500
    counted = sum(e.token_cost for e in ctx.compiled_evidence)
    assert profile.allocated_evidence_tokens == counted
    # Wrapper/citation metadata is counted, not free.
    assert counted >= sum(estimate_tokens(e.content) for e in ctx.compiled_evidence)
