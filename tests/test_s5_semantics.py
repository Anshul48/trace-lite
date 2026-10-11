"""S5 semantic-value trial (contract TL-QN-2026-09-12.1, steering S5 + B2).

Proves whether added semantic complexity earns its keep on a fixed
10-question set (2 per prescribed category: beyond-top-K, conditional
disagreement, quantities, actor reversal, source updates):

- the "cheap structure" definition is frozen BEFORE the trial
  (structural markdown hierarchy + entity co-occurrence clusters —
  NOT the 14-verb regex floor, NOT a per-sentence SLM);
- matched arms: flat-hybrid vs structural-expansion vs
  selective-graph/holon vs bounded query-time interpretation;
- exit metrics: evidence completeness / qualifier retention /
  unsupported-relation rate / missing counterevidence at measured cost
  (operation counts, never wall-time assertions).

Writes ``evidence/query-native/_integration/S5/s5_trial.json``
(machine-written artifact, S4 precedent). R08 stays parked: nothing
here predicts, segments, or repairs.
"""

from __future__ import annotations

import json
from pathlib import Path

from trace_lite.obsidian import semantics as S
from trace_lite.obsidian import structure as ST

REPO_ROOT = Path(__file__).resolve().parents[1]
S5_DIR = REPO_ROOT / "evidence" / "query-native" / "_integration" / "S5"

CATEGORIES = {"beyond-topk", "disagreement", "quantity", "actor", "update"}


def _idx():
    return S.build_index()


def _trial():
    result = S.run_trial()
    S5_DIR.mkdir(parents=True, exist_ok=True)
    (S5_DIR / "s5_trial.json").write_text(json.dumps(result, indent=1) + "\n")
    return result


def test_cheap_structure_definition_frozen():
    """B2: the definition exists before the trial, names the middle ground,
    and explicitly excludes both the regex floor and the SLM."""
    frozen = ST.CHEAP_STRUCTURE_DEF
    assert frozen["version"] == "v1"
    assert set(frozen["block_kinds"]) == {
        "section", "list", "callout", "table", "code"}
    assert set(frozen["entity_sources"]) == {
        "wikilink-target", "tag", "capitalized-phrase"}
    excluded = " ".join(str(x) for x in frozen["explicitly_excluded"])
    assert "SLM" in excluded and "triple" in excluded
    assert S.STRUCTURE_DEF_VERSION == "v1"
    assert S.run_trial()["structure_def"]["version"] == "v1"


def test_structure_uses_no_verb_triples_or_models():
    """Cheap structure is syntax only: no verb-pattern table, no triples,
    no model calls anywhere in the module."""
    assert not hasattr(ST, "_VERB_PATTERNS")
    assert "verb" not in ST.CHEAP_STRUCTURE_DEF["entity_sources"]
    # table block: members come only from repeated syntax surfaces
    # ("NVMe" appears twice) — no verb patterns, no invented relations.
    idx = _idx()
    table = idx.by_id[S.find_atom(idx, "wal-mode.md", "| drive |")]
    assert idx.clusters[table.atom_id].members == ["NVMe"]


def test_blocks_and_mentions_byte_exact():
    """Every block span and every cluster mention span slices its note."""
    idx = _idx()
    n_blocks = n_mentions = 0
    for doc_id, raw in S.CORPUS.items():
        blob = raw.encode("utf-8")
        blocks, clusters = ST.build_note(doc_id, raw)
        assert blocks, f"no blocks for {doc_id}"
        for b in blocks:
            assert blob[b.span.start_byte:b.span.end_byte] == \
                b.span.text.encode("utf-8")
            n_blocks += 1
        for c in clusters:
            for spans in c.mentions.values():
                for span in spans:
                    assert blob[span.start_byte:span.end_byte] == \
                        span.text.encode("utf-8")
                    n_mentions += 1
    assert n_blocks > 15 and n_mentions > 10


def test_question_set_covers_prescribed_categories():
    """Two questions per steering-mandated category."""
    assert len(S.QUESTIONS) == 10
    counts: dict[str, int] = {}
    for q in S.QUESTIONS:
        counts[q.category] = counts.get(q.category, 0) + 1
    assert set(counts) == CATEGORIES
    assert all(n == 2 for n in counts.values())


def test_beyond_topk_flat_misses_structural_recovers():
    """Q1 gold lives outside flat top-3; cheap-structure expansion
    recovers every gold block with a recorded structural reason."""
    idx = _idx()
    q1 = next(q for q in S.QUESTIONS if q.qid == "Q1")
    flat = S.grade(idx, q1, S.run_flat(idx, q1))
    struct = S.grade(idx, q1, S.run_structural(idx, q1))
    assert flat.completeness == 0.0
    assert struct.completeness == 1.0
    assert struct.correct and not flat.correct


def test_structural_expansion_bounded_and_reasoned():
    """Expansion stays within budget and every added block names a
    definition-sanctioned structural reason."""
    idx = _idx()
    allowed = ("same-section", "shared-wikilink", "cluster-overlap")
    for q in S.QUESTIONS:
        res = S.run_structural(idx, q)
        seeds = [a for a in S.flat_rank(idx, q.query)
                 if not S._is_stale(idx, a)][: S.TOP_K]
        extra = [a for a in res.returned if a not in seeds]
        assert len(extra) <= S.EXPAND_TOTAL
        assert len(res.expansion_reasons) == len(extra)
        assert all(r.split(" via ")[0] in extra for r in res.expansion_reasons)
        assert all(any(r.split(" via ")[1].startswith(k) for k in allowed)
                   for r in res.expansion_reasons)


def test_conditional_disagreement_keeps_counterevidence():
    """Q3/Q7: the baseline drops the counter-block; structural and graph
    arms retain it, emitting zero unsupported relations."""
    idx = _idx()
    for qid in ("Q3", "Q7"):
        q = next(x for x in S.QUESTIONS if x.qid == qid)
        flat = S.grade(idx, q, S.run_flat(idx, q))
        assert flat.missing_counterevidence == 1.0
        for arm in (S.run_structural, ):
            assert S.grade(idx, q, arm(idx, q)).missing_counterevidence == 0.0
    ledger = S.PromotionLedger()
    groups = S.build_groups(idx)
    for qid in ("Q3", "Q7"):
        q = next(x for x in S.QUESTIONS if x.qid == qid)
        scored = S.grade(idx, q, S.run_graph(idx, q, ledger, groups))
        assert scored.missing_counterevidence == 0.0
        assert scored.unsupported_relations == 0.0


def test_source_updates_exclude_stale():
    """Q9/Q10: the flat arm serves the superseded note; structure-aware
    arms filter it via explicit Status syntax and answer current."""
    idx = _idx()
    for qid in ("Q9", "Q10"):
        q = next(x for x in S.QUESTIONS if x.qid == qid)
        assert S.grade(idx, q, S.run_flat(idx, q)).stale_included is True
        assert S.grade(idx, q, S.run_structural(idx, q)).stale_included is False
        interp = S.interpret(idx, q, S.run_structural(idx, q).returned)
        assert interp.answer == "64 pages" and not interp.abstained


def test_quantities_exact_with_verbatim_units():
    """Interpretation returns exact quantities with source spacing."""
    idx = _idx()
    for qid, expected in (("Q5", "40ms"), ("Q6", "400ms")):
        q = next(x for x in S.QUESTIONS if x.qid == qid)
        res = S.interpret(idx, q, S.run_structural(idx, q).returned)
        assert res.answer == expected, (qid, res.answer)
        assert S.grade(idx, q, res).correct is True


def test_actor_reversal_attribution_without_merging():
    """Q7/Q8 attribute to the qualified actor; no arm ever emits a
    merged bare-'Chen' claim (the unsupported-relation control)."""
    idx = _idx()
    q7 = next(x for x in S.QUESTIONS if x.qid == "Q7")
    q8 = next(x for x in S.QUESTIONS if x.qid == "Q8")
    pool7 = S.run_structural(idx, q7).returned
    pool8 = S.run_structural(idx, q8).returned
    assert S.interpret(idx, q7, pool7).answer == "40ms"
    assert S.interpret(idx, q8, pool8).answer == "Chen (query team)"
    ledger = S.PromotionLedger()
    groups = S.build_groups(idx)
    for q, pool in ((q7, pool7), (q8, pool8)):
        results = [S.run_structural(idx, q),
                   S.run_graph(idx, q, ledger, groups),
                   S.interpret(idx, q, pool)]
        for res in results:
            for rel in res.relations:
                assert "bare-Chen-merged" not in str(rel)
                assert rel.get("supported", False) is True
            assert S.grade(idx, q, res).unsupported_relations == 0.0


def test_abstention_on_uncertain_semantics():
    """Rules for explicit syntax; abstain everywhere else, with reasons."""
    idx = _idx()
    pronoun = S.abstain_probe(idx, "It improves throughput under load.")
    assert pronoun.abstained and pronoun.abstain_reason == "pronoun-subject"
    ambiguous = S.abstain_probe(idx, "Chen confirms the bound.")
    assert ambiguous.abstained and ambiguous.abstain_reason == "ambiguous-actor"
    vague = S.abstain_probe(
        idx, "Batching seems broadly beneficial for performance.")
    assert vague.abstained and vague.abstain_reason == "no-explicit-syntax"
    for probe in (pronoun, ambiguous, vague):
        assert probe.answer is None and probe.relations == []


def test_interpretation_budget_honored():
    """Bounded query-time interpretation: at most 8 sentences per question."""
    idx = _idx()
    for q in S.QUESTIONS:
        res = S.interpret(idx, q, S.run_structural(idx, q).returned)
        assert res.cost["sentences"] <= S.INTERPRET_BUDGET


def test_selective_promotion_logic():
    """Promotion needs reuse (2+ distinct questions) AND usefulness;
    single-use or never-useful groups stay unpromoted."""
    ledger = S.PromotionLedger()
    ledger.record("g-single", "Q1", useful=True)
    assert "g-single" not in ledger.promoted()
    ledger.record("g-useless", "Q1", useful=False)
    ledger.record("g-useless", "Q2", useful=False)
    assert "g-useless" not in ledger.promoted()
    ledger.record("g-hot", "Q1", useful=False)
    ledger.record("g-hot", "Q2", useful=True)
    assert "g-hot" in ledger.promoted()


def test_trial_promotion_reduces_repeated_work():
    """Round 2 resolves promoted groups by lookup: fewer index scans at
    identical completeness; only the reused-useful group is promoted."""
    result = _trial()
    promo = result["promotion"]
    assert promo["promoted"] == ["group:Batching"]
    assert promo["round2_groups"] < promo["round1_groups"]
    assert (promo["round2_completeness"] == promo["round1_completeness"]
            == len(S.QUESTIONS))


def test_verdicts_adopt_at_equal_correctness():
    """Exit criterion computed from measurements: every complexity arm
    beats or matches flat correctness; verdicts adopt (bounded for D)."""
    result = _trial()
    table, verdicts = result["table"], result["verdicts"]
    base = table["flat"]["correctness"]
    assert table["structural"]["completeness"] > table["flat"]["completeness"]
    assert (table["graph"]["missing_counterevidence"]
            < table["flat"]["missing_counterevidence"])
    for arm in ("structural", "graph", "interpret"):
        assert table[arm]["correctness"] >= base
        assert table[arm]["unsupported_relations"] == 0.0
    assert verdicts == {"structural": "adopt", "graph": "adopt",
                        "selective-promotion": "adopt",
                        "interpret": "adopt-bounded"}


def test_graph_relations_byte_anchored():
    """Every selective-graph relation anchor slices its source note."""
    idx = _idx()
    ledger = S.PromotionLedger()
    groups = S.build_groups(idx)
    checked = 0
    for q in S.QUESTIONS:
        for rel in S.run_graph(idx, q, ledger, groups).relations:
            atom = idx.by_id[rel["atom"]]
            blob = S.CORPUS[atom.doc_id].encode("utf-8")
            start, end = rel["anchor"]
            assert blob[start:end] == atom.text.encode("utf-8")
            assert atom.text.encode("utf-8") == \
                S.CORPUS[atom.doc_id].encode("utf-8")[atom.start_byte:
                                                      atom.end_byte]
            checked += 1
    assert checked > 20


def test_trial_artifact_written():
    """Machine-written evidence artifact carries def version, table,
    verdicts, and corpus digest."""
    result = _trial()
    path = S5_DIR / "s5_trial.json"
    assert path.exists()
    stored = json.loads(path.read_text())
    assert stored["structure_def"]["version"] == "v1"
    assert stored["trial"] == S.TRIAL_VERSION
    assert set(stored["table"]) == {"flat", "structural", "graph", "interpret"}
    assert stored["verdicts"] == result["verdicts"]
    assert len(stored["corpus_digest"]) == 16
