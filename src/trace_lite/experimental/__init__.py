"""R08 experimental predictors (Prediction-Error Atomization + selective repair).

No model downloads, no torch/HF weights (host has no network; HF cache is
empty). Both boundary signals are deterministic CPU heuristics:

- ``surprise``: character trigram surprisal fitted on the document itself.
- ``shift``: local embedding-shift deltas in the pinned R03 hashbow space
  (``filing.engine.text_vector``, 128-d, no learned weights).

Thresholds are per-document adaptive (mean + 0.5*std over the document's
own candidates, max 2 cuts per structural span). Nothing is tuned on any
qrels — DEV runs below are measurements, not selection.
"""

from .segment import refine_cuts, refine_text
from .shift import EmbeddingShiftSignal
from .surprise import NGramSurpriseSignal

__all__ = [
    "EmbeddingShiftSignal",
    "NGramSurpriseSignal",
    "refine_cuts",
    "refine_text",
]
