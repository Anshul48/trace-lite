"""R08 prediction-error tests (new file; no pre-existing tests touched).

Covers the pluggable boundary-signal interface, both experimental scorers
(determinism, byte-exact refinement, split-only containment), the C8-2
qualifier-severance probe, and the EVALUATION §6 repair comparison
(negation/actor-reversal must not be missed by the deterministic policy).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trace_lite.experimental import refine_cuts, refine_text  # noqa: E402
from trace_lite.experimental import qualifiers as qual_mod  # noqa: E402
from trace_lite.experimental import repair as repair_mod  # noqa: E402
from trace_lite.experimental.shift import EmbeddingShiftSignal  # noqa: E402
from trace_lite.experimental.surprise import NGramSurpriseSignal  # noqa: E402
from trace_lite.source import boundary_signals as bs  # noqa: E402
from trace_lite.source.segmentation import (  # noqa: E402
    RawSpan,
    segment_text,
    verify_spans,
)

PARA = (
    "Unless the database uses WAL mode, readers block during a write. "
    "The checkpoint path assumes this ordering for recovery. "
    "Operators who ignore the precondition see spurious read errors."
)


def test_signal_registry_lists_both_signals():
    assert "ngram-surprise-trigram" in bs.names()
    assert "hashbow-shift-cos" in bs.names()


def test_signals_are_deterministic():
    text = PARA * 2
    cuts = bs.candidate_offsets(text)
    assert len(cuts) >= 2
    for signal in (NGramSurpriseSignal(), EmbeddingShiftSignal()):
        assert signal.score(text, cuts) == signal.score(text, cuts)
        assert len(signal.score(text, cuts)) == len(cuts)


def test_signal_scores_empty_offsets():
    assert NGramSurpriseSignal().score(PARA, []) == []
    assert EmbeddingShiftSignal().score(PARA, []) == []


def test_structural_path_is_untouched_baseline():
    assert refine_text(PARA, method="structural") == segment_text(PARA)
    assert refine_cuts(PARA, method="structural") == []


def test_refinement_is_byte_exact_all_methods():
    for method in ("structural", "surprise", "shift"):
        spans = refine_text(PARA, method=method)
        assert spans, method
        assert verify_spans(PARA, spans), method
        assert [s.ordinal for s in spans] == list(range(len(spans))), method


def test_refinement_is_split_only_within_structural_spans():
    base = segment_text(PARA)
    assert len(base) == 1
    for method in ("surprise", "shift"):
        refined = refine_text(PARA, method=method)
        blob = PARA.encode("utf-8")
        covered = b"".join(blob[s.start_byte : s.end_byte] for s in refined)
        # Every refined byte belongs to the single structural span; cuts only
        # drop inter-sentence separators, never invent text.
        assert covered in blob[base[0].start_byte : base[0].end_byte]
        for span in refined:
            assert base[0].start_byte <= span.start_byte < span.end_byte
            assert span.end_byte <= base[0].end_byte
            assert span.kind == base[0].kind


def test_atomic_spans_never_refined():
    text = "```python\nx = 1\ny = 2\n```\n\n" + PARA
    base = segment_text(text)
    assert any(s.kind == "code" for s in base)
    for method in ("surprise", "shift"):
        refined = refine_text(text, method=method)
        codes = [s for s in refined if s.kind == "code"]
        assert len(codes) == 1
        assert codes[0].text == next(s for s in base if s.kind == "code").text


def test_short_spans_stay_atomic():
    assert refine_text("Too short. One cut.", method="surprise") == segment_text(
        "Too short. One cut."
    )


def test_unknown_method_raises():
    try:
        refine_text(PARA, method="smart")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_qualifier_fixtures_are_single_structural_spans():
    for fix in qual_mod.fixtures():
        assert len(segment_text(fix.text)) == 1, fix.fid


def test_structural_baseline_severs_nothing():
    for fix in qual_mod.fixtures():
        assert refine_cuts(fix.text, method="structural") == []


def test_predictive_cuts_sever_qualifiers():
    # C8-2 probe must bite: the signals demonstrably cut inside qualifier
    # paragraphs (promotion requires zero severance, which this falsifies).
    severed = {
        method: sum(
            1 for f in qual_mod.fixtures() if refine_cuts(f.text, method=method)
        )
        for method in ("surprise", "shift")
    }
    assert severed["surprise"] > 0
    assert severed["shift"] > 0


def test_repair_deterministic_closure_misses_nothing():
    assertions = repair_mod.base_assertions()
    for case in repair_mod.edit_cases():
        det = repair_mod.deterministic_closure(assertions, case.target_aid)
        expected = repair_mod.expected_affected(assertions, case)
        assert expected <= det, case.edit_id
        assert repair_mod.summarize(
            repair_mod.compare_all(assertions), assertions
        )["det"].missed_meaningful == 0


def test_repair_predictor_misses_negation_when_saving_work():
    # EVALUATION §6: a predictor that avoids work but misses negations fails.
    assertions = repair_mod.base_assertions()
    summaries = repair_mod.summarize(repair_mod.compare_all(assertions), assertions)
    assert summaries["pred@0.1"].missed_meaningful >= 1
    neg = next(
        c for c in repair_mod.compare_all(assertions) if c.edit_id == "E1-negation"
    )
    assert neg.meaning_changed
    assert neg.predictive_invalidated[0.1] == []


def test_repair_predictor_cannot_save_work_and_catch_all():
    assertions = repair_mod.base_assertions()
    summaries = repair_mod.summarize(repair_mod.compare_all(assertions), assertions)
    det = summaries["det"]
    for key in ("pred@0.02", "pred@0.05", "pred@0.1"):
        s = summaries[key]
        saves = s.touched_fraction < det.touched_fraction
        clean = s.missed_meaningful == 0 and not s.false_invalidations
        assert not (saves and clean), key


def test_refine_cuts_agree_with_refined_boundaries():
    spans = refine_text(PARA, method="shift")
    cuts = refine_cuts(PARA, method="shift")
    blob = PARA.encode("utf-8")
    char_starts = [
        len(blob[: s.start_byte].decode("utf-8")) for s in spans[1:]
    ]
    assert cuts == sorted(char_starts)


def test_rawspan_shape_preserved():
    spans = refine_text(PARA, method="surprise")
    assert all(isinstance(s, RawSpan) for s in spans)
