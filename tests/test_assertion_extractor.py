"""R04 structural extractor: relational fidelity + fallback (C4-1, C4-4 halves).

Pure-function tests against bounded fixtures (R00 DEV families plus the R04
condition probes). No database, no network, stdlib only.
"""

from trace_lite.graph import extractor as X
from trace_lite.graph.frames import REL_TYPES


def _spans(texts, run="t"):
    _ = run
    return [
        {"span_id": f"sp{i}", "text": text,
         "start_byte": 0, "end_byte": len(text.encode("utf-8"))}
        for i, text in enumerate(texts)
    ]


def _frames_for(text):
    result = X.extract_spans(_spans([text]), run_id="u1")
    assert result.status == "complete", result.warnings
    return result


def test_condition_mechanism_frame_roles():
    """C4-1: direction, polarity, condition, units preserved without loss."""
    result = _frames_for(
        "Batching improves throughput by 30% under low contention"
        " (fewer syscalls per item).")
    assert len(result.assertions) == 1
    frame = result.assertions[0]
    assert frame.subject_text == "Batching"
    assert frame.predicate == "improves"
    assert frame.object_text == "throughput by 30%"
    assert frame.polarity == "positive"
    assert frame.condition_text == "under low contention"
    assert frame.units == "30%"
    assert frame.epistemic_status == "source_asserted"
    assert frame.status == "asserted"
    edge = next(e for e in result.relations if e.rel_type == "improves")
    assert edge.subject_text == "Batching"
    assert edge.status == "asserted"
    qualifier = next(e for e in result.relations if e.rel_type == "qualifies")
    assert qualifier.subject_text == "under low contention"
    assert qualifier.object_assertion_id == frame.assertion_id


def test_no_invented_mechanism():
    """C4-1: the parenthetical is a verbatim qualifier, never a causal claim."""
    result = _frames_for(
        "Batching improves throughput by 30% under low contention"
        " (fewer syscalls per item).")
    frame = result.assertions[0]
    assert frame.qualifier_texts == ["fewer syscalls per item"]
    blob = " ".join(
        " ".join([a.predicate, a.subject_text or "", a.object_text or ""])
        for a in result.assertions
    ).lower()
    assert "cache" not in blob and "bouncing" not in blob
    assert "syscall" not in blob  # qualifier only, not a causal surface
    assert all(e.rel_type in REL_TYPES for e in result.relations)


def test_contradictory_assertions_preserved_opposing():
    """EVALUATION counterexamples: opposing pair coexists, never merged."""
    result = X.extract_spans(_spans([
        "Batching improves throughput by 30% under low contention.",
        "Under high contention, the batching benefit disappears.",
    ]), run_id="u2")
    positives = [a for a in result.assertions if a.polarity == "positive"]
    negatives = [a for a in result.assertions if a.polarity == "negative"]
    assert len(positives) == 1 and len(negatives) == 1
    assert positives[0].assertion_id != negatives[0].assertion_id
    # No merge edge, no similarity edge: only structural verb/qualifier edges.
    assert {e.rel_type for e in result.relations} <= REL_TYPES
    assert not any(e.rel_type in ("supports", "contradicts", "causes")
                   for e in result.relations)


def test_negation_polarity_and_temporal_scope():
    """Negated span must not support the positive claim (R00 negation)."""
    pos = _frames_for("WAL mode allows one writer with concurrent readers.")
    assert pos.assertions[0].polarity == "positive"
    assert pos.assertions[0].object_text == "one writer with concurrent readers"
    neg = _frames_for(
        "The legacy rollback-journal mode does NOT allow concurrent readers"
        " during a write.")
    frame = neg.assertions[0]
    assert frame.polarity == "negative"
    assert frame.subject_text == "The legacy rollback-journal mode"
    assert frame.object_text == "concurrent readers"
    assert frame.temporal_scope == "during a write"


def test_unless_and_only_conditions_retained():
    """EVALUATION 'unless/only' exceptions are kept as qualifier edges."""
    unless = _frames_for("Batching helps unless queues are saturated.")
    frame = unless.assertions[0]
    assert frame.condition_text == "unless queues are saturated"
    assert any(e.rel_type == "qualifies"
               and e.subject_text == "unless queues are saturated"
               for e in unless.relations)
    only = _frames_for("Only under low contention does batching improve throughput.")
    only_frame = only.assertions[0]
    assert only_frame.subject_text == "batching"
    assert only_frame.condition_text == "Only under low contention"
    assert any(e.rel_type == "qualifies" for e in only.relations)


def test_restores_condition_probe():
    """C4-3 (extractor half): restores keeps its conditional qualifier."""
    result = _frames_for(
        "Batching restores throughput benefit under high contention.")
    frame = result.assertions[0]
    assert frame.predicate == "restores"
    assert frame.condition_text == "under high contention"
    assert any(e.rel_type == "qualifies"
               and e.subject_text == "under high contention"
               and e.object_assertion_id == frame.assertion_id
               for e in result.relations)


def test_entity_ambiguity_never_forced():
    """R00 entity-ambiguity: qualified reporters stay distinct; bare stays open."""
    result = X.extract_spans(_spans([
        "Chen (storage team) reports checkpoint latency p99 of 40ms on NVMe.",
        "Chen (query team) reports checkpoint latency is not the bottleneck.",
        "Chen reports latency improved.",
    ]), run_id="u3")
    subjects = [a.subject_text for a in result.assertions
                if a.predicate == "reports"]
    assert "Chen (storage team)" in subjects
    assert "Chen (query team)" in subjects
    bare = next(a for a in result.assertions if a.subject_text == "Chen")
    assert bare.status == "unresolved"
    keys = {m.surface: m.canonical_key for a in result.assertions
            for m in a.mentions if m.role == "subject"}
    assert keys["Chen (storage team)"] != keys["Chen (query team)"]
    assert keys["Chen"] is None
    unresolved = next(e for e in result.relations if e.status == "unresolved")
    assert unresolved.subject_mention_id is None  # bound nothing
    assert len(unresolved.candidates) <= X.LONG_RANGE_TOP_K


def test_pronoun_subject_stays_unresolved():
    result = _frames_for("It improves throughput.")
    assert result.assertions[0].status == "unresolved"
    assert result.relations[0].status == "unresolved"


def test_reports_links_outer_to_inner_assertion():
    result = _frames_for(
        "Chen (query team) reports checkpoint latency is not the bottleneck.")
    outer = next(a for a in result.assertions if a.predicate == "reports")
    inner = next(a for a in result.assertions if a.predicate == "is")
    assert outer.polarity == "positive"  # the reporting itself is asserted
    assert inner.polarity == "negative"
    edge = next(e for e in result.relations if e.rel_type == "reports")
    assert edge.object_assertion_id == inner.assertion_id
    assert edge.object_mention_id is None


def test_similarity_never_becomes_support():
    """Word salad and paraphrase-adjacent text yield no relational edges."""
    result = X.extract_spans(_spans([
        "SQLite WAL checkpoint frames durability truncate oversight valve.",
    ]), run_id="u4")
    assert result.assertions == [] and result.relations == []
    assert result.status == "complete"


def test_epistemic_status_never_validated():
    result = X.extract_spans(_spans([
        "Batching improves throughput by 30% under low contention.",
        "WAL mode allows one writer with concurrent readers.",
        "The zebra-striped backoff timer uses a 250ms base interval.",
    ]), run_id="u5")
    assert result.assertions, "expected frames from supported patterns"
    assert {a.epistemic_status for a in result.assertions} == {"source_asserted"}
    assert {e.epistemic_status for e in result.relations} == {"source_asserted"}


def test_sentence_byte_ranges_are_exact():
    text = "Batching improves throughput. héllo wörld ☃ improves morale under load."
    (span,) = _spans([text])
    result = X.extract_spans([span], run_id="u6")
    blob = text.encode("utf-8")
    assert len(result.assertions) == 2
    for frame in result.assertions:
        sl = blob[frame.start_byte:frame.end_byte].decode("utf-8")
        assert frame.subject_text in sl and frame.predicate in sl.lower()
    assert result.assertions[0].end_byte <= result.assertions[1].start_byte


def test_extraction_is_deterministic():
    texts = ["Batching improves throughput under low contention.",
             "It improves throughput."]
    first = X.extract_spans(_spans(texts), run_id="det")
    second = X.extract_spans(_spans(texts), run_id="det")
    assert [a.assertion_id for a in first.assertions] == [
        a.assertion_id for a in second.assertions]
    assert [e.relation_id for e in first.relations] == [
        e.relation_id for e in second.relations]


def test_bounded_windows_no_cross_span_asserted_edges():
    """Asserted edges never span two sources of truth; only candidates reach."""
    result = X.extract_spans(_spans([
        "Batching improves throughput under low contention.",
        "It improves throughput.",
    ]), run_id="u7")
    by_id = {a.assertion_id: a for a in result.assertions}
    for edge in result.relations:
        if edge.status == "asserted" and edge.object_assertion_id is not None:
            target = by_id[edge.object_assertion_id]
            assert target.span_id == edge.span_id


def test_fallback_malformed_inputs_never_raise():
    """C4-4 (extractor half): hostile inputs yield partial graphs, no crash."""
    assert X.extract_spans(None).status == "partial"  # type: ignore[arg-type]
    assert X.extract_spans("not a list").status == "partial"  # type: ignore[arg-type]
    hostile = [
        None, 42, b"bytes text", {"no": "text"}, {"text": 42, "span_id": "x"},
        {"text": "ok improves things.", "span_id": ""},
        {"span_id": "e1", "text": ""},
        {"span_id": "e2", "text": "   "},
        {"span_id": "e3", "text": "Batching improves throughput."},
        {"span_id": "e4", "text": "x" * 200000},
        {"span_id": "e5", "text": "héllo 🚀\r\n\U0001f600 improves things ☃"},
        {"span_id": "e6", "text": "((((((("},
        {"span_id": "e7", "text": "under under under improves improves improves"},
        {"span_id": "e8", "text": "Only only only does does does batching improve."},
    ]
    result = X.extract_spans(hostile, run_id="hostile")  # type: ignore[list-item]
    assert result.status == "partial"
    assert result.spans_skipped == 6
    assert result.spans_seen == 8
    blob_ok = True
    for frame in result.assertions:
        try:
            assert frame.start_byte <= frame.end_byte
        except AssertionError:
            blob_ok = False
    assert blob_ok
    assert X.extract_spans([], run_id="empty").status == "complete"
