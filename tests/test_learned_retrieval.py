"""INT-3 learned retrieval: pinned-encoder seam tests (contract TL-QN-2026-09-12.1).

Weights-independent by default (fast, no network): pinning identity, light
import, weights-missing degradation, and fallback-default preservation. The
live-weights test runs only with INT3_LIVE=1 plus a cached snapshot, so the
default suite never pays the torch import or a download.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from trace_lite.router import CascadeRouter, HashBowEncoder
from trace_lite.router.learned import (
    ENCODER_DIM,
    MODEL_ID,
    MODEL_REVISION,
    LearnedBgeEncoder,
    WeightsMissingError,
    pinned_spec,
    served_default,
    set_served_default,
)
from trace_lite.store import Database


def test_pinned_spec_identity() -> None:
    spec = pinned_spec()
    assert spec.dim == ENCODER_DIM == 384
    assert spec.degraded is False
    assert MODEL_ID in spec.weights and MODEL_REVISION in spec.weights
    assert spec.config_hash() == pinned_spec().config_hash()
    # Fallback identity is a different encoder (generations never mix).
    assert spec.config_hash() != HashBowEncoder().config_hash()


def test_learned_import_stays_light() -> None:
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules


def test_weights_missing_raises_named_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    enc = LearnedBgeEncoder()
    with pytest.raises(WeightsMissingError, match="weights-missing"):
        enc.load()


def test_seam_idf_noops_keep_fallback_compatible() -> None:
    enc = LearnedBgeEncoder()
    assert enc.fit_idf(["a", "b"]) == []
    enc.set_idf([1.0] * ENCODER_DIM)  # accepted, ignored
    assert enc.degraded is False
    assert enc.dim == 384


def test_served_default_none_restores_fallback(tmp_path: Path) -> None:
    set_served_default(None)
    try:
        assert served_default() is None
        db = Database(tmp_path / "svc.db")
        try:
            db.bulk_ingest([("d1", "batching improves throughput"),
                            ("d2", "wal mode allows one writer")])
            router = CascadeRouter(db.conn)
            out = router.refresh_served_vector("gen-t")
            assert out["built"] is True
            assert "DEGRADED" in out["encoder"]
            assert router.vector_generation_id == "gen-t"
        finally:
            db.close()
    finally:
        set_served_default(None)


def _snapshot_present() -> bool:
    root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface")))
    return (root / "hub" / ("models--" + MODEL_ID.replace("/", "--"))).exists()


LIVE = os.environ.get("INT3_LIVE") == "1" and _snapshot_present()


@pytest.mark.skipif(not LIVE, reason="needs INT3_LIVE=1 + cached bge snapshot")
def test_live_weights_encode_and_chunk_policy() -> None:
    enc = LearnedBgeEncoder()
    enc.reset_chunk_stats()
    long_text = "retrieval evidence " * 400  # well over the 510-token window
    mat = enc.encode_many(["hello world", long_text])
    assert mat.shape == (2, 384)
    import math

    for row in mat.tolist():
        assert abs(math.sqrt(sum(v * v for v in row)) - 1.0) < 1e-4
    assert enc.chunked_texts >= 1
    assert enc.chunk_windows >= 2
    single = enc.encode_weighted("hello world")
    assert len(single) == 384
