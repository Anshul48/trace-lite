"""Tests for trace-lite visualizer serializers, terminal rendering, and web server."""

import pytest
from pathlib import Path
from trace_lite.db import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.visualizer import (
    serialize_status,
    serialize_forest,
    serialize_vectors,
    serialize_query_result,
    serialize_spine,
    render_terminal_visualizer,
)


@pytest.fixture
def populated_db(tmp_path):
    """Fixture providing a populated TraceLite instance with mock LLM/Embedder for fast offline testing."""
    embedder = MockEmbedder(dim=384)
    llm = MockLLMAdapter()
    db = TraceLite(tmp_path / "test_vis_data", embedder=embedder, llm=llm)
    db.ingest(
        "SQLite is a lightweight ACID database engine. It stores data in a single file on disk.",
        document_name="SQLite Doc",
    )
    db.ingest(
        "LanceDB is a developer-friendly vector database built on top of the Lance file format.",
        document_name="LanceDB Doc",
    )
    db.consolidate()
    return db


def test_serializers(populated_db):
    # Status
    status_data = serialize_status(populated_db)
    assert status_data["total_atoms"] > 0
    assert status_data["total_trees"] >= 0

    # Forest
    forest_data = serialize_forest(populated_db)
    assert "trees" in forest_data

    # Vectors
    vector_data = serialize_vectors(populated_db)
    assert "points" in vector_data

    # Spine
    spine_data = serialize_spine(populated_db)
    assert spine_data["artifacts_count"] == 2
    assert len(spine_data["artifacts"]) == 2

    # Query result
    qres = populated_db.query("vector database", top_k=2)
    qres_serialized = serialize_query_result(qres)
    assert qres_serialized["results_count"] == len(qres.items)


def test_terminal_visualizer(populated_db, capsys):
    render_terminal_visualizer(populated_db)
    captured = capsys.readouterr()
    assert "trace-lite" in captured.out or "RAPTOR" in captured.out or len(captured.out) > 0


def test_web_app_creation(populated_db):
    try:
        from trace_lite.ui.server import create_app
        app = create_app(populated_db.data_dir)
        assert app is not None
        assert app.title == "trace-lite Visualizer"
    except ImportError:
        pytest.skip("FastAPI / uvicorn not installed in test environment")
