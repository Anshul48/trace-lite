"""End-to-end regressions for no-LLM/no-index behavior."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trace_lite import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.adapters.llm import LLMPreflightError
from trace_lite.cortex import MockVectorStore
from trace_lite.db import QueryBlockedError


def _db(path: Path, llm=None) -> TraceLite:
    return TraceLite(
        path,
        embedder=MockEmbedder(dim=12),
        llm=llm or MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )


def _two_atoms() -> str:
    return (
        "Remote provider authentication must be verified before a staged index is built.\n\n"
        "Pending source captures must remain durable when the provider rejects a request."
    )


def test_provider_preflight_failure_keeps_capture_source_only_and_pending(tmp_path: Path):
    class RejectingProvider:
        def __init__(self):
            self.preflight_calls = 0
            self.completion_calls = 0

        def preflight(self):
            self.preflight_calls += 1
            raise LLMPreflightError("Provider preflight failed (AuthenticationError). Check the model, endpoint, and API key.")

        def complete(self, _prompt, max_tokens=500):
            self.completion_calls += 1
            raise AssertionError("summary/naming must not run after failed provider preflight")

    provider = RejectingProvider()
    db = _db(tmp_path / "auth-failure", llm=provider)
    db.ingest(_two_atoms(), document_name="Pending remote source")

    with pytest.raises(LLMPreflightError, match="AuthenticationError"):
        db.organize()

    assert provider.preflight_calls == 1
    assert provider.completion_calls == 0
    assert db.forest.list_trees() == []
    assert db.forest.get_index_build() is None
    assert db.vector_store.count() == 0
    assert db.forest.has_pending_source_atoms()
    assert db.status().pending_atoms == 2


def test_legacy_fallback_index_is_untrusted_and_cannot_be_force_queried(tmp_path: Path):
    db = _db(tmp_path / "legacy-fallback")
    db.ingest(_two_atoms(), document_name="Legacy source")
    db.reindex_all()
    build_id = db.index_manifest()["build_id"]

    with sqlite3.connect(db.forest.db_path) as connection:
        connection.execute(
            "UPDATE tree_nodes SET summary_provenance = 'fallback' WHERE level > 0"
        )
    db.forest.update_index_build(build_id, fallback_summary_count=1)

    health = db.validate_index()
    assert health["trusted"] is False
    assert health["validation_state"] == "untrusted"
    assert health["fallback_summaries"] == 1
    with pytest.raises(QueryBlockedError, match="Force query requires a last verified active index"):
        db.query("What is pending?", force=True)


def test_successful_derived_nodes_have_only_llm_or_retry_provenance(tmp_path: Path):
    db = _db(tmp_path / "verified")
    db.ingest(_two_atoms(), document_name="Verified source")
    db.reindex_all()

    derived = [
        node
        for tree in db.trees()
        for node in db.forest.get_tree_nodes(tree.tree_id)
        if node.level > 0
    ]
    assert derived
    assert {node.summary_provenance for node in derived} <= {"llm", "retry"}
    assert db.validate_index()["trusted"] is True
