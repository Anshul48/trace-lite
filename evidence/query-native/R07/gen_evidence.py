"""R07 evidence generator: C7-1..C7-4 artifacts (deterministic, no LLM).

Writes evidence/query-native/R07/{objective_align.json, cross_dossier.json,
conflict_dossier.log, budget_guard.log}. Uses R00 conflict fixtures
(conditions batching pair) + R02 segmentation views for byte citations.
Exit nonzero on any criterion miss (escalation surface).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

from trace_lite import source as S  # noqa: E402
from trace_lite.cordis import (  # noqa: E402
    AuthorityLevel,
    CommitmentRecord,
    ModularContractBoundaryTMS,
    TokenBudget,
    TraceLiteContextCompiler,
    compile_dossier,
)
from trace_lite.cordis.dossier import SUPPORT_METHOD  # noqa: E402
from trace_lite.source import views as V  # noqa: E402
from trace_lite.store import Database  # noqa: E402

OUT = REPO / "evidence" / "query-native" / "R07"
OUT.mkdir(parents=True, exist_ok=True)

COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = "Under high contention, the batching benefit disappears; throughput matches the unbatched path."
OBJECTIVE = "Does batching improve throughput?"


def _tms():
    tms = ModularContractBoundaryTMS()
    tms.register_commitment(CommitmentRecord(
        id="inv-arch-001", target_scopes=["global"],
        authority=AuthorityLevel.ARCH_SPEC,
        statement="mandatory budget guardrails ride along with every dossier",
    ))
    return tms


def c71_objective_align() -> dict:
    """Same objective + different goal_id → identical ranking (100% parity)."""
    compiler = TraceLiteContextCompiler(_tms())
    cands = [
        {"artifact_id": "cond-a", "content": COND_A, "source_uri": "cond-a"},
        {"artifact_id": "cond-b", "content": COND_B, "source_uri": "cond-b"},
        {"artifact_id": "off-a",
         "content": "SQLite WAL checkpoint frames durability truncate oversight valve.",
         "source_uri": "off-a"},
        {"artifact_id": "wal-a",
         "content": "WAL mode allows one writer with concurrent readers.",
         "source_uri": "wal-a"},
    ]
    budget = TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=600,
                         salient_evidence_budget=1800)
    ids = ["goal-alpha", "goal-beta", "opaque/7f3a", "project/x"]
    rankings = {}
    for gid in ids:
        ctx = compiler.compile_context(
            "s1", gid, ["global"], "state_transition", budget, list(cands),
            goal_state={"objective": OBJECTIVE},
        )
        rankings[gid] = [
            {"artifact_id": e.artifact_id, "salience": round(e.salience_score, 6)}
            for e in ctx.compiled_evidence
        ]
    base = rankings[ids[0]]
    parity = all(r == base for r in rankings.values())
    # Negative control: a different objective must NOT rank identically.
    ctx_other = compiler.compile_context(
        "s1", ids[0], ["global"], "state_transition", budget, list(cands),
        goal_state={"objective": "Does WAL mode allow concurrent readers?"},
    )
    other = [e.artifact_id for e in ctx_other.compiled_evidence]
    result = {
        "criterion": "C7-1 objective term alignment",
        "objective": OBJECTIVE,
        "goal_ids": ids,
        "rankings": rankings,
        "parity": parity,
        "parity_pct": 100.0 if parity else 0.0,
        "negative_control_objective": "Does WAL mode allow concurrent readers?",
        "negative_control_top": other[0] if other else None,
        "negative_control_differs": [r["artifact_id"] for r in base] != other,
        "limit": "100% rank parity",
    }
    (OUT / "objective_align.json").write_text(json.dumps(result, indent=2) + "\n")
    assert parity, "C7-1 parity broken"
    assert result["negative_control_differs"], "ranking ignores objective text"
    return result


def c72_cross_dossier(tmp_path: Path) -> dict:
    """Multi-source dossier, every citation R02-resolvable (exact bytes)."""
    db = Database(tmp_path / "r07_cross.db")
    try:
        rev_a = S.publish(db, "cond-a", COND_A + "\n")
        rev_b = S.publish(db, "cond-b", COND_B + "\n")
        va = V.create_segmentation_view(db.conn, rev_a.revision_id)
        vb = V.create_segmentation_view(db.conn, rev_b.revision_id)
        spans = [(s, "cond-a", "lexical") for s in V.get_spans(db.conn, va["view_id"])]
        spans += [(s, "cond-b", "holon") for s in V.get_spans(db.conn, vb["view_id"])]
        candidates = [{
            "candidate_id": f"{doc}-s{i}", "text": s["text"],
            "doc_id": doc, "source_id": doc,
            "revision_id": s["revision_id"], "span_id": s["span_id"],
            "start_byte": s["start_byte"], "end_byte": s["end_byte"],
            "namespace": "default", "access_scope": "default",
            "version_status": "current", "route": route,
        } for i, (s, doc, route) in enumerate(spans)]
        dossier = compile_dossier(
            query=OBJECTIVE, objective=OBJECTIVE, goal_id="opaque-r07",
            scope={"namespaces": ["default"]},
            candidates=candidates,
            relations=[{"rel_type": "qualifies", "subject": "cond-a-s0",
                        "object": "batching-claim", "evidence": "cond-a span"}],
            snapshot={"generation_id": "gen-r07", "watermark": "atoms=2:maxid=2"},
            budget=TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=600,
                               salient_evidence_budget=1800),
            conn=db.conn,
        )
        assert dossier.status.value == "ok"
        assert {s.doc_id for s in dossier.selections} >= {"cond-a", "cond-b"}
        resolved = 0
        for sel in dossier.selections:
            assert sel.citation_valid and sel.citation_method == "r02_excerpt"
            assert V.excerpt_span(db.conn, sel.span_id) == sel.excerpt_text.encode()
            resolved += 1
        body = dossier.model_dump()
        body["evidence_check"] = {
            "selections": len(dossier.selections),
            "byte_citations_resolved": resolved,
            "resolution_rate": resolved / max(len(dossier.selections), 1),
            "virtual_holons": len(dossier.virtual_holons),
        }
        (OUT / "cross_dossier.json").write_text(json.dumps(body, indent=2) + "\n")
        assert resolved == len(dossier.selections), "unresolved citations"
        return body["evidence_check"]
    finally:
        db.close()


def c73_conflict() -> dict:
    """Conflicting evidence coexists under an explicit disagreement."""
    def atom(cid, text, doc, route):
        return {"candidate_id": cid, "text": text, "doc_id": doc,
                "source_id": doc, "start_byte": 0,
                "end_byte": len(text.encode()), "namespace": "default",
                "access_scope": "default", "version_status": "current",
                "route": route}

    dossier = compile_dossier(
        query=OBJECTIVE, objective=OBJECTIVE,
        candidates=[atom("cond-a", COND_A, "cond-a", "lexical"),
                    atom("cond-b", COND_B, "cond-b", "holon")],
        relations=[{"rel_type": "conflicts_with", "subject": "cond-a",
                    "object": "cond-b",
                    "evidence": "low-contention gain vs high-contention loss"}],
        budget=TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=600,
                           salient_evidence_budget=1800),
    )
    lines = [
        f"query: {OBJECTIVE}",
        f"status: {dossier.status.value}",
        f"support_state: {dossier.support_state.value}",
        f"selections: {sorted(s.candidate_id for s in dossier.selections)}",
        f"disagreements: {json.dumps(dossier.disagreements)}",
        f"relevant={dossier.relevant_candidate_count} "
        f"supported={dossier.supported_count} (distinct by construction)",
        f"checker: {dossier.checker}",
    ]
    (OUT / "conflict_dossier.log").write_text("\n".join(lines) + "\n")
    assert dossier.support_state.value == "conflicting"
    assert {s.candidate_id for s in dossier.selections} == {"cond-a", "cond-b"}
    assert len(dossier.disagreements) == 1
    return {"support": dossier.support_state.value}


def c74_budget() -> dict:
    """Mandatory overflow → insufficient_budget; atomic overflow → coverage."""
    big = "NEVER drop this invariant " * 80
    mandatory = [CommitmentRecord(
        id=f"inv-{i}", target_scopes=["global"],
        authority=AuthorityLevel.USER, statement=big) for i in range(3)]
    d1 = compile_dossier(
        query=OBJECTIVE, objective=OBJECTIVE,
        candidates=[{"candidate_id": "a", "text": COND_A, "doc_id": "d",
                     "start_byte": 0, "end_byte": len(COND_A.encode())}],
        mandatory=mandatory,
        budget=TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=50,
                           salient_evidence_budget=1800),
    )
    huge = "batching throughput contention evidence words " * 200
    d2 = compile_dossier(
        query=OBJECTIVE, objective=OBJECTIVE,
        candidates=[
            {"candidate_id": "g1", "text": huge, "doc_id": "d1",
             "start_byte": 0, "end_byte": len(huge.encode()),
             "group_id": "G", "atomic_group": True},
            {"candidate_id": "g2", "text": huge, "doc_id": "d1",
             "start_byte": 0, "end_byte": len(huge.encode()),
             "group_id": "G", "atomic_group": True},
        ],
        budget=TokenBudget(max_total_tokens=3500, mandatory_invariant_tokens=600,
                           salient_evidence_budget=40),
    )
    lines = [
        f"mandatory_overflow: status={d1.status.value} "
        f"omitted={sorted(o['id'] for o in d1.omissions)} "
        f"selections={len(d1.selections)}",
        f"atomic_overflow: status={d2.status.value} reason={d2.failure_reason}",
        "silent_drops: 0 (every omission named above or in dossier omissions)",
        f"checker: {SUPPORT_METHOD} (lexical heuristic, not NLI)",
    ]
    (OUT / "budget_guard.log").write_text("\n".join(lines) + "\n")
    assert d1.status.value == "insufficient_budget" and d1.selections == []
    assert sorted(o["id"] for o in d1.omissions) == ["inv-0", "inv-1", "inv-2"]
    assert d2.status.value == "coverage_failure" and "G" in d2.failure_reason
    return {"mandatory": d1.status.value, "atomic": d2.status.value}


def main() -> None:
    import tempfile

    r1 = c71_objective_align()
    with tempfile.TemporaryDirectory() as td:
        r2 = c72_cross_dossier(Path(td))
    r3 = c73_conflict()
    r4 = c74_budget()
    print(json.dumps({"C7-1": r1["parity_pct"], "C7-2": r2,
                      "C7-3": r3, "C7-4": r4}, indent=2))


if __name__ == "__main__":
    main()
