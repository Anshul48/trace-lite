"""R07 dossier compilation: cross-source dossiers + honest adapter behavior.

C7-2: multi-source dossier with exact byte citations (R02 excerpts).
C7-3: conflicting evidence coexists under explicit disagreements.
C7-4: mandatory overflow → insufficient_budget; atomic-group overflow →
explicit coverage failure; zero silent drops. Plus EVALUATION in-scope
counterexamples: zero-lexical-overlap paraphrase retrievable via the holon
route; off-corpus NL query → insufficient (never fabricated support).
Deterministic fixtures; no LLM calls.
"""

import json

import pytest
from fastapi.testclient import TestClient

from trace_lite import source as S
from trace_lite.api import create_app
from trace_lite.cordis import (
    AuthorityLevel,
    CommitmentRecord,
    TokenBudget,
    compile_dossier,
)
from trace_lite.cordis.dossier import (
    SUPPORT_METHOD,
    TIER1_GATE_LABEL,
    adapter_report,
    anchored_excerpt,
)
from trace_lite.source import views as V
from trace_lite.store import Database

COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = "Under high contention, the batching benefit disappears; throughput matches the unbatched path."
QUERY = "Does batching improve throughput?"


def _atom(cid, text, doc, **kw):
    d = {
        "candidate_id": cid,
        "text": text,
        "doc_id": doc,
        "source_id": doc,
        "start_byte": 0,
        "end_byte": len(text.encode("utf-8")),
        "namespace": "default",
        "access_scope": "default",
        "version_status": "current",
        "route": "lexical",
    }
    d.update(kw)
    return d


def _budget(**kw):
    args = {
        "max_total_tokens": 3500,
        "mandatory_invariant_tokens": 600,
        "salient_evidence_budget": 1800,
    }
    args.update(kw)
    return TokenBudget(**args)


# -- C7-2 cross-source dossier with exact byte citations ---------------------

def _r02_pair(tmp_path):
    db = Database(tmp_path / "r07.db")
    rev_a = S.publish(db, "cond-a", COND_A + "\n")
    rev_b = S.publish(db, "cond-b", COND_B + "\n")
    view_a = V.create_segmentation_view(db.conn, rev_a.revision_id)
    view_b = V.create_segmentation_view(db.conn, rev_b.revision_id)
    spans_a = V.get_spans(db.conn, view_a["view_id"])
    spans_b = V.get_spans(db.conn, view_b["view_id"])
    return db, rev_a, rev_b, spans_a, spans_b


def test_cross_source_dossier_exact_byte_citations(tmp_path):
    """C7-2: spans from two sources, every citation R02-resolvable."""
    db, rev_a, rev_b, spans_a, spans_b = _r02_pair(tmp_path)
    try:
        candidates = []
        for i, s in enumerate(spans_a):
            candidates.append({
                "candidate_id": f"cond-a-s{i}", "text": s["text"],
                "doc_id": "cond-a", "source_id": "cond-a",
                "revision_id": s["revision_id"], "span_id": s["span_id"],
                "start_byte": s["start_byte"], "end_byte": s["end_byte"],
                "namespace": "default", "access_scope": "default",
                "version_status": "current", "route": "lexical",
            })
        for i, s in enumerate(spans_b):
            candidates.append({
                "candidate_id": f"cond-b-s{i}", "text": s["text"],
                "doc_id": "cond-b", "source_id": "cond-b",
                "revision_id": s["revision_id"], "span_id": s["span_id"],
                "start_byte": s["start_byte"], "end_byte": s["end_byte"],
                "namespace": "default", "access_scope": "default",
                "version_status": "current", "route": "holon",
            })
        dossier = compile_dossier(
            query=QUERY, objective=QUERY,
            scope={"namespaces": ["default"]},
            candidates=candidates,
            relations=[{
                "rel_type": "qualifies", "subject": "cond-a-s0",
                "object": "batching-claim",
                "evidence": "cond-a span",
            }],
            snapshot={"generation_id": "gen-1", "watermark": "atoms=2:maxid=2"},
            budget=_budget(),
            conn=db.conn,
        )
        assert dossier.status.value == "ok"
        assert len(dossier.selections) >= 2
        docs = {s.doc_id for s in dossier.selections}
        assert {"cond-a", "cond-b"} <= docs
        for sel in dossier.selections:
            assert sel.citation_valid is True
            assert sel.citation_method == "r02_excerpt"
            assert V.excerpt_span(db.conn, sel.span_id) == sel.excerpt_text.encode("utf-8")
        assert dossier.snapshot["generation_id"] == "gen-1"
        # Cross-source virtual holon: transient, derived, with lineage.
        assert len(dossier.virtual_holons) == 1
        vh = dossier.virtual_holons[0]
        assert vh.kind == "query_dossier" and vh.status == "derived"
        assert sorted(vh.member_sources) == ["cond-a", "cond-b"]
        assert "lineage" in vh.derivation or "method" in vh.derivation
        assert vh.qualification_edges  # qualification retained, not dropped
        # Token accounting uses the consumer tokenizer incl. wrappers.
        assert dossier.token_accounting["total"] <= 3500
        assert dossier.token_accounting["evidence_cost"] > 0
    finally:
        db.close()


# -- C7-3 conflict retention ---------------------------------------------------

def test_conflict_retention_both_sides_coexist():
    """C7-3: contradictory assertions surface under explicit disagreement."""
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[
            _atom("cond-a", COND_A, "cond-a", route="lexical"),
            _atom("cond-b", COND_B, "cond-b", route="holon"),
        ],
        relations=[{
            "rel_type": "conflicts_with", "subject": "cond-a",
            "object": "cond-b",
            "evidence": "low-contention gain vs high-contention disappearance",
        }],
        budget=_budget(),
    )
    assert dossier.status.value == "ok"
    assert dossier.support_state.value == "conflicting"
    assert {s.candidate_id for s in dossier.selections} == {"cond-a", "cond-b"}
    assert len(dossier.disagreements) == 1
    dis = dossier.disagreements[0]
    assert {dis["side_a"], dis["side_b"]} == {"cond-a", "cond-b"}


# -- C7-4 budget guards ----------------------------------------------------------

def test_mandatory_overflow_explicit_insufficient_budget():
    """C7-4a: oversized invariant set → explicit failure, zero silent drops."""
    mandatory = [
        CommitmentRecord(
            id=f"inv-{i}", target_scopes=["global"],
            authority=AuthorityLevel.USER,
            statement="NEVER drop this invariant " * 80,
        )
        for i in range(3)
    ]
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[_atom("cond-a", COND_A, "cond-a")],
        mandatory=mandatory,
        budget=_budget(mandatory_invariant_tokens=50),
    )
    assert dossier.status.value == "insufficient_budget"
    assert dossier.selections == []
    assert sorted(o["id"] for o in dossier.omissions) == ["inv-0", "inv-1", "inv-2"]


def test_atomic_group_overflow_is_coverage_failure():
    """C7-4b: complete group that cannot fit → explicit coverage failure."""
    big = "batching throughput contention evidence words " * 200
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[
            _atom("g1", big, "d1", group_id="G", atomic_group=True),
            _atom("g2", big, "d1", group_id="G", atomic_group=True),
        ],
        budget=_budget(salient_evidence_budget=40),
    )
    assert dossier.status.value == "coverage_failure"
    assert "G" in dossier.failure_reason
    assert any(o["kind"] == "evidence_group" for o in dossier.omissions)


# -- EVALUATION counterexamples ---------------------------------------------------

def test_paraphrase_no_lexical_overlap_retrievable_via_holon():
    """Zero-word-overlap paraphrase arriving on the holon route is retained."""
    paraphrase = "Assembling dispatches jointly elevates requests per second."
    assert not (
        {w for w in paraphrase.lower().split() if len(w) > 2}
        & {w for w in QUERY.lower().split() if len(w) > 2}
    ), "fixture must share no long words with the query"
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[_atom("para-1", paraphrase, "para-doc", route="holon")],
        budget=_budget(),
    )
    assert dossier.status.value == "ok"
    assert [s.candidate_id for s in dossier.selections] == ["para-1"]
    assert dossier.selections[0].candidate_route == "holon"


def test_off_corpus_query_is_insufficient_never_fabricated():
    """Off-corpus NL query → insufficient; unrelated records are not support."""
    dossier = compile_dossier(
        query="What are sourdough starter hydration ratios?",
        objective="What are sourdough starter hydration ratios?",
        candidates=[
            _atom("off-a", "SQLite WAL checkpoint frames durability truncate oversight valve.", "off-a"),
        ],
        budget=_budget(),
    )
    assert dossier.support_state.value == "insufficient"
    assert dossier.supported_count == 0


# -- filters, support states, honesty -----------------------------------------------

def test_scope_filters_apply_before_reveal_with_coverage_report():
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        scope={"namespaces": ["public"], "access_scopes": ["open"], "current_only": True},
        candidates=[
            _atom("ok-1", COND_A, "pub-doc", namespace="public", access_scope="open"),
            _atom("ns-1", "SECRET batching throughput zero-day launch codes", "priv-doc",
                  namespace="private", access_scope="open"),
            _atom("ac-1", "RESTRICTED batching throughput internals", "pub-doc",
                  namespace="public", access_scope="restricted"),
            _atom("old-1", COND_B, "pub-doc", namespace="public",
                  access_scope="open", version_status="superseded"),
        ],
        budget=_budget(),
    )
    visible_text = " ".join(s.excerpt_text for s in dossier.selections)
    assert "SECRET" not in visible_text and "RESTRICTED" not in visible_text
    assert "unbatched" not in visible_text  # superseded version withheld
    assert [s.candidate_id for s in dossier.selections] == ["ok-1"]
    cov = dossier.coverage_report
    assert cov["pre_filter"] == 4 and cov["post_filter"] == 1
    assert cov["filtered_out"] == {"namespace": 1, "access": 1, "version": 1}


def test_filter_wipeout_reports_coverage_gap_not_absence():
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        scope={"namespaces": ["public"]},
        candidates=[_atom("ns-1", COND_A, "priv-doc", namespace="private")],
        budget=_budget(),
    )
    assert dossier.coverage_report["coverage_gap_after_filter"] is True
    assert dossier.support_state.value == "insufficient"
    assert dossier.selections == []


def test_relevant_candidates_distinct_from_supported():
    """relevant_candidates ≠ supported: relevance admits, support judges."""
    weak = "Batching and throughput are words in this unrelated memo."
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[
            _atom("strong", COND_A, "d1"),
            _atom("weak", weak, "d2"),
        ],
        budget=_budget(),
    )
    assert dossier.relevant_candidate_count >= 1
    assert dossier.supported_count <= dossier.relevant_candidate_count
    assert dossier.checker["method"] == SUPPORT_METHOD
    assert "not NLI" in dossier.checker["note"]


def test_synthesis_always_derived_with_lineage():
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[_atom("cond-a", COND_A, "cond-a")],
        synthesis_text="Batching may help throughput.",
        synthesis_lineage="derived from ['cond-a'] via lexical-heuristic-v1",
        budget=_budget(),
    )
    synth = dossier.token_accounting["synthesis"]
    assert synth["status"] == "derived"
    assert "cond-a" in synth["lineage"]
    assert dossier.adapter_honesty["synthesis_status"] == "derived"


def test_adapter_honesty_conformance_is_not_harness_loading():
    report = adapter_report(schema_conformance=True)
    assert report["schema_conformance"] is True
    assert report["harness_loading_verified"] is False
    assert report["tier1_action_gate_label"] == TIER1_GATE_LABEL == "heuristic"
    assert "never authorizes execution" in report["tier1_note"]


def test_token_accounting_counts_wrappers_and_metadata():
    text = COND_A
    dossier = compile_dossier(
        query=QUERY, objective=QUERY,
        candidates=[_atom(
            "cond-a", text, "cond-a",
            relations=[{"rel_type": "qualifies", "subject": "a", "object": "b"}],
        )],
        budget=_budget(),
    )
    from trace_lite.cordis import estimate_tokens

    sel = dossier.selections[0]
    assert sel.token_cost >= estimate_tokens(text)  # wrappers/metadata counted
    acct = dossier.token_accounting
    budget = dossier.budget
    assert acct["total"] == (
        budget["system_prompt_tokens"]
        + acct["mandatory_cost"] + acct["goal_cost"] + acct["evidence_cost"]
    )
    assert acct["total"] <= 3500


def test_anchored_excerpt_is_source_linked_never_prefix():
    filler = "padding words without signal here. " * 100
    text = filler + "Batching improves throughput under the final anchor."
    excerpt, sb, eb, is_ex = anchored_excerpt(text, QUERY, 40)
    assert is_ex is True
    assert "throughput" in excerpt
    assert text.encode("utf-8")[sb:eb] == excerpt.encode("utf-8")
    assert not text.startswith(excerpt)


# -- additive API route ---------------------------------------------------------------

def test_api_dossier_route_end_to_end(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "b.md").write_text("# batch\n\n" + COND_A + "\n", encoding="utf-8")
    (vault / "c.md").write_text("# contention\n\n" + COND_B + "\n", encoding="utf-8")
    client = TestClient(create_app(tmp_path / "dossier.db", vault))
    with client:
        assert client.post("/api/sync", json={}).status_code == 200
        resp = client.post("/api/dossier", json={
            "query": QUERY, "objective": QUERY, "goal_id": "opaque-1", "limit": 5,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == QUERY
        assert body["objective"] == QUERY
        assert body["goal_id"] == "opaque-1"
        assert body["snapshot"]["generation_id"]
        assert body["snapshot"]["watermark"]
        assert body["checker"]["method"] == SUPPORT_METHOD
        assert body["adapter_honesty"]["tier1_action_gate_label"] == "heuristic"
        assert body["support_state"] in ("supported", "partial", "conflicting", "insufficient")
        for sel in body["selections"]:
            assert sel["citation_valid"] is True
            blob = sel["excerpt_text"].encode("utf-8")
            assert sel["end_byte"] - sel["start_byte"] >= len(blob)
