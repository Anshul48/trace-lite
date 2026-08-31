"""TREC RAG 2026 Retrieval task adapter (ClimbMix / segmented deep retrieval)."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class TrecRagAdapter(BaseDatasetAdapter):
    """
    Adapter for TREC RAG 2026 Retrieval task.
    Evaluates deep retrieval over long narrative queries and segmented collections.
    """

    def __init__(self, cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.dataset_dir = self.cache_dir / "trec_rag_2026"

    def dataset_name(self) -> str:
        return "trec-rag-2026"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        # Writes sample segment mappings if offline or missing
        corpus_path = self.dataset_dir / "corpus.jsonl"
        queries_path = self.dataset_dir / "queries.jsonl"

        if not corpus_path.exists():
            sample_docs = [
                {"id": "doc-trec-01", "title": "Distributed Storage Replication", "text": "Replication factor 3 ensures tolerance against two node failures in a Raft consensus ring."},
                {"id": "doc-trec-02", "title": "Segment Routing Protocol", "text": "Segment routing maps explicit policy paths across intermediate carrier transport domains."},
                {"id": "doc-trec-03", "title": "Memory Substrate Architecture", "text": "The source spine preserves immutable provenance hashes and hierarchical tree summaries."},
            ]
            with open(corpus_path, "w", encoding="utf-8") as f:
                for doc in sample_docs:
                    f.write(json.dumps(doc) + "\n")

        if not queries_path.exists():
            sample_queries = [
                {"id": "trec-q-01", "query": "How does replication factor 3 provide fault tolerance in Raft consensus?", "gold_ids": ["doc-trec-01"]},
                {"id": "trec-q-02", "query": "What maps explicit policy paths in segment routing protocols?", "gold_ids": ["doc-trec-02"]},
            ]
            with open(queries_path, "w", encoding="utf-8") as f:
                for q in sample_queries:
                    f.write(json.dumps(q) + "\n")

    def load_corpus(self) -> list[CorpusDocument]:
        self.download_or_prepare()
        corpus_path = self.dataset_dir / "corpus.jsonl"
        docs: list[CorpusDocument] = []
        with open(corpus_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    docs.append(
                        CorpusDocument(
                            doc_id=str(item.get("id", item.get("doc_id", ""))),
                            title=item.get("title", ""),
                            text=item.get("text", item.get("content", "")),
                        )
                    )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        self.download_or_prepare()
        queries_path = self.dataset_dir / "queries.jsonl"
        queries: list[BenchmarkQuery] = []
        with open(queries_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    queries.append(
                        BenchmarkQuery(
                            query_id=str(item.get("id", item.get("query_id", ""))),
                            text=item.get("query", item.get("text", "")),
                            gold_doc_ids=[str(x) for x in item.get("gold_ids", [])],
                            category="global_context",
                        )
                    )
        return queries
