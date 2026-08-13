"""Focused regression tests for versioned RAPTOR/index hardening."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from trace_lite import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.cortex import LanceDBStore, MockVectorStore, TreeNode
from trace_lite.engines import RaptorEngine, SummaryGenerationError, normalize_markdown_for_summary
from trace_lite import providers
from trace_lite.providers import save_provider_key


def _db(path: Path, *, vector_store=None, llm=None, config=None) -> TraceLite:
    return TraceLite(
        path,
        config=config,
        embedder=MockEmbedder(dim=16),
        llm=llm or MockLLMAdapter(),
        vector_store=vector_store or MockVectorStore(),
    )


def test_full_reindex_creates_manifest_and_exact_parity(tmp_path: Path):
    db = _db(tmp_path / "workspace")
    db.ingest(
        "First source paragraph describes durable RAPTOR summaries and bounded semantic groups.\n\n"
        "Second source paragraph describes LATTICE traversal and source evidence retrieval.",
        document_name="hardening",
    )

    result = db.reindex_all()
    health = db.validate_index()

    assert result.state == "active"
    assert health["healthy"] is True
    assert health["indexed_atoms"] == db.status().total_atoms
    assert health["vector_count"] == health["node_count"]
    assert db.index_manifest()["state"] == "active"
    assert db.index_manifest()["vector_count"] == health["vector_count"]


def test_failed_vector_activation_keeps_previous_index(tmp_path: Path):
    class ToggleStore(MockVectorStore):
        fail = False

        def upsert_batch(self, items):
            if self.fail:
                raise RuntimeError("injected vector failure")
            return super().upsert_batch(items)

    store = ToggleStore()
    db = _db(tmp_path / "workspace", vector_store=store)
    db.ingest(
        "First source paragraph describes durable RAPTOR summaries and bounded semantic groups.\n\n"
        "Second source paragraph describes LATTICE traversal and source evidence retrieval.",
        document_name="hardening",
    )
    db.reindex_all()
    old_nodes = {node.node_id for tree in db.trees() for node in db.forest.get_tree_nodes(tree.tree_id)}
    old_build = db.index_manifest()["build_id"]
    store.fail = True

    with pytest.raises(RuntimeError, match="injected vector failure"):
        db.reindex_all()

    assert {node.node_id for tree in db.trees() for node in db.forest.get_tree_nodes(tree.tree_id)} == old_nodes
    assert db.index_manifest()["build_id"] == old_build
    assert db.validate_index()["healthy"] is True


def test_failed_first_build_surfaces_its_actual_recovery_diagnostic(tmp_path: Path):
    db = _db(tmp_path / "workspace")
    db.ingest("A durable source atom that remains in Spine when a derived build fails.")
    db.forest.create_index_build({
        "build_id": "build-failed-diagnostic",
        "source_atom_count": 1,
        "embedding_model": "mock",
        "embedding_dimension": 16,
        "config_fingerprint": "test",
    })
    db.forest.update_index_build(
        "build-failed-diagnostic", state="failed", error="injected clustering failure"
    )

    health = db.validate_index()

    assert health["healthy"] is False
    assert health["last_failed_build"]["build_id"] == "build-failed-diagnostic"
    assert "injected clustering failure" in health["errors"][0]
    assert "Spine atoms and pending assignments were preserved" in health["errors"][0]
    assert health["checks"]["one_root_per_tree"] is True
    assert health["active_vector_collection"] is None


def test_summary_guard_retries_then_fails_closed_and_normalizes_markdown(tmp_path: Path):
    class SequenceLLM:
        def __init__(self, responses):
            self.responses = list(responses)

        def complete(self, prompt, max_tokens=500):
            return self.responses.pop(0) if self.responses else ""

    db = _db(tmp_path / "workspace")
    engine = RaptorEngine(
        llm=SequenceLLM(["bad", "A repaired factual summary covers every child passage."]),
        embedder=MockEmbedder(dim=16),
        vector_store=MockVectorStore(),
        forest=db.forest,
        summary_min_length=20,
        summary_retry_count=1,
    )
    children = [
        TreeNode("child-a", "tree-a", 0, "leaf", ["atom-a"], "Heading A contains durable facts."),
        TreeNode("child-b", "tree-a", 0, "leaf", ["atom-b"], "Heading B contains related facts."),
    ]
    retried = engine.generate_summary(children)
    failed_engine = RaptorEngine(
        llm=SequenceLLM(["", ""]),
        embedder=MockEmbedder(dim=16),
        vector_store=MockVectorStore(),
        forest=db.forest,
        summary_min_length=20,
        summary_retry_count=1,
    )
    with pytest.raises(SummaryGenerationError, match="failed after 2 attempt"):
        failed_engine.generate_summary(children)
    with pytest.raises(SummaryGenerationError):
        failed_engine.generate_summary([children[0]])

    assert retried.provenance == "retry"
    assert "# Heading" in normalize_markdown_for_summary("# Heading\n- **Heading** [facts](https://example.test)")
    assert "facts" in normalize_markdown_for_summary("# Heading\n- **Heading** [facts](https://example.test)")


def test_noise_assignment_recomputes_centroids_after_new_bucket():
    class NoiseClustering:
        def cluster(self, embeddings):
            return [[0, 1]], [2, 3, 4]

        def split_oversized(self, indices, embeddings=None):
            return [indices]

        @staticmethod
        def cosine_similarity(left, right):
            left_norm = np.linalg.norm(left) + 1e-9
            right_norm = np.linalg.norm(right) + 1e-9
            return float(np.dot(left, right) / (left_norm * right_norm))

    engine = RaptorEngine(
        llm=MockLLMAdapter(),
        embedder=MockEmbedder(dim=4),
        vector_store=MockVectorStore(),
        forest=object(),
        clustering=NoiseClustering(),
        max_children=2,
    )

    groups = engine._complete_groups(np.eye(5, dtype=np.float32))

    assert sorted(index for group in groups for index in group) == [0, 1, 2, 3, 4]
    assert all(0 < len(group) <= 2 for group in groups)


def test_export_contains_manifest_and_checksums(tmp_path: Path):
    db = _db(tmp_path / "workspace")
    db.ingest("A source paragraph with enough content to make one immutable atom.")
    db.reindex_all()
    archive = tmp_path / "export.zip"
    db.export(archive)

    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
        assert "index-manifest.json" in names
        assert "checksums.json" in names
        manifest = json.loads(handle.read("index-manifest.json"))
        assert manifest["active"]["state"] == "active"


def test_empty_lancedb_rebuild_publishes_an_empty_versioned_collection(tmp_path: Path):
    vector_store = LanceDBStore(tmp_path / "vectors.lance")
    vector_store.upsert("stale-node", np.zeros(16, dtype=np.float32), {})
    db = _db(tmp_path / "workspace", vector_store=vector_store)

    result = db.reindex_all()

    assert result.state == "active"
    assert vector_store.count() == 0
    assert db.validate_index()["healthy"] is True


def test_provider_config_file_has_no_plaintext_key(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(config_path))
    secrets: dict[str, str] = {}
    monkeypatch.setattr(providers, "_keyring_set", lambda provider_id, value: secrets.__setitem__(provider_id, value))
    monkeypatch.setattr(providers, "_keyring_get", lambda provider_id, *, required=False: secrets.get(provider_id))
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))
    save_provider_key("openai", api_key="sk-never-on-disk", config_path=config_path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert "api_key" not in json.dumps(payload)
