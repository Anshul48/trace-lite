import pytest
from pathlib import Path

from trace_lite.spine import SpineStore, Atom, SourceArtifact
from trace_lite.cortex import ForestIndex, MockVectorStore
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.engines import RaptorEngine, ForestRouter, LatticeEngine


@pytest.fixture
def sample_data(tmp_path: Path):
    spine_path = tmp_path / "spine.sqlite3"
    cortex_path = tmp_path / "cortex.sqlite3"

    spine = SpineStore(spine_path)
    forest = ForestIndex(cortex_path)
    vector_store = MockVectorStore()

    embedder = MockEmbedder(dim=384)
    llm = MockLLMAdapter()

    artifact = SourceArtifact.create("art-1", "Doc 1 text", document_name="Doc 1")
    spine.store_artifact(artifact)

    atoms = [
        Atom.create("atom-1", "Neural networks process vector data.", "art-1", 0, 0, 35),
        Atom.create("atom-2", "Convolutional layers analyze spatial features.", "art-1", 1, 36, 78),
        Atom.create("atom-3", "SQLite handles relational data in a single file.", "art-1", 2, 79, 120),
    ]
    spine.store_atoms_batch(atoms)

    return {
        "spine": spine,
        "forest": forest,
        "vector_store": vector_store,
        "embedder": embedder,
        "llm": llm,
        "atoms": atoms,
    }


def test_raptor_engine(sample_data):
    raptor = RaptorEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
    )

    tree = raptor.build_tree("tree-1", sample_data["atoms"], tree_name="AI & DB Notes")

    assert tree.tree_id == "tree-1"
    assert tree.name == "AI & DB Notes"
    assert tree.leaf_count == 3
    assert tree.root_node_id is not None

    root_node = sample_data["forest"].get_node(tree.root_node_id)
    assert root_node is not None
    assert root_node.node_type == "root_summary"


def test_forest_router(sample_data):
    router = ForestRouter(
        embedder=sample_data["embedder"],
        llm=sample_data["llm"],
        forest=sample_data["forest"],
        vector_store=sample_data["vector_store"],
    )

    routing_map = router.route(sample_data["atoms"])
    assert len(routing_map) >= 1
    total_routed = sum(len(atoms) for atoms in routing_map.values())
    assert total_routed >= len(sample_data["atoms"])


def test_lattice_engine(sample_data):
    raptor = RaptorEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
    )
    tree = raptor.build_tree("tree-1", sample_data["atoms"])

    lattice = LatticeEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
        spine=sample_data["spine"],
    )

    res = lattice.query("neural networks", top_k=2, mode="hybrid")
    assert len(res.items) > 0
    assert res.items[0].atom.content is not None
    assert res.items[0].source_artifact is not None
