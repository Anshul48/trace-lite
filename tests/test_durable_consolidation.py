"""Regression tests for persistent tree assignment and Cortex rebuilds."""

from pathlib import Path

import pytest

from trace_lite import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.cortex import MockVectorStore, Tree
from trace_lite.db import QueryBlockedError
from trace_lite.spine import Atom, SourceArtifact


def _new_db(data_dir: Path, vector_store: MockVectorStore | None = None) -> TraceLite:
    return TraceLite(
        data_dir,
        embedder=MockEmbedder(dim=32),
        llm=MockLLMAdapter(),
        vector_store=vector_store or MockVectorStore(),
    )


def _two_atom_text() -> str:
    return (
        "RAPTOR builds durable hierarchical retrieval indexes from source atoms. "
        "Its summary nodes preserve the relationships between related passages.\n\n"
        "LATTICE searches those hierarchies and flat vectors to retrieve evidence. "
        "The retrieval path must remain available after the process restarts."
    )


def test_pending_source_captures_survive_restart_and_organize(tmp_path: Path):
    data_dir = tmp_path / "durable"
    initial = _new_db(data_dir)

    ingest_result = initial.ingest(_two_atom_text(), document_name="Durability")
    assert ingest_result.tree_ids == []
    assert initial.forest.has_pending_source_atoms()

    # A new CLI/REPL process receives a new database object and vector store.
    restarted = _new_db(data_dir)
    consolidation = restarted.consolidate()

    assert consolidation.trees_updated == 1
    assert not restarted.forest.has_pending_assignments()

    result = restarted.query("How does LATTICE retrieve evidence?", top_k=1)
    assert result.items
    assert result.items[0].atom is not None


def test_default_query_blocks_pending_and_force_excludes_them(tmp_path: Path):
    data_dir = tmp_path / "query-repair"
    initial = _new_db(data_dir)
    initial.ingest(_two_atom_text(), document_name="Indexed source")
    initial.organize()
    initial.ingest(
        "A newly captured source must remain pending and be excluded from force query results.",
        document_name="Pending source",
    )

    with pytest.raises(QueryBlockedError, match="pending organization"):
        initial.query("What does RAPTOR build?", top_k=1, mode="tree")

    result = initial.query("What does RAPTOR build?", top_k=1, force=True)

    assert result.items
    assert result.mode == "flat"
    assert result.warnings
    assert initial.forest.has_pending_source_atoms()


def test_legacy_orphaned_atoms_rebuild_empty_placeholder_tree(tmp_path: Path):
    db = _new_db(tmp_path / "legacy")
    artifact = SourceArtifact.create("art-legacy", "Legacy content", document_name="Legacy")
    db.spine.store_artifact(artifact)
    atoms = [
        Atom.create(
            "atom-legacy-1",
            "Legacy source atoms must be recoverable even when Cortex has no leaves.",
            artifact.artifact_id,
            0,
            0,
            72,
        ),
        Atom.create(
            "atom-legacy-2",
            "Consolidation should rebuild the placeholder tree without re-ingesting data.",
            artifact.artifact_id,
            1,
            73,
            150,
        ),
    ]
    db.spine.store_atoms_batch(atoms)
    db.forest.store_tree(Tree("legacy-tree", "Legacy", "Empty placeholder"))

    consolidation = db.consolidate()

    assert consolidation.trees_updated == 1
    assert db.forest.get_indexed_atom_ids("legacy-tree") == {
        atom.atom_id for atom in atoms
    }
    assert not db.forest.has_pending_assignments()


def test_rebuilding_tree_replaces_stale_nodes_and_vectors(tmp_path: Path):
    vector_store = MockVectorStore()
    db = _new_db(tmp_path / "rebuild", vector_store=vector_store)
    db.ingest(_two_atom_text(), document_name="Rebuild")

    first = db.consolidate()
    tree_id = db.trees()[0].tree_id
    first_tree = db.forest.get_tree(tree_id)
    assert first.trees_updated == 1
    assert first_tree is not None

    second = db.consolidate(tree_id=tree_id)
    second_tree = db.forest.get_tree(tree_id)
    current_nodes = db.forest.get_tree_nodes(tree_id)

    assert second.trees_updated == 1
    assert second.summaries_generated == second_tree.node_count - second_tree.leaf_count
    assert len(current_nodes) == second_tree.node_count
    assert set(vector_store.nodes) == {node.node_id for node in current_nodes}
    assert len(vector_store.nodes) == second_tree.node_count
