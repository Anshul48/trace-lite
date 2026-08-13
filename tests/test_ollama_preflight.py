"""Regression coverage for Ollama model availability checks."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import types
from urllib.error import URLError

import pytest

from trace_lite import TraceLite
from trace_lite.adapters import LiteLLMAdapter, MockEmbedder, MockLLMAdapter
from trace_lite.adapters.llm import (
    OllamaModelNotFoundError,
    OllamaServiceUnavailableError,
)
from trace_lite.cortex import MockVectorStore


class _TagsResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_TagsResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _tags_urlopen(payload: dict, calls: list[tuple[str, float]]):
    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return _TagsResponse(payload)

    return fake_urlopen


def _text() -> str:
    return (
        "RAPTOR builds a durable hierarchy whose summary nodes preserve source facts for retrieval.\n\n"
        "LATTICE uses those hierarchy nodes together with vectors to retrieve supporting evidence."
    )


def _db(path: Path) -> TraceLite:
    return TraceLite(
        path,
        embedder=MockEmbedder(dim=16),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )


def test_ollama_preflight_accepts_a_configured_installed_tag(monkeypatch):
    import trace_lite.adapters.llm as llm_module

    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(
        llm_module,
        "urlopen",
        _tags_urlopen({"models": [{"name": "holo3.1-64k:latest"}]}, calls),
    )

    LiteLLMAdapter(
        "ollama/holo3.1-64k:latest", api_base="http://ollama.test"
    ).preflight_ollama()

    assert calls == [("http://ollama.test/api/tags", 2.0)]


def test_ollama_preflight_rejects_a_missing_configured_tag(monkeypatch):
    import trace_lite.adapters.llm as llm_module

    monkeypatch.setattr(
        llm_module,
        "urlopen",
        _tags_urlopen({"models": [{"name": "installed:latest"}]}, []),
    )

    with pytest.raises(OllamaModelNotFoundError, match="ollama pull missing:latest"):
        LiteLLMAdapter(
            "ollama/missing:latest", api_base="http://ollama.test"
        ).preflight_ollama()


def test_ollama_preflight_reports_an_unreachable_server(monkeypatch):
    import trace_lite.adapters.llm as llm_module

    def unavailable(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(llm_module, "urlopen", unavailable)

    with pytest.raises(OllamaServiceUnavailableError, match="Ollama is unavailable"):
        LiteLLMAdapter(
            "ollama/installed:latest", api_base="http://ollama.test"
        ).preflight_ollama()


def test_generic_preflight_reuses_ollama_preflight(monkeypatch):
    import trace_lite.adapters.llm as llm_module

    monkeypatch.setattr(
        llm_module,
        "urlopen",
        _tags_urlopen({"models": []}, []),
    )

    with pytest.raises(OllamaModelNotFoundError, match="ollama pull missing:latest"):
        LiteLLMAdapter("ollama/missing:latest", api_base="http://ollama.test").preflight()


def test_litellm_debug_is_disabled_and_diagnostics_are_redacted(monkeypatch):
    debug_enabled = []

    def turn_on_debug():
        debug_enabled.append(True)

    def completion(**_kwargs):
        logging.getLogger("LiteLLM").warning("provider request failed api_key=sk-secret-value")
        raise RuntimeError("provider exploded")

    dummy_litellm = types.ModuleType("litellm")
    dummy_litellm._turn_on_debug = turn_on_debug
    dummy_litellm.completion = completion
    monkeypatch.setitem(sys.modules, "litellm", dummy_litellm)
    monkeypatch.delenv("LITELLM_LOG", raising=False)

    adapter = LiteLLMAdapter("ollama/installed:latest")
    with pytest.raises(RuntimeError, match="provider exploded"):
        adapter.complete("hello")

    assert debug_enabled == []
    snapshot = adapter.debug_snapshot()
    assert "provider exploded" in snapshot
    assert "provider request failed" in snapshot
    assert "sk-secret-value" not in snapshot
    assert "[REDACTED]" in snapshot


def test_missing_ollama_model_aborts_before_build_and_preserves_active_index(monkeypatch, tmp_path):
    import trace_lite.adapters.llm as llm_module

    db = _db(tmp_path / "workspace")
    db.ingest(_text(), document_name="Initial")
    db.reindex_all()
    old_build = db.index_manifest()
    old_nodes = {
        node.node_id
        for tree in db.trees()
        for node in db.forest.get_tree_nodes(tree.tree_id)
    }
    old_vectors = set(db.vector_store.nodes)

    db.ingest(
        "A subsequent durable atom remains queued until the configured summary model is available.",
        document_name="Pending",
    )
    assert db.forest.has_pending_source_atoms()
    pending_before = db.status().pending_atoms

    unavailable = LiteLLMAdapter("ollama/missing:latest", api_base="http://ollama.test")
    db.llm = unavailable
    db.raptor.llm = unavailable
    db.router.llm = unavailable
    monkeypatch.setattr(
        llm_module,
        "urlopen",
        _tags_urlopen({"models": [{"name": "installed:latest"}]}, []),
    )

    with pytest.raises(OllamaModelNotFoundError):
        db.organize()

    assert db.status().pending_atoms == pending_before
    assert db.forest.has_pending_source_atoms()
    assert db.index_manifest() == old_build
    assert db.forest.get_latest_index_build() == old_build
    assert {
        node.node_id
        for tree in db.trees()
        for node in db.forest.get_tree_nodes(tree.tree_id)
    } == old_nodes
    assert set(db.vector_store.nodes) == old_vectors


def test_api_reports_ollama_configuration_error_and_force_query(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    import trace_lite.adapters.llm as llm_module
    from trace_lite.ui.server import create_app

    db = _db(tmp_path / "workspace")
    db.ingest(_text(), document_name="Initial")
    db.reindex_all()
    db.ingest(
        "This queued source keeps automatic organization pending until Ollama is configured.",
        document_name="Pending",
    )
    unavailable = LiteLLMAdapter("ollama/missing:latest", api_base="http://ollama.test")
    db.llm = unavailable
    db.raptor.llm = unavailable
    db.router.llm = unavailable
    monkeypatch.setattr(
        llm_module,
        "urlopen",
        _tags_urlopen({"models": []}, []),
    )
    client = TestClient(create_app(db.data_dir, db=db))

    organize = client.post("/api/organize", json={})
    query = client.post("/api/query", json={"query_text": "How does retrieval work?"})

    assert organize.status_code == 422
    assert "Ollama configuration error" in organize.json()["detail"]
    assert "ollama pull missing:latest" in organize.json()["detail"]
    assert query.status_code == 409
    assert "pending organization" in query.json()["detail"]

    forced = client.post("/api/query", json={"query_text": "How does retrieval work?", "force": True})
    assert forced.status_code == 200
    assert forced.json()["mode"] == "flat"
    assert forced.json()["warnings"]

    def unavailable(*_args, **_kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(llm_module, "urlopen", unavailable)
    service_failure = client.post("/api/organize", json={})

    assert service_failure.status_code == 503
    assert "Ollama service unavailable" in service_failure.json()["detail"]
