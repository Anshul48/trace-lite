"""Apply boundary signals as split-only refinements of structural spans.

``refine_text`` starts from the frozen R02 structural segmentation and
optionally adds cuts *inside* multi-sentence structural spans (never across
them, never merging, never touching code/table/heading spans). Every
refined span is a verbatim sub-slice of exactly one structural span with
exact UTF-8 byte offsets, so the R02 byte contract holds by construction.

Cut rule (frozen a priori, identical for both signals, no qrel tuning):
per structural span, score its inter-sentence candidates, cut at candidates
scoring above the span's own mean + 0.5*std, at most ``MAX_CUTS_PER_SPAN``
cuts, preferred highest-first with spacing enforced by the candidate
generator (``MIN_SIDE_CHARS`` on both sides of every cut).
"""

from __future__ import annotations

import statistics

from ..source import boundary_signals as bs
from ..source.segmentation import RawSpan, segment_text
from .shift import EmbeddingShiftSignal
from .surprise import NGramSurpriseSignal

MAX_CUTS_PER_SPAN = 2
#: Structural spans shorter than this are never refined (single-claim spans
#: stay atomic; also bounds the extra CPU on short docs). 120 chars still
#: requires at least two substantive sentences given MIN_SIDE_CHARS.
MIN_REFINE_CHARS = 120
#: Span kinds the signals must never cut (structural integrity wins, R02 C2-3).
ATOMIC_KINDS = frozenset({"code", "table", "heading"})

_SIGNALS: dict[str, bs.BoundarySignal] = {
    NGramSurpriseSignal.name: NGramSurpriseSignal(),
    EmbeddingShiftSignal.name: EmbeddingShiftSignal(),
}
bs.register(_SIGNALS[NGramSurpriseSignal.name])
bs.register(_SIGNALS[EmbeddingShiftSignal.name])


def _pick_cuts(scores: list[float], n_cuts: int = MAX_CUTS_PER_SPAN) -> list[int]:
    """Indices of scores above mean + 0.5*std, best-first, capped."""
    if len(scores) < 2:
        return []
    mean = statistics.fmean(scores)
    std = statistics.pstdev(scores)
    if std == 0.0:
        return []
    ranked = sorted(range(len(scores)), key=lambda i: -scores[i])
    return [i for i in ranked if scores[i] > mean + 0.5 * std][:n_cuts]


def refine_text(text: str, *, method: str = "structural") -> list[RawSpan]:
    """Segment ``text`` with the structural baseline plus an optional signal.

    ``method`` is ``structural`` (R02 baseline, untouched path), ``surprise``
    (``+ngram-surprise-trigram``), or ``shift`` (``+hashbow-shift-cos``).
    """
    base = segment_text(text)
    if method == "structural":
        return base
    signal = _SIGNALS[
        "ngram-surprise-trigram" if method == "surprise" else "hashbow-shift-cos"
    ]
    if method not in ("surprise", "shift"):
        raise ValueError(f"unknown refinement method: {method!r}")
    out: list[RawSpan] = []
    ordinal = 0
    for span in base:
        if (
            span.kind in ATOMIC_KINDS
            or len(span.text) < MIN_REFINE_CHARS
            or not bs.SENTENCE_SPLIT_RE.search(span.text)
        ):
            out.append(
                RawSpan(
                    ordinal=ordinal,
                    start_byte=span.start_byte,
                    end_byte=span.end_byte,
                    kind=span.kind,
                    header=span.header,
                    text=span.text,
                )
            )
            ordinal += 1
            continue
        offsets = bs.candidate_offsets(span.text)
        if len(offsets) < 2:
            out.append(
                RawSpan(
                    ordinal=ordinal,
                    start_byte=span.start_byte,
                    end_byte=span.end_byte,
                    kind=span.kind,
                    header=span.header,
                    text=span.text,
                )
            )
            ordinal += 1
            continue
        scores = signal.score(span.text, offsets)
        picks = sorted(offsets[i] for i in _pick_cuts(scores))
        bounds = [0, *picks, len(span.text)]
        for cs, ce in zip(bounds, bounds[1:]):
            piece = span.text[cs:ce]
            if not piece.strip():
                continue
            sb, eb = bs.char_span_to_bytes(
                span.text, cs, ce, byte_base=span.start_byte
            )
            out.append(
                RawSpan(
                    ordinal=ordinal,
                    start_byte=sb,
                    end_byte=eb,
                    kind=span.kind,
                    header=span.header,
                    text=piece,
                )
            )
            ordinal += 1
    return out


def refine_cuts(text: str, *, method: str = "structural") -> list[int]:
    """Character offsets of cuts *added* by the signal (structural → []).

    Because refinement is split-only inside structural spans, every interior
    boundary of a refined span that lies strictly inside one structural span
    is a signal-added cut. Used by the C8-2 qualifier-severance probe.
    """
    if method == "structural":
        return []
    if method not in ("surprise", "shift"):
        raise ValueError(f"unknown refinement method: {method!r}")
    blob = text.encode("utf-8")
    base = segment_text(text)
    refined = refine_text(text, method=method)
    cuts: list[int] = []
    for span in refined:
        if any(
            b.start_byte == span.start_byte and b.end_byte == span.end_byte
            for b in base
        ):
            continue  # unrefined span, not a cut product
        for parent in base:
            if parent.start_byte < span.start_byte < parent.end_byte:
                cuts.append(len(blob[: span.start_byte].decode("utf-8")))
                break
    return sorted(cuts)
