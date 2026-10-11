"""Character n-gram entropy boundary scorer (R08 experimental).

Fits a character trigram model on the document's own text (no external
corpus, no downloads) and scores each candidate cut by the mean
negative-log-likelihood (surprisal) of the following window. A boundary is
surprising when the text that follows it is unpredictable from the
document's own character statistics.

Deterministic: fixed n, fixed smoothing, dict-iteration-free math.
"""

from __future__ import annotations

import math
from collections import Counter

N = 3
ALPHA = 0.5
WINDOW_CHARS = 48


class NGramSurpriseSignal:
    """Boundary signal named ``ngram-surprise-trigram``."""

    name = "ngram-surprise-trigram"

    def __init__(
        self, n: int = N, alpha: float = ALPHA, window: int = WINDOW_CHARS
    ) -> None:
        self.n = n
        self.alpha = alpha
        self.window = window

    def fit_counts(self, text: str) -> tuple[dict[str, Counter], Counter, int]:
        """Trigram context counts over the lowercased text (with padding)."""
        seq = "\x02" * (self.n - 1) + text.lower() + "\x03"
        ctx_counts: dict[str, Counter] = {}
        ctx_totals: Counter = Counter()
        alphabet = set(seq)
        for i in range(len(seq) - self.n + 1):
            ctx, ch = seq[i : i + self.n - 1], seq[i + self.n - 1]
            ctx_counts.setdefault(ctx, Counter())[ch] += 1
            ctx_totals[ctx] += 1
        return ctx_counts, ctx_totals, len(alphabet)

    def score(self, text: str, offsets: list[int]) -> list[float]:
        """Mean char NLL of the ``window`` chars following each offset."""
        if not offsets:
            return []
        ctx_counts, ctx_totals, alpha_size = self.fit_counts(text)
        lowered = "\x02" * (self.n - 1) + text.lower() + "\x03"
        # Char index i in text maps to i + (n-1) in the padded sequence.
        out: list[float] = []
        for at in offsets:
            window = text[at : at + self.window].lower()
            if not window.strip():
                out.append(0.0)
                continue
            nll = 0.0
            for j, ch in enumerate(window):
                pos = at + (self.n - 1) + j
                ctx = lowered[pos - (self.n - 1) : pos]
                counts = ctx_counts.get(ctx)
                tot = ctx_totals.get(ctx, 0)
                prob = ((counts.get(ch, 0) if counts else 0) + self.alpha) / (
                    tot + self.alpha * alpha_size
                )
                nll += -math.log(prob)
            out.append(nll / max(1, len(window)))
        return out
