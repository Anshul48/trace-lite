import pytest
from pathlib import Path

from trace_lite import TraceLite
from trace_lite.spine import SpineStore, Atom, SourceArtifact
from trace_lite.cortex import ForestIndex, MockVectorStore, TreeNode, Tree
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.engines import RaptorEngine, ForestRouter, LatticeEngine
from trace_lite.db import QueryBlockedError


@pytest.fixture
def sample_data(tmp_path: Path):
    spine_path = tmp_path / "spine.sqlite3"
    cortex_path = tmp_path / "cortex.sqlite3"

    spine = SpineStore(spine_path)
    forest = ForestIndex(cortex_path)
    vector_store = MockVectorStore(dim=384)

    embedder = MockEmbedder(dim=384)
    llm = MockLLMAdapter()

    artifact = SourceArtifact.create(
        "art-1", "Doc 1 text", document_name="Doc 1", source_uri="file:///doc1.txt"
    )
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
        "artifact": artifact,
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


def test_tri_channel_hybrid_fusion_scoring(sample_data):
    raptor = RaptorEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
    )
    raptor.build_tree("tree-1", sample_data["atoms"])

    lattice = LatticeEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
        spine=sample_data["spine"],
    )

    res = lattice.query("Neural networks vector", top_k=3, mode="hybrid")
    assert len(res.items) > 0
    for item in res.items:
        cs = item.channel_scores
        assert "flat" in cs
        assert "tree" in cs
        assert "bm25" in cs
        assert "lexical" in cs
        assert "graph_ppr" in cs
        assert "fused" in cs
        if cs["graph_ppr"] > 0:
            expected_score = 0.35 * cs["flat"] + 0.25 * cs["tree"] + 0.25 * cs["bm25"] + 0.15 * cs["graph_ppr"]
        else:
            expected_score = 0.40 * cs["flat"] + 0.30 * cs["tree"] + 0.30 * cs["bm25"]
        assert item.score == pytest.approx(expected_score, rel=1e-5)
        assert item.channel_scores["fused"] == item.score
        assert item.channel_scores["lexical"] == item.channel_scores["bm25"]


def test_mode_switching(sample_data):
    raptor = RaptorEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
    )
    raptor.build_tree("tree-1", sample_data["atoms"])

    lattice = LatticeEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
        spine=sample_data["spine"],
    )

    # 1. Tree mode
    res_tree = lattice.query("SQLite relational", top_k=3, mode="tree")
    assert res_tree.mode == "tree"
    for item in res_tree.items:
        assert item.score == item.channel_scores["tree"]

    # 2. Flat mode
    res_flat = lattice.query("SQLite relational", top_k=3, mode="flat")
    assert res_flat.mode == "flat"
    for item in res_flat.items:
        assert item.score == item.channel_scores["flat"]

    # 3. Lexical mode
    res_lex = lattice.query("SQLite relational", top_k=3, mode="lexical")
    assert res_lex.mode == "lexical"
    for item in res_lex.items:
        assert item.score == item.channel_scores["bm25"]
        assert item.score == item.channel_scores["lexical"]


def test_sufficiency_states(tmp_path: Path):
    spine = SpineStore(tmp_path / "spine.sqlite3")
    forest = ForestIndex(tmp_path / "cortex.sqlite3")
    vector_store = MockVectorStore(dim=16)
    embedder = MockEmbedder(dim=16)
    llm = MockLLMAdapter()

    artifact = SourceArtifact.create("art-s", "Sufficiency test", document_name="Sufficiency")
    spine.store_artifact(artifact)

    atoms = [
        Atom.create("atom-a", "Quantum computing leverages qubits for superposition.", "art-s", 0, 0, 50),
        Atom.create("atom-b", "General relativity describes spacetime curvature.", "art-s", 1, 51, 100),
    ]
    spine.store_atoms_batch(atoms)

    class CustomScoringSpine:
        def __init__(self, base_spine, scores_map):
            self.base = base_spine
            self.scores_map = scores_map

        def search_lexical(self, query_text, top_k=20):
            return self.scores_map.get(query_text, [])

        def get_atom(self, atom_id):
            return self.base.get_atom(atom_id)

        def get_artifact(self, artifact_id):
            return self.base.get_artifact(artifact_id)

    scores_map = {
        "empty": [],
        "low_score": [("atom-a", 0.15), ("atom-b", 0.10)],
        "ambiguous": [("atom-a", 0.600), ("atom-b", 0.590)],  # diff = 0.010 < 0.015
        "answerable": [("atom-a", 0.850), ("atom-b", 0.500)],  # diff = 0.350 >= 0.015
        "single_hit": [("atom-a", 0.700)],
    }

    mock_spine = CustomScoringSpine(spine, scores_map)
    lattice = LatticeEngine(
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        forest=forest,
        spine=mock_spine,
    )

    # 1. Insufficient evidence: empty
    res_empty = lattice.query("empty", top_k=5, mode="lexical")
    assert res_empty.sufficiency_state == "insufficient_evidence"

    # 2. Insufficient evidence: top score < 0.20
    res_low = lattice.query("low_score", top_k=5, mode="lexical")
    assert res_low.sufficiency_state == "insufficient_evidence"

    # 3. Ambiguous: top 2 scores differ by < 0.015
    res_ambig = lattice.query("ambiguous", top_k=5, mode="lexical")
    assert res_ambig.sufficiency_state == "ambiguous"

    # 4. Answerable: top score >= 0.20 and margin >= 0.015
    res_ans = lattice.query("answerable", top_k=5, mode="lexical")
    assert res_ans.sufficiency_state == "answerable"

    # 5. Answerable: single item with score >= 0.20
    res_single = lattice.query("single_hit", top_k=5, mode="lexical")
    assert res_single.sufficiency_state == "answerable"


def test_structured_citation_location_and_channel_scores(sample_data):
    raptor = RaptorEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
    )
    raptor.build_tree("tree-1", sample_data["atoms"])

    lattice = LatticeEngine(
        llm=sample_data["llm"],
        embedder=sample_data["embedder"],
        vector_store=sample_data["vector_store"],
        forest=sample_data["forest"],
        spine=sample_data["spine"],
    )

    res = lattice.query("Neural networks", top_k=1, mode="hybrid")
    assert len(res.items) == 1
    item = res.items[0]

    assert item.source_location["char_start"] == 0
    assert item.source_location["char_end"] == 35
    assert item.source_location["content_hash"] == item.atom.content_hash
    assert item.source_location["document_name"] == "Doc 1"
    assert item.source_location["source_uri"] == "file:///doc1.txt"

    assert "flat" in item.channel_scores
    assert "tree" in item.channel_scores
    assert "bm25" in item.channel_scores
    assert "lexical" in item.channel_scores
    assert "graph_ppr" in item.channel_scores
    assert "fused" in item.channel_scores


def test_hot_inbox_query_in_tracelite(tmp_path: Path):
    data_dir = tmp_path / "hot_inbox_db"
    db = TraceLite(
        data_dir,
        embedder=MockEmbedder(dim=32),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(dim=32),
    )

    # Ingest text without calling organize
    db.ingest(
        "Hot inbox evidence is immediately searchable lexically without blocking on LLM organization.\n\n"
        "Hierarchical trees will organize it later during consolidation.",
        document_name="Hot Inbox Doc",
    )

    # 1. Mode "tree" with allow_hot_inbox=False -> raises QueryBlockedError
    with pytest.raises(QueryBlockedError, match="pending organization"):
        db.query("searchable lexically", mode="tree", allow_hot_inbox=False)

    # 2. Mode "flat" with allow_hot_inbox=False -> raises QueryBlockedError
    with pytest.raises(QueryBlockedError, match="pending organization"):
        db.query("searchable lexically", mode="flat", allow_hot_inbox=False)

    # 3. Default mode ("hybrid" with allow_hot_inbox=False) -> raises QueryBlockedError
    with pytest.raises(QueryBlockedError, match="pending organization"):
        db.query("searchable lexically", mode="hybrid", allow_hot_inbox=False)

    # 4. Mode "hybrid" with allow_hot_inbox=True -> succeeds with hot-inbox warning
    res_hybrid = db.query("searchable lexically", mode="hybrid", allow_hot_inbox=True)
    assert len(res_hybrid.items) > 0
    assert "Contains unorganized hot-inbox evidence" in res_hybrid.warnings

    # 5. Mode "lexical" with allow_hot_inbox=True -> succeeds with hot-inbox warning
    res_lexical = db.query("searchable lexically", mode="lexical", allow_hot_inbox=True)
    assert len(res_lexical.items) > 0
    assert "Contains unorganized hot-inbox evidence" in res_lexical.warnings

    # 6. Mode "tree" with allow_hot_inbox=True -> succeeds with hot-inbox warning
    res_tree_allowed = db.query("searchable lexically", mode="tree", allow_hot_inbox=True)
    assert "Contains unorganized hot-inbox evidence" in res_tree_allowed.warnings
