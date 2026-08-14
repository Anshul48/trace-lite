"""Automated unit test suite verifying the benchmark harness, metrics, baselines, and loaders."""

import tempfile
from pathlib import Path
import pytest

from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase
from benchmarks.datasets.loader import load_manifest, compute_sha256, DatasetIntegrityError
from benchmarks.baselines.base import IndexedDocument
from benchmarks.baselines.bm25_retriever import BM25Retriever
from benchmarks.baselines.dense_retriever import DenseRetriever
from benchmarks.baselines.hybrid_rrf_retriever import HybridRRFRetriever
from benchmarks.baselines.flat_hierarchy_retriever import FlatHierarchyRetriever
from benchmarks.metrics.retrieval import (
    recall_at_k,
    complete_gold_coverage,
    citation_precision,
    ndcg_at_k,
    mrr_at_k,
    abstention_accuracy,
    evaluate_case_retrieval,
)
from benchmarks.metrics.organization import (
    source_atom_coverage,
    orphan_count,
    duplicate_membership_count,
    routing_purity,
)
from benchmarks.metrics.aggregator import BenchmarkAggregator
from benchmarks.generators.scale_corpus import generate_scale_benchmark


def test_compute_sha256():
    text1 = "Hello world\nSecond line"
    text2 = "Hello world\r\nSecond line"
    # Should normalize CRLF to LF
    assert compute_sha256(text1) == compute_sha256(text2)


def test_dev_manifest_loader():
    dev_path = Path(__file__).parent / "datasets" / "technical_docs_dev.json"
    manifest, corpus = load_manifest(dev_path)
    assert manifest.name == "trace-technical-docs-dev"
    assert manifest.release_authority is False
    assert len(manifest.cases) == 6
    assert manifest.corpus_sha256 == compute_sha256(corpus)


def test_manifest_integrity_mismatch():
    manifest = BenchmarkManifest(
        name="test-bad",
        corpus_sha256="wrong_hash_12345",
        corpus_text="Sample text",
        cases=[],
    )
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(manifest.model_dump_json())
        temp_path = f.name

    with pytest.raises(DatasetIntegrityError):
        load_manifest(temp_path)


def test_bm25_retriever_execution():
    docs = [
        IndexedDocument(doc_id="doc1", text="The quick brown fox jumps over the lazy dog."),
        IndexedDocument(doc_id="doc2", text="Distributed consensus algorithms use Raft for leader election."),
        IndexedDocument(doc_id="doc3", text="Reciprocal rank fusion combines lexical and dense rankings."),
    ]
    bm25 = BM25Retriever()
    bm25.index(docs)

    results = bm25.retrieve("Raft leader election", top_k=2)
    assert len(results) > 0
    assert results[0].doc_id == "doc2"


def test_dense_and_hybrid_rrf_retrievers():
    docs = [
        IndexedDocument(doc_id="doc1", text="Quantum computing protocols for cryogenic states."),
        IndexedDocument(doc_id="doc2", text="Database storage engines write-ahead logs and WAL durability."),
    ]
    dense = DenseRetriever(model_name="all-MiniLM-L6-v2")
    dense.index(docs)
    res_dense = dense.retrieve("database durability and WAL", top_k=1)
    assert len(res_dense) == 1
    assert res_dense[0].doc_id == "doc2"

    rrf = HybridRRFRetriever(dense_model="all-MiniLM-L6-v2")
    rrf.index(docs)
    res_rrf = rrf.retrieve("database durability and WAL", top_k=1)
    assert len(res_rrf) == 1
    assert res_rrf[0].doc_id == "doc2"


def test_flat_hierarchy_retriever():
    docs = [
        IndexedDocument(doc_id=f"doc-{i}", text=f"Document topic {i % 3} with content {i}")
        for i in range(10)
    ]
    hier = FlatHierarchyRetriever(dense_model="all-MiniLM-L6-v2", cluster_size=3)
    hier.index(docs)
    res = hier.retrieve("Document topic 1", top_k=3)
    assert len(res) > 0


def test_metric_calculations():
    retrieved = ["a1", "a2", "a3", "a4", "a5"]
    gold = ["a2", "a4"]

    # Recall@5: both gold found -> 1.0
    assert recall_at_k(retrieved, gold, 5) == 1.0
    # Recall@1: none found in top 1 -> 0.0
    assert recall_at_k(retrieved, gold, 1) == 0.0
    # Complete gold coverage: all in top 5 -> 1.0
    assert complete_gold_coverage(retrieved, gold, 5) == 1.0
    # Complete gold coverage: not all in top 2 -> 0.0
    assert complete_gold_coverage(retrieved, gold, 2) == 0.0
    # Precision@5: 2 hits out of 5 -> 0.4
    assert citation_precision(retrieved, gold, 5) == 0.4
    # MRR: first hit at rank 2 -> 1/2 = 0.5
    assert mrr_at_k(retrieved, gold, 5) == 0.5
    # Abstention
    assert abstention_accuracy(True, True) == 1.0
    assert abstention_accuracy(False, True) == 0.0


def test_organization_metrics():
    preserved = {"atom-1", "atom-2", "atom-3"}
    expected = {"atom-1", "atom-2", "atom-3", "atom-4"}
    assert source_atom_coverage(preserved, expected) == 0.75

    indexed = {"atom-1", "atom-2"}
    assert orphan_count(preserved, indexed) == 1

    node_memberships = [["a1", "a2"], ["a3"], ["a2", "a4"]]
    assert duplicate_membership_count(node_memberships) == 1

    assignments = ["cluster_A", "cluster_A", "cluster_B", "cluster_B"]
    labels = ["topic_1", "topic_1", "topic_2", "topic_2"]
    assert routing_purity(assignments, labels) == 1.0


def test_scale_generator_determinism():
    manifest1, c1 = generate_scale_benchmark(tier="1k", seed=42)
    manifest2, c2 = generate_scale_benchmark(tier="1k", seed=42)
    assert manifest1.corpus_sha256 == manifest2.corpus_sha256
    assert c1 == c2
    assert len(manifest1.cases) == 8


def test_aggregator():
    case_metric = evaluate_case_retrieval(
        case_id="c1",
        category="direct_lookup",
        retrieved_ids=["atom-1"],
        gold_ids=["atom-1"],
        is_abstaining=False,
        expected_abstention=False,
    )
    res = BenchmarkAggregator.aggregate_baseline("bm25", [case_metric])
    assert res.overall_recall_at_5 == 1.0
    assert res.overall_complete_coverage_at_10 == 1.0
    assert "direct_lookup" in res.category_breakdown
