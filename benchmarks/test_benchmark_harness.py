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
from benchmarks.baselines.hipporag_retriever import HippoRagPPRRetriever
from benchmarks.adapters.hipporag_adapter import HippoRagAdapter
from benchmarks.adapters.trec_rag_adapter import TrecRagAdapter
from benchmarks.adapters.mteb_adapter import MtebAdapter
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
from benchmarks.metrics.aggregator import (
    BenchmarkAggregator,
    BaselineEvaluationResult,
    BenchmarkRunResult,
    compute_paired_statistics,
    check_release_gates,
)
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


def test_curated_in_domain_manifest():
    curated_path = Path(__file__).parent / "datasets" / "trace_engineering_curated.json"
    manifest, corpus = load_manifest(curated_path)
    assert manifest.name == "trace-engineering-curated"
    assert manifest.release_authority is True
    assert len(manifest.cases) == 320
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


def test_hipporag_ppr_retriever():
    docs = [
        IndexedDocument(doc_id="doc1", text="Steve Jobs founded NeXT in 1985 after leaving Apple. NeXT developed NeXTSTEP."),
        IndexedDocument(doc_id="doc2", text="Apple acquired NeXT in 1997, and NeXTSTEP became Mac OS X."),
        IndexedDocument(doc_id="doc3", text="Linux kernel was created by Linus Torvalds."),
    ]
    hippo = HippoRagPPRRetriever()
    hippo.index(docs)
    res = hippo.retrieve("Steve Jobs Apple operating system", top_k=2)
    assert len(res) > 0
    assert res[0].doc_id in ("doc1", "doc2")


def test_hipporag_and_trec_adapters(tmp_path):
    hippo_adapter = HippoRagAdapter(task="sample", cache_dir=tmp_path)
    hippo_adapter.download_or_prepare()
    hippo_docs = hippo_adapter.load_corpus()
    hippo_queries = hippo_adapter.load_queries()
    assert len(hippo_docs) > 0
    assert len(hippo_queries) > 0

    trec_adapter = TrecRagAdapter(cache_dir=tmp_path)
    trec_adapter.download_or_prepare()
    trec_docs = trec_adapter.load_corpus()
    trec_queries = trec_adapter.load_queries()
    assert len(trec_docs) > 0
    assert len(trec_queries) > 0


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

    assert recall_at_k(retrieved, gold, 5) == 1.0
    assert recall_at_k(retrieved, gold, 1) == 0.0
    assert complete_gold_coverage(retrieved, gold, 5) == 1.0
    assert complete_gold_coverage(retrieved, gold, 2) == 0.0
    assert citation_precision(retrieved, gold, 5) == 0.4
    assert mrr_at_k(retrieved, gold, 5) == 0.5
    assert abstention_accuracy(True, True) == 1.0
    assert abstention_accuracy(False, True) == 0.0


def test_paired_statistics():
    c_scores = [0.9, 0.85, 0.88, 0.92, 0.95]
    b_scores = [0.7, 0.65, 0.72, 0.68, 0.75]
    stats = compute_paired_statistics(c_scores, b_scores)
    assert stats["delta"] > 0.15
    assert stats["p_value"] < 0.05
    assert stats["stat_significant"] is True


def test_release_gates_enforcement():
    good_res = BaselineEvaluationResult(
        baseline_name="test_good",
        overall_complete_coverage_at_10=0.85,
        overall_citation_precision_at_5=0.92,
        overall_abstention_accuracy=0.98,
        organization={"source_atom_coverage": 1.0},
    )
    passed, fails = check_release_gates(good_res, release_authority=True)
    assert passed is True
    assert len(fails) == 0

    bad_res = BaselineEvaluationResult(
        baseline_name="test_bad",
        overall_complete_coverage_at_10=0.60,
        overall_citation_precision_at_5=0.75,
        overall_abstention_accuracy=0.80,
    )
    passed_bad, fails_bad = check_release_gates(bad_res, release_authority=True)
    assert passed_bad is False
    assert len(fails_bad) >= 3


def test_scale_generator_determinism():
    manifest1, c1 = generate_scale_benchmark(tier="1k", seed=42)
    manifest2, c2 = generate_scale_benchmark(tier="1k", seed=42)
    assert manifest1.corpus_sha256 == manifest2.corpus_sha256
    assert c1 == c2
    assert len(manifest1.cases) == 8
