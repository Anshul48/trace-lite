"""R03 encoder wrapper: pinned sparse-projection encoder (packet fallback).

DEGRADED — no learned weights. Host has no network (B1 probe:
``Network is unreachable``), ``usearch``/``onnxruntime`` are not installed and
the HuggingFace cache is empty, so no transformer weights can be fetched.
This module therefore pins the exact sparse space the frozen BEIR baselines
were measured in and labels it honestly instead of claiming learned quality.

Pinned configuration (part of the encoder identity / config hash):
  tokenizer:      ``filing`` word regex ``[a-z0-9_]+`` (casefolded);
                  all-digit tokens excluded from vectors (serial/year guard)
  weights:        none — deterministic sha256(token) mod 128 hashed counts
  pooling:        count-sum over vector tokens
  normalization:  L2 unit norm, then corpus IDF ``log((N+1)/(df+1))+1``
                  reweight + L2 renormalize (same as FlatHybrid.warm)
  dim:            128

A future learned encoder plugs in beside this one by implementing the same
``encode``/``config_hash`` surface and flipping ``degraded`` to False; the
index manifest records which encoder built it so generations never mix.
"""

from __future__ import annotations

import hashlib
import math

from ..filing.engine import CENTROID_DIM, text_vector, tokenize, vector_tokens

ENCODER_NAME = "hashbow-128-idf"
ENCODER_DIM = CENTROID_DIM
ENCODER_TOKENIZER = "filing-word-regex[a-z0-9_]+/lowercase/digit-excluded"
ENCODER_WEIGHTS = "none-hashed-sha256-mod-128(degraded)"
ENCODER_RUNTIME = "numpy-cpu"
ENCODER_POOLING = "count-sum"
ENCODER_NORMALIZATION = "L2-then-idf-log((N+1)/(df+1))+1-then-L2"


class EncoderSpec:
    """Pinned encoder identity. ``config_hash`` binds index generations."""

    def __init__(
        self,
        name: str = ENCODER_NAME,
        dim: int = ENCODER_DIM,
        tokenizer: str = ENCODER_TOKENIZER,
        weights: str = ENCODER_WEIGHTS,
        runtime: str = ENCODER_RUNTIME,
        pooling: str = ENCODER_POOLING,
        normalization: str = ENCODER_NORMALIZATION,
        degraded: bool = True,
    ) -> None:
        self.name = name
        self.dim = dim
        self.tokenizer = tokenizer
        self.weights = weights
        self.runtime = runtime
        self.pooling = pooling
        self.normalization = normalization
        self.degraded = degraded

    def canonical(self) -> str:
        return "|".join(
            [
                self.name,
                str(self.dim),
                self.tokenizer,
                self.weights,
                self.runtime,
                self.pooling,
                self.normalization,
            ]
        )

    def config_hash(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()[:32]

    def label(self) -> str:
        state = "DEGRADED-no-learned-weights" if self.degraded else "learned"
        return f"{self.name}/{self.dim}d/{state}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dim": self.dim,
            "tokenizer": self.tokenizer,
            "weights": self.weights,
            "runtime": self.runtime,
            "pooling": self.pooling,
            "normalization": self.normalization,
            "degraded": self.degraded,
            "config_hash": self.config_hash(),
            "label": self.label(),
        }


class HashBowEncoder:
    """Exact wrapper around the pinned sparse projection (+ IDF weighting)."""

    def __init__(self, spec: EncoderSpec | None = None) -> None:
        self.spec = spec or EncoderSpec()
        self._idf: list[float] | None = None
        self._doc_count: int = 0

    @property
    def degraded(self) -> bool:
        return self.spec.degraded

    @property
    def dim(self) -> int:
        return self.spec.dim

    def config_hash(self) -> str:
        return self.spec.config_hash()

    def tokenize(self, text: str) -> list[str]:
        return tokenize(text)

    def vector_tokens(self, text: str) -> list[str]:
        return vector_tokens(text)

    def encode(self, text: str) -> list[float]:
        """Unweighted L2-normalized hashed vector (IDF-free base form)."""
        return text_vector(text, self.spec.dim)

    def fit_idf(self, texts: list[str]) -> list[float]:
        """Estimate IDF over an indexed corpus (same formula as FlatHybrid)."""
        total = len(texts)
        df = [0] * self.spec.dim
        for text in texts:
            for d, v in enumerate(text_vector(text, self.spec.dim)):
                if v:
                    df[d] += 1
        self._idf = [math.log((total + 1) / (d + 1)) + 1.0 for d in df]
        self._doc_count = total
        return self._idf

    def set_idf(self, idf: list[float], doc_count: int = 0) -> None:
        if len(idf) != self.spec.dim:
            raise ValueError(f"IDF dim {len(idf)} != encoder dim {self.spec.dim}")
        self._idf = list(idf)
        self._doc_count = doc_count

    def encode_weighted(self, text: str) -> list[float]:
        """IDF-weighted L2-normalized vector — the indexed/searchable form."""
        vec = text_vector(text, self.spec.dim)
        if self._idf is not None:
            vec = [v * w for v, w in zip(vec, self._idf)]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
        return vec
