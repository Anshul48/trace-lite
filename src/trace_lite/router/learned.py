"""INT-3 learned encoder: pinned BAAI/bge-small-en-v1.5 behind the INT-2 seam.

Spike (contract TL-QN-2026-09-12.1): prove or disprove learned retrieval on
the SERVED path. Either outcome is acceptable if measured.

Pinned configuration (part of the encoder identity / config hash):
  weights:        HF ``BAAI/bge-small-en-v1.5`` @ revision
                  ``5c38ec7c405ec4b44b94cc5a9bb96e735b38267a``
  tokenizer:      bert-base-uncased WordPiece (model-pinned ``vocab.txt``),
                  lowercase; 512-token model limit
  prompts:        none on either side (symmetric passage encoding for docs
                  and queries; no ``Represent this sentence`` prefix)
  pooling:        CLS token (model-pinned ``1_Pooling`` config:
                  ``pooling_mode_cls_token=true``)
  normalization:  L2 unit norm (``normalize_embeddings=True``)
  dim:            384

Long-span policy (never silent truncation): texts longer than 510 content
tokens are split into overlapping WordPiece windows (510 tokens, stride 458,
i.e. 52-token overlap, decoded back to text), each window is encoded, and
the window vectors are mean-pooled + L2-renormalized. The build records how
many texts were chunked (``chunked_texts``/``chunk_windows``); queries take
the same path, so docs and queries share one code path.

Interface: implements the INT-2 encoder seam (``fit_idf`` /
``encode_weighted`` / ``config_hash`` / ``spec`` / ``dim`` / ``degraded``)
plus ``encode_many`` for bounded-batch corpus builds. ``fit_idf``/``set_idf``
are seam-compatible no-ops (a learned space has no IDF) that record the
corpus size. ``degraded`` is False (real weights).

The module imports light: ``torch``/``sentence_transformers`` load lazily
inside :meth:`LearnedBgeEncoder.load` so the library and test suite never
pay the import cost unless weights are actually used. When weights are
unavailable the factory raises a named ``WeightsMissingError`` and callers
keep the hashbow fallback (see ``served_default``).
"""

from __future__ import annotations

import os
from pathlib import Path

from .encoder import EncoderSpec

MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
ENCODER_NAME = "bge-small-en-v1.5"
ENCODER_DIM = 384
ENCODER_TOKENIZER = "bert-base-uncased-wordpiece/lowercase/512-token-limit"
ENCODER_PROMPTS = "none-symmetric(no query prefix, no doc prefix)"
ENCODER_RUNTIME = "sentence_transformers-5.7/torch-cpu"
ENCODER_POOLING = "cls-token(model-pinned 1_Pooling)"
ENCODER_NORMALIZATION = "L2-unit-norm(normalize_embeddings=True)"
#: Long-span policy, pinned: window/stride in WordPiece content tokens.
CHUNK_WINDOW = 510
CHUNK_STRIDE = 458
ENCODER_CHUNKING = (
    f"overlap-windows(window={CHUNK_WINDOW},stride={CHUNK_STRIDE},"
    "overlap=52,mean-pool+renorm;never-truncate)"
)
ENCODER_WEIGHTS = f"hf:{MODEL_ID}@{MODEL_REVISION}"

#: Default batch size for corpus builds (bounded batches, CPU-only).
ENCODE_BATCH_SIZE = 32


class WeightsMissingError(RuntimeError):
    """Raised when learned weights cannot be loaded (no snapshot, no network).

    Callers treat this as "stay on the hashbow fallback", never as a silent
    zero-vector substitution.
    """


def default_snapshot_dir() -> Path:
    """Writable HF cache root (the image default ``~/.cache`` is read-only)."""
    root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    return root / "hub"


def pinned_spec() -> EncoderSpec:
    """Pinned identity for the INT-3 spike encoder (``degraded=False``)."""
    return EncoderSpec(
        name=ENCODER_NAME,
        dim=ENCODER_DIM,
        tokenizer=f"{ENCODER_TOKENIZER}|prompts:{ENCODER_PROMPTS}",
        weights=ENCODER_WEIGHTS,
        runtime=ENCODER_RUNTIME,
        pooling=f"{ENCODER_POOLING}|chunking:{ENCODER_CHUNKING}",
        normalization=ENCODER_NORMALIZATION,
        degraded=False,
    )


class LearnedBgeEncoder:
    """Pinned bge-small-en-v1.5 encoder behind the INT-2 seam."""

    def __init__(self, spec: EncoderSpec | None = None,
                 snapshot_hint: str | Path | None = None) -> None:
        self.spec = spec or pinned_spec()
        self._model = None
        self._snapshot_hint = str(snapshot_hint) if snapshot_hint else None
        self._doc_count = 0
        #: Cumulative long-span stats (reset by :meth:`reset_chunk_stats`).
        self.chunked_texts = 0
        self.chunk_windows = 0

    # -- identity ---------------------------------------------------
    @property
    def degraded(self) -> bool:
        return self.spec.degraded

    @property
    def dim(self) -> int:
        return self.spec.dim

    def config_hash(self) -> str:
        return self.spec.config_hash()

    def reset_chunk_stats(self) -> None:
        self.chunked_texts = 0
        self.chunk_windows = 0

    # -- weights ----------------------------------------------------
    def load(self):
        """Load (and cache) the pinned weights. Raises WeightsMissingError."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise WeightsMissingError(
                "weights-missing: sentence_transformers is not installed; "
                "fallback stays active"
            ) from exc
        # Cache location is governed by the HF_HOME environment (the spike
        # harness sets HF_HOME=/tmp/int3_hf); no explicit cache_folder so the
        # pinned snapshot is reused instead of re-downloaded elsewhere.
        cache = str(default_snapshot_dir().parent)
        try:
            self._model = SentenceTransformer(MODEL_ID, device="cpu")
        except Exception as exc:
            raise WeightsMissingError(
                f"weights-missing: cannot load {MODEL_ID}@{MODEL_REVISION} "
                f"(cache {cache}): {type(exc).__name__}: {exc}; "
                "fallback stays active"
            ) from exc
        got = int(self._model.get_sentence_embedding_dimension())
        if got != self.spec.dim:
            raise WeightsMissingError(
                f"weights-missing: dim mismatch (snapshot {got}d != "
                f"pinned {self.spec.dim}d); refusing to serve"
            )
        return self._model

    # -- seam --------------------------------------------------------
    def fit_idf(self, texts: list[str]) -> list[float]:
        """Seam-compatible no-op: a learned space has no IDF weights."""
        self._doc_count = len(texts)
        return []

    def set_idf(self, idf: list[float], doc_count: int = 0) -> None:
        """Seam-compatible no-op (accepted so generations can replay it)."""

    def encode(self, text: str) -> list[float]:
        return self.encode_weighted(text)

    def encode_weighted(self, text: str) -> list[float]:
        import numpy as _np

        return [float(v) for v in self.encode_many([text])[0].tolist()]

    def encode_many(self, texts: list[str], batch_size: int = ENCODE_BATCH_SIZE):
        """Bounded-batch encoding; long spans take the pinned chunk path."""
        import numpy as _np

        model = self.load()
        plans = self._plan_windows(model, texts)
        shorts: list[str] = []
        order: list = []
        for text, windows in zip(texts, plans):
            if len(windows) > 1:
                self.chunked_texts += 1
                self.chunk_windows += len(windows)
            if len(windows) == 1 and windows[0] == text:
                order.append(("s", len(shorts)))
                shorts.append(text)
            else:
                order.append(("c", windows))
        mat = _np.zeros((len(texts), self.spec.dim), dtype=_np.float32)
        if shorts:
            vecs = model.encode(
                shorts, batch_size=batch_size, normalize_embeddings=True,
                show_progress_bar=False, convert_to_numpy=True,
            )
            vecs = _np.asarray(vecs, dtype=_np.float32)
            for row, (_, pos) in [(i, o) for i, o in enumerate(order) if o[0] == "s"]:
                mat[row] = vecs[pos]
        for row, kind in enumerate(order):
            if kind[0] == "c":
                vecs = model.encode(
                    kind[1], batch_size=batch_size,
                    normalize_embeddings=True, show_progress_bar=False,
                    convert_to_numpy=True,
                )
                mean = _np.asarray(vecs, dtype=_np.float32).mean(axis=0)
                nrm = float(_np.linalg.norm(mean)) or 1.0
                mat[row] = (mean / nrm).astype(_np.float32)
        return mat

    def _plan_windows(self, model, texts: list[str]) -> list[list[str]]:
        """One batched length check; only over-limit texts are windowed.

        A single tokenizer pass (no per-text ``SentenceTransformer.tokenize``
        overhead, which stalls on thousand-doc corpora). Texts the tokenizer
        cannot process stay singletons (encoded as-is, never dropped).
        """
        toks = model.tokenizer
        try:
            enc = toks(list(texts), add_special_tokens=False,
                       truncation=False, verbose=False)
            seqs = enc["input_ids"]
        except Exception:
            return [[t] for t in texts]
        plans = []
        for text, seq in zip(texts, seqs):
            if len(seq) <= CHUNK_WINDOW:
                plans.append([text])
                continue
            out = []
            for start in range(0, len(seq), CHUNK_STRIDE):
                piece = seq[start:start + CHUNK_WINDOW]
                # Decode->re-encode is not length-idempotent (roundtrip can
                # inflate past the window); verify and halve until every
                # chunk re-tokenizes within the limit, so the model never
                # truncates a span silently.
                chunk = toks.decode(piece, skip_special_tokens=True)
                probe = toks([chunk], add_special_tokens=False,
                             truncation=False,
                             verbose=False)["input_ids"][0]
                while len(probe) > CHUNK_WINDOW and len(piece) > 1:
                    piece = piece[: (len(piece) + 1) // 2]
                    chunk = toks.decode(piece, skip_special_tokens=True)
                    probe = toks([chunk], add_special_tokens=False,
                                 truncation=False,
                                 verbose=False)["input_ids"][0]
                out.append(chunk)
                if start + CHUNK_WINDOW >= len(seq):
                    break
            plans.append(out)
        return plans


# -- served default (promotion flip point; None = hashbow fallback) ----
_SERVED_DEFAULT: LearnedBgeEncoder | None = None


def set_served_default(enc: LearnedBgeEncoder | None) -> None:
    """Select the served encoder. None restores the hashbow fallback."""
    global _SERVED_DEFAULT
    _SERVED_DEFAULT = enc


def served_default() -> LearnedBgeEncoder | None:
    """Encoder the served path uses when no explicit encoder is passed."""
    return _SERVED_DEFAULT
