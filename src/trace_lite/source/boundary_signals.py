"""Pluggable boundary-signal interface (R08, additive).

A ``BoundarySignal`` scores candidate cut points (character offsets) inside
one text. Experimental refiners in ``trace_lite.experimental`` map chosen
cuts to byte-exact spans. This module adds no behavior to
``segmentation.py`` / ``views.py`` (both untouched); it only declares the
protocol, a registry, and shared byte-exact splitting helpers.
"""

from __future__ import annotations

import re
from typing import Protocol

# Read-only mirror of the sentence-boundary pattern used by
# ``source.segmentation`` (private ``_SENTENCE_SPLIT_RE`` there). Mirrored —
# not imported — so this module never couples to segmentation internals.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

#: Minimum characters of context required on each side of a cut so signals
#: cannot propose degenerate slivers.
MIN_SIDE_CHARS = 40


class BoundarySignal(Protocol):
    """Scores candidate cut offsets; higher means more boundary-like."""

    name: str

    def score(self, text: str, offsets: list[int]) -> list[float]:
        """Return one score per offset, in order. Must be deterministic."""
        ...


_REGISTRY: dict[str, BoundarySignal] = {}


def register(signal: BoundarySignal) -> BoundarySignal:
    """Register a signal implementation under its ``name``."""
    _REGISTRY[signal.name] = signal
    return signal


def get(name: str) -> BoundarySignal:
    """Fetch a registered signal by name (``KeyError`` when unknown)."""
    return _REGISTRY[name]


def names() -> list[str]:
    """Registered signal names, sorted."""
    return sorted(_REGISTRY)


def candidate_offsets(text: str, *, min_side: int = MIN_SIDE_CHARS) -> list[int]:
    """Inter-sentence char offsets eligible as cut points.

    Only boundaries with at least ``min_side`` characters on each side are
    returned, in increasing order. Deterministic.
    """
    cuts: list[int] = []
    for match in SENTENCE_SPLIT_RE.finditer(text):
        at = match.start()
        if at >= min_side and len(text) - match.end() >= min_side - len(match.group()):
            # Require real content on both sides (not just whitespace tail).
            if text[:at].strip() and text[match.end():].strip():
                cuts.append(at)
    return cuts


def char_span_to_bytes(
    text: str, char_start: int, char_end: int, *, byte_base: int = 0
) -> tuple[int, int]:
    """Map a char slice to exact UTF-8 half-open byte offsets (no estimation)."""
    start_byte = byte_base + len(text[:char_start].encode("utf-8"))
    end_byte = start_byte + len(text[char_start:char_end].encode("utf-8"))
    return start_byte, end_byte
