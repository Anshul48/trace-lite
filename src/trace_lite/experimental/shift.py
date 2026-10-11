"""Embedding-shift delta boundary scorer (R08 experimental).

Scores each candidate cut by the cosine distance between the hashed
bag-of-words vectors of the text windows on either side, using the pinned
R03 hashbow space (``filing.engine.text_vector``: deterministic
sha256(token)-mod-128 counts, L2-normalized; no learned weights, no
downloads). A boundary is shift-like when adjacent windows point in
different directions of that space.

Deterministic: pure function of the text and the frozen window width.
"""

from __future__ import annotations

import math

from ..filing.engine import text_vector

WINDOW_CHARS = 160


def _cosine(a: list[float], b: list[float]) -> float:
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denom == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


class EmbeddingShiftSignal:
    """Boundary signal named ``hashbow-shift-cos``."""

    name = "hashbow-shift-cos"

    def __init__(self, window: int = WINDOW_CHARS) -> None:
        self.window = window

    def score(self, text: str, offsets: list[int]) -> list[float]:
        """Cosine distance between the side windows at each offset."""
        out: list[float] = []
        for at in offsets:
            left = text[max(0, at - self.window) : at]
            right = text[at : at + self.window]
            if not left.strip() or not right.strip():
                out.append(0.0)
                continue
            dist = 1.0 - _cosine(text_vector(left), text_vector(right))
            out.append(max(0.0, dist))
        return out
