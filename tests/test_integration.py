import pytest
from pathlib import Path
from trace_lite import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.cortex import MockVectorStore


@pytest.fixture
def db(tmp_path: Path):
    data_dir = tmp_path / "trace_data"
    embedder = MockEmbedder(dim=384)
    llm = MockLLMAdapter()
    vector_store = MockVectorStore()

    return TraceLite(
        data_dir=data_dir,
        embedder=embedder,
        llm=llm,
        vector_store=vector_store,
    )


def test_end_to_end_ingest_consolidate_query(db: TraceLite, tmp_path: Path):
    # 1. Ingest text
    text1 = (
        "RAPTOR constructs a hierarchical tree of documents using recursive clustering. "
        "It generates summaries for each cluster to enable multi-level retrieval."
    )
    text2 = (
        "LATTICE uses LLM-guided search to traverse the tree top-down. "
        "It achieves logarithmic search complexity over millions of tokens."
    )

    res1 = db.ingest(text1, document_name="RAPTOR Paper")
    assert res1.atom_count >= 1

    res2 = db.ingest(text2, document_name="LATTICE Paper")
    assert res2.atom_count >= 1

    # 2. Consolidate into RAPTOR trees
    cons_res = db.consolidate()
    assert cons_res.trees_updated >= 1

    # 3. Query
    query_res = db.query("How does LATTICE traverse trees?", top_k=2, mode="hybrid")
    assert len(query_res.items) > 0
    assert query_res.items[0].atom is not None
    assert query_res.items[0].source_artifact is not None

    # 4. Status
    status = db.status()
    assert status.total_atoms >= 2
    assert status.total_trees >= 1

    # 5. Export
    zip_path = tmp_path / "export.zip"
    db.export(zip_path)
    assert zip_path.exists()
