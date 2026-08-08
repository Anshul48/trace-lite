import pytest
from pathlib import Path
from trace_lite.cortex import (
    MockVectorStore,
    ForestIndex,
    Tree,
    TreeNode,
    EnergyModel,
    ClusteringPipeline,
)
import numpy as np


def test_vector_store():
    store = MockVectorStore()
    vec1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    vec2 = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    store.upsert("node-1", vec1, {"tree_id": "tree-1"})
    store.upsert("node-2", vec2, {"tree_id": "tree-1"})

    results = store.search(vec1, top_k=2)
    assert len(results) == 2
    assert results[0].node_id == "node-1"
    assert results[0].score > 0.99


def test_forest_index(tmp_path: Path):
    db_path = tmp_path / "cortex.sqlite3"
    forest = ForestIndex(db_path)

    tree = Tree(
        tree_id="tree-1",
        name="Test Tree",
        description="A tree for testing",
        root_node_id="root-1",
        node_count=3,
        leaf_count=2,
        depth=1,
    )
    forest.store_tree(tree)

    leaf1 = TreeNode("leaf-1", "tree-1", 0, "leaf", ["atom-1"], None, parent_id="root-1")
    leaf2 = TreeNode("leaf-2", "tree-1", 0, "leaf", ["atom-2"], None, parent_id="root-1")
    root = TreeNode(
        "root-1",
        "tree-1",
        1,
        "root_summary",
        ["atom-1", "atom-2"],
        "Summary of tree",
        children_ids=["leaf-1", "leaf-2"],
    )

    forest.store_nodes_batch([leaf1, leaf2, root])

    fetched_tree = forest.get_tree("tree-1")
    assert fetched_tree is not None
    assert fetched_tree.name == "Test Tree"

    fetched_root = forest.get_node("root-1")
    assert fetched_root is not None
    assert len(fetched_root.children_ids) == 2

    children = forest.get_children("root-1")
    assert len(children) == 2


def test_energy_model():
    energy = EnergyModel(decay_exponent=0.5, retrieval_threshold=0.1)

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=10)
    old = now - timedelta(days=30)

    act_recent = energy.compute_activation(recent, access_count=1, now=now)
    assert act_recent == 1.0

    act_old = energy.compute_activation(old, access_count=1, now=now)
    assert act_old < 0.1
    assert not energy.is_active(old, access_count=1, now=now)

    # Frequency boost should increase activation
    act_old_frequent = energy.compute_activation(old, access_count=50, now=now)
    assert act_old_frequent > act_old


def test_clustering_pipeline():
    pipeline = ClusteringPipeline(min_cluster_size=2)
    # Create two distinct clusters in 3D
    cluster1 = np.random.randn(4, 3) + np.array([10.0, 0.0, 0.0])
    cluster2 = np.random.randn(4, 3) + np.array([0.0, 10.0, 0.0])
    embeddings = np.vstack([cluster1, cluster2])

    clusters, orphans = pipeline.cluster(embeddings)
    assert len(clusters) >= 1
