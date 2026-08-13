"""Regression coverage for the LLM trust boundary and retrieval isolation."""

from __future__ import annotations

from types import SimpleNamespace
import sqlite3
import json

import numpy as np
import pytest

from trace_lite import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.adapters.llm import EmptyResponseError, LiteLLMAdapter
from trace_lite.cortex import ForestIndex, MockVectorStore, TreeNode
from trace_lite.engines import ForestRouter, LatticeEngine, RaptorEngine, SummaryGenerationError
from trace_lite.spine import Atom, SourceArtifact, SpineStore


def _children() -> list[TreeNode]:
    return [
        TreeNode(
            "child-a", "tree-a", 0, "leaf", ["atom-a"],
            "Neural networks process vector data.",
        ),
        TreeNode(
            "child-b", "tree-a", 0, "leaf", ["atom-b"],
            "SQLite stores relational data in a single file.",
        ),
    ]


class _SequenceLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt, max_tokens=500):
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else ""


def test_provider_visible_content_wins_and_reasoning_only_is_preflight_only(monkeypatch):
    def visible(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="visible answer", reasoning_content="hidden chain"
                    )
                )
            ]
        )

    adapter = LiteLLMAdapter("openai/test")
    monkeypatch.setattr(
        adapter,
        "_load_litellm",
        lambda: SimpleNamespace(
            completion=visible,
            get_llm_provider=lambda **_kwargs: ("test", "openai", None, None),
        ),
    )
    assert adapter.complete("question") == "visible answer"

    def reasoning_only(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, reasoning_content="OK")
                )
            ]
        )

    monkeypatch.setattr(
        adapter,
        "_load_litellm",
        lambda: SimpleNamespace(
            completion=reasoning_only,
            get_llm_provider=lambda **_kwargs: ("test", "openai", None, None),
        ),
    )
    with pytest.raises(EmptyResponseError):
        adapter.complete("question")
    adapter.preflight()


def test_summary_retries_to_attempt_four_and_does_not_replay_failed_output():
    llm = _SequenceLLM([
        "<think>hidden</think> unrelated",
        "Return only the summary instruction.",
        "Answer: unrelated weather report.",
        "Neural networks process vector data and SQLite stores relational data.",
    ])
    engine = RaptorEngine(
        llm=llm,
        embedder=MockEmbedder(dim=4),
        vector_store=MockVectorStore(),
        forest=ForestIndex(":memory:"),
        summary_min_length=20,
        summary_retry_count=3,
    )
    events: list[dict] = []

    result = engine.generate_summary(_children())

    assert result.attempts == 4
    assert result.provenance == "retry"
    assert len(llm.prompts) == 4
    assert "<think>hidden</think> unrelated" not in llm.prompts[1]


def test_summary_final_failure_emits_safe_diagnostics():
    llm = _SequenceLLM([
        "<think>hidden</think>",
        "Return only the summary.",
        "Answer: weather report.",
        "A generic response unrelated to the passages.",
    ])
    engine = RaptorEngine(
        llm=llm,
        embedder=MockEmbedder(dim=4),
        vector_store=MockVectorStore(),
        forest=ForestIndex(":memory:"),
        summary_min_length=20,
        summary_retry_count=3,
    )
    events: list[dict] = []
    engine.diagnostic_sink = events.append

    with pytest.raises(SummaryGenerationError, match="failed after 4 attempt"):
        engine.generate_summary(_children())

    assert [event["status"] for event in events[:-1]] == [
        "retrying", "retrying", "retrying"
    ]
    assert events[-1]["status"] == "final_failure"
    assert events[-1]["attempt"] == 4
    assert events[-1]["failure_code"] == "missing_source_terms"
    assert "generic response unrelated" not in events[-1]["message"]


def test_title_validation_retries_and_accepts_ampersand_label(tmp_path):
    llm = _SequenceLLM(["AI", "AI & DB Notes"])
    router = ForestRouter(
        embedder=MockEmbedder(dim=4),
        llm=llm,
        forest=ForestIndex(tmp_path / "cortex.sqlite3"),
        vector_store=MockVectorStore(),
    )
    artifact = SourceArtifact.create("art-1", "Neural and database notes")
    atoms = [Atom.create("atom-1", "Neural networks and databases.", "art-1", 0, 0, 35)]

    plan = router.route_plan(atoms, existing_trees=[])

    assert plan[0][1] == "AI & DB Notes"
    assert len(llm.prompts) == 2


def test_invalid_stored_summary_makes_active_index_untrusted(tmp_path):
    db = TraceLite(
        tmp_path / "workspace",
        embedder=MockEmbedder(dim=8),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )
    db.ingest(
        "Neural networks process vector data and transform learned representations for retrieval.\n\n"
        "SQLite stores relational data in a single file and supports durable local queries.",
        document_name="source",
    )
    db.reindex_all()
    build_id = db.index_manifest()["build_id"]
    with sqlite3.connect(db.forest.db_path) as connection:
        connection.execute(
            "UPDATE tree_nodes SET summary_text = ? WHERE level > 0",
            ("<think>hidden reasoning</think>",),
        )
    db.forest.update_index_build(build_id, valid_summary_count=1)

    health = db.validate_index()

    assert health["trusted"] is False
    assert health["quality_verified"] is False
    assert any("reasoning_output" in error for error in health["errors"])


def test_failed_summary_build_leaves_previous_active_index_untouched(tmp_path):
    db = TraceLite(
        tmp_path / "workspace",
        embedder=MockEmbedder(dim=8),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )
    db.ingest(
        "Neural networks process vector data and transform learned representations for retrieval.\n\n"
        "SQLite stores relational data in a single file and supports durable local queries.",
        document_name="source",
    )
    db.reindex_all()
    old_build = db.index_manifest()["build_id"]
    old_nodes = {
        node.node_id
        for tree in db.trees()
        for node in db.forest.get_tree_nodes(tree.tree_id)
    }
    failing = _SequenceLLM(["unrelated output"] * 4)
    db.llm = failing
    db.raptor.llm = failing
    db.router.llm = failing

    with pytest.raises(SummaryGenerationError, match="activation was prevented"):
        db.reindex_all()

    assert db.index_manifest()["build_id"] == old_build
    assert {
        node.node_id
        for tree in db.trees()
        for node in db.forest.get_tree_nodes(tree.tree_id)
    } == old_nodes


def test_flat_search_filters_internal_summary_vectors(tmp_path):
    class RecordingStore(MockVectorStore):
        def __init__(self):
            super().__init__(dim=2)
            self.filters = []

        def search(self, query_embedding, top_k=10, filter_expr=None):
            self.filters.append(filter_expr)
            return super().search(query_embedding, top_k, filter_expr)

    class FixedEmbedder:
        def embed(self, texts):
            return np.asarray([[1.0, 0.0] for _ in texts], dtype=np.float32)

    spine = SpineStore(tmp_path / "spine.sqlite3")
    artifact = SourceArtifact.create("art-1", "A source note")
    atom = Atom.create("atom-1", "Neural networks are source evidence.", "art-1", 0, 0, 36)
    spine.store_artifact(artifact)
    spine.store_atoms_batch([atom])
    forest = ForestIndex(tmp_path / "cortex.sqlite3")
    store = RecordingStore()
    store.upsert("leaf-1", np.asarray([1.0, 0.0]), {
        "node_type": "leaf", "atom_ids": [atom.atom_id], "tree_id": "tree-1"
    })
    store.upsert("summary-1", np.asarray([1.0, 0.0]), {
        "node_type": "cluster_summary", "atom_ids": [atom.atom_id], "tree_id": "tree-1"
    })
    engine = LatticeEngine(
        llm=MockLLMAdapter(),
        embedder=FixedEmbedder(),
        vector_store=store,
        forest=forest,
        spine=spine,
    )

    result = engine.query("neural networks", top_k=1, mode="flat")

    assert store.filters == ["node_type = 'leaf'"]
    assert [item.atom.atom_id for item in result.items] == [atom.atom_id]


def test_cli_json_keeps_progress_diagnostics_on_stderr(monkeypatch):
    from click.testing import CliRunner
    import trace_lite.cli as cli
    from trace_lite.db import ConsolidationResult

    class FakeDB:
        def __init__(self, *_args, **_kwargs):
            pass

        def organize(self, diagnostic_sink=None):
            diagnostic_sink({
                "stage": "summary_generation",
                "stage_label": "summary generation",
                "status": "retrying",
                "attempt": 1,
                "max_attempts": 4,
                "failure_code": "prompt_echo",
                "message": "retry",
            })
            return ConsolidationResult(trees_updated=0, summaries_generated=0)

    monkeypatch.setattr(cli, "TraceLite", FakeDB)
    result = CliRunner().invoke(cli.main, ["--data-dir", "workspace", "organize", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "ok"
    assert "summary generation retrying" in result.stderr
