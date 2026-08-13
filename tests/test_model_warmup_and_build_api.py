"""Coverage for local-model warm-up and serialized UI builds."""

from __future__ import annotations

import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from click.testing import CliRunner

from trace_lite.adapters import MockEmbedder, MockLLMAdapter, SentenceTransformerEmbedder
from trace_lite.cli import main
from trace_lite.db import ConsolidationResult, TraceLite
from trace_lite.cortex import MockVectorStore
from trace_lite.providers import load_config_data, set_auto_load_models


def test_sentence_transformer_warm_up_is_thread_safe(monkeypatch):
    calls: list[str] = []
    encode_calls: list[dict] = []

    class FakeSentenceTransformer:
        def __init__(self, name: str):
            calls.append(name)
            time.sleep(0.02)

        def encode(self, texts, normalize_embeddings=True, show_progress_bar=True):
            encode_calls.append(
                {
                    "normalize_embeddings": normalize_embeddings,
                    "show_progress_bar": show_progress_bar,
                }
            )
            return [[1.0, 0.0] for _ in texts]

        def get_sentence_embedding_dimension(self):
            return 2

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    embedder = SentenceTransformerEmbedder("test-model")
    threads = [threading.Thread(target=embedder.warm_up) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == ["test-model"]
    assert embedder.is_loaded
    embedder.embed(["a local embedding request"])
    assert encode_calls == [
        {"normalize_embeddings": True, "show_progress_bar": False}
    ]


def test_auto_load_setting_defaults_enabled_and_cli_persists(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(config_path))
    assert load_config_data(config_path)["auto_load_models"] is True

    result = CliRunner().invoke(main, ["config", "auto-load", "--off", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["auto_load_models"] is False
    assert load_config_data(config_path)["auto_load_models"] is False

    set_auto_load_models(True, config_path)
    assert json.loads(config_path.read_text(encoding="utf-8"))["auto_load_models"] is True


def test_trace_lite_warm_up_does_not_call_llm(tmp_path: Path):
    class FailingLLM(MockLLMAdapter):
        def complete(self, prompt: str, max_tokens: int = 500) -> str:
            raise AssertionError("model warm-up must not call the LLM")

    db = TraceLite(
        tmp_path / "workspace",
        embedder=MockEmbedder(dim=8),
        llm=FailingLLM(),
        vector_store=MockVectorStore(dim=8),
    )
    result = db.warm_up_models()
    assert result["status"] == "ready"
    assert result["llm"]["contacted"] is False


def test_api_model_toggle_and_concurrent_organize_are_safe(tmp_path: Path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from trace_lite.ui.server import create_app

    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(tmp_path / "config.json"))
    db = TraceLite(
        tmp_path / "workspace",
        embedder=MockEmbedder(dim=8),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(dim=8),
    )
    active = 0
    maximum = 0
    state_lock = threading.Lock()

    def organize(diagnostic_sink=None):
        nonlocal active, maximum
        del diagnostic_sink
        with state_lock:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.04)
        with state_lock:
            active -= 1
        return ConsolidationResult(trees_updated=1, summaries_generated=0)

    db.organize = organize  # type: ignore[method-assign]
    client = TestClient(create_app(db.data_dir, db=db))

    assert client.get("/api/models/status").status_code == 200
    toggled = client.post("/api/config/auto-load", json={"enabled": False})
    assert toggled.status_code == 200
    assert toggled.json()["auto_load_models"] is False

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/organize", json={}), range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert maximum == 1


def test_unexpected_build_failure_is_not_collapsed_to_409(tmp_path: Path):
    from fastapi.testclient import TestClient
    from trace_lite.ui.server import create_app

    db = TraceLite(
        tmp_path / "workspace",
        embedder=MockEmbedder(dim=8),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(dim=8),
    )

    def fail(diagnostic_sink=None):
        del diagnostic_sink
        raise RuntimeError("internal build detail")

    db.organize = fail  # type: ignore[method-assign]
    response = TestClient(create_app(db.data_dir, db=db)).post("/api/organize", json={})
    assert response.status_code == 500
    assert response.json()["error_code"] == "build_failed"
    assert response.json()["stage"] == "build"
    assert "internal build detail" not in response.json()["detail"]
