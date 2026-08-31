"""Tests for Personalized PageRank Graph Activation Engine, SQLite Graph Edge Schema, and Multi-Hop Retrieval."""

import pytest
from pathlib import Path

from trace_lite import TraceLite
from trace_lite.spine import SpineStore, Atom, SourceArtifact
from trace_lite.cortex import ForestIndex, MockVectorStore, TreeNode, Tree
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.engines import (
    RaptorEngine,
    LatticeEngine,
    GraphActivationEngine,
    generate_sequential_edges,
    generate_hierarchical_edges,
    generate_co_occurrence_edges,
    generate_all_edges,
)


def test_ppr_engine_basic():
    engine = GraphActivationEngine(damping=0.85, max_iterations=20)

    # Linear chain with bi-directional graph: A <-> B <-> C
    seeds = {"A": 1.0}
    adjacency = {
        "A": [("B", 1.0)],
        "B": [("A", 1.0), ("C", 1.0)],
        "C": [("B", 1.0)],
    }

    scores = engine.personalized_pagerank(seeds, adjacency)
    assert len(scores) == 3
    assert "A" in scores and "B" in scores and "C" in scores
    assert scores["A"] > 0
    assert scores["B"] > 0
    assert scores["C"] > 0
    assert scores["B"] > scores["C"]
    assert scores["A"] > scores["C"]


def test_ppr_engine_empty():
    engine = GraphActivationEngine()
    assert engine.personalized_pagerank({}, {}) == {}
    assert engine.personalized_pagerank({"A": 1.0}, {}) == {}
    assert engine.personalized_pagerank({}, {"A": [("B", 1.0)]}) == {}


def test_forest_graph_edge_storage(tmp_path: Path):
    cortex_path = tmp_path / "cortex.sqlite3"
    forest = ForestIndex(cortex_path)

    edges = [
        {
            "edge_id": "edge-1",
            "source_atom_id": "atom-1",
            "target_atom_id": "atom-2",
            "relation_type": "NEXT",
            "weight": 1.0,
            "confidence": 1.0,
        },
        {
            "edge_id": "edge-2",
            "source_atom_id": "atom-2",
            "target_atom_id": "atom-3",
            "relation_type": "NEXT",
            "weight": 0.8,
            "confidence": 0.9,
        },
        {
            "edge_id": "edge-3",
            "source_atom_id": "atom-10",
            "target_atom_id": "atom-11",
            "relation_type": "CO_OCCURS",
            "weight": 0.5,
            "confidence": 1.0,
        },
    ]

    forest.store_edges(edges)

    # Query neighbors of atom-2
    neighbors = forest.get_neighbor_edges(["atom-2"])
    assert len(neighbors) == 2
    edge_ids = {e["edge_id"] for e in neighbors}
    assert edge_ids == {"edge-1", "edge-2"}

    # Query all edges
    all_edges = forest.get_all_edges()
    assert len(all_edges) == 3

    # Reset derived
    forest.reset_derived()
    assert len(forest.get_all_edges()) == 0


def test_zero_llm_edge_generators():
    atoms = [
        Atom.create("atom-1", "Personalized PageRank graph activation expands retrieval.", "art-1", 0, 0, 50),
        Atom.create("atom-2", "Graph activation links sequential atoms and entities.", "art-1", 1, 51, 100),
        Atom.create("atom-3", "SQLite database stores graph edges for multi-hop search.", "art-2", 0, 0, 50),
    ]

    # 1. Sequential edges
    seq_edges = generate_sequential_edges(atoms)
    assert len(seq_edges) == 2  # NEXT(atom-1->atom-2), PREV(atom-2->atom-1)
    types = {e["relation_type"] for e in seq_edges}
    assert types == {"NEXT", "PREV"}

    # 2. Co-occurrence edges (atoms 1 and 2 share "graph", "activation")
    co_edges = generate_co_occurrence_edges(atoms)
    assert len(co_edges) >= 2
    assert all(e["relation_type"] == "CO_OCCURS" for e in co_edges)

    # 3. Hierarchical edges
    nodes = [
        TreeNode(
            node_id="parent-1",
            tree_id="tree-1",
            level=1,
            node_type="cluster_summary",
            atom_ids=["atom-1", "atom-2"],
            summary_text="Graph activation overview",
            children_ids=["leaf-1", "leaf-2"],
        )
    ]
    hier_edges = generate_hierarchical_edges(nodes)
    assert len(hier_edges) > 0
    rel_types = {e["relation_type"] for e in hier_edges}
    assert "PARENT_OF" in rel_types and "CHILD_OF" in rel_types


def test_multi_hop_graph_retrieval(tmp_path: Path):
    db = TraceLite(
        tmp_path / "multi_hop_db",
        embedder=MockEmbedder(dim=32),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(dim=32),
    )

    # Ingest document with sequential and multi-hop structure
    db.ingest(
        "Hop 1: Alpha protocol establishes the initial network handshake.\n\n"
        "Hop 2: Beta protocol validates the cryptographic token from the handshake.\n\n"
        "Hop 3: Gamma protocol grants resource authorization after token validation.",
        document_name="Protocol Specs",
    )

    db.reindex_all()

    # Query targeting Hop 1; graph activation should propagate to Hop 2 and Hop 3
    res = db.query("Alpha protocol establishes initial handshake", top_k=5, mode="hybrid")
    assert len(res.items) >= 2
    
    # Check that channel_scores contains graph_ppr
    for item in res.items:
        assert "graph_ppr" in item.channel_scores
        assert "flat" in item.channel_scores
        assert "tree" in item.channel_scores
        assert "bm25" in item.channel_scores


def test_adaptive_3_tier_gating(tmp_path: Path):
    spine = SpineStore(tmp_path / "spine.sqlite3")
    forest = ForestIndex(tmp_path / "cortex.sqlite3")
    vector_store = MockVectorStore(dim=16)
    embedder = MockEmbedder(dim=16)
    llm = MockLLMAdapter()

    artifact = SourceArtifact.create("art-gate", "Gating test", document_name="Gating")
    spine.store_artifact(artifact)

    atoms = [
        Atom.create("atom-g1", "Exact needle term unique alpha token.", "art-gate", 0, 0, 50),
        Atom.create("atom-g2", "Secondary information for multi-hop graph spreading.", "art-gate", 1, 51, 100),
    ]
    spine.store_atoms_batch(atoms)

    raptor = RaptorEngine(
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        forest=forest,
    )
    raptor.build_tree("tree-g", atoms)

    # Store edge between g1 and g2
    forest.store_edges([
        {
            "edge_id": "edge-g1-g2",
            "source_atom_id": "atom-g1",
            "target_atom_id": "atom-g2",
            "relation_type": "NEXT",
            "weight": 1.0,
            "confidence": 1.0,
        }
    ])

    lattice = LatticeEngine(
        llm=llm,
        embedder=embedder,
        vector_store=vector_store,
        forest=forest,
        spine=spine,
    )

    # Gate 1: Abstention Gate (Query with no lexical or vector matches)
    res_abstain = lattice.query("xyznonexistentterm12345", top_k=5, mode="hybrid")
    assert res_abstain.sufficiency_state == "insufficient_evidence"

    # Gate 3: Multi-hop query
    res_match = lattice.query("Exact needle term unique alpha token.", top_k=5, mode="hybrid")
    assert len(res_match.items) > 0
    assert res_match.sufficiency_state == "answerable"
