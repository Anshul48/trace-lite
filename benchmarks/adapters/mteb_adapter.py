"""MTEB / LMEB embedding layer benchmark adapter."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class MtebAdapter(BaseDatasetAdapter):
    """
    Adapter for MTEB / LMEB retrieval tasks.
    Evaluates raw embedding bi-encoder backends against standardized retrieval tasks.
    """

    def __init__(self, task_name: str = "scifact", cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.task_name = task_name.lower()
        self.dataset_dir = self.cache_dir / f"mteb_{self.task_name}"

    def dataset_name(self) -> str:
        return f"mteb-{self.task_name}"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        # Writes sample MTEB benchmark data if not present
        corpus_path = self.dataset_dir / "corpus.jsonl"
        queries_path = self.dataset_dir / "queries.jsonl"

        if not corpus_path.exists():
            sample_docs = [
                {"id": "mteb-doc-01", "title": "Transformer Attention Mechanisms", "text": "Multi-head self-attention computes query-key dot products scaled by square root of head dimension."},
                {"id": "mteb-doc-02", "title": "Vector Quantization", "text": "Product quantization decomposes high-dimensional vector spaces into Cartesian products of low-dimensional subspaces."},
            ]
            with open(corpus_path, "w", encoding="utf-8") as f:
                for doc in sample_docs:
                    f.write(json.dumps(doc) + "\n")

        if not queries_path.exists():
            sample_queries = [
                {"id": "mteb-q-01", "query": "How is multi-head attention scaled?", "gold_ids": ["mteb-doc-01"]},
                {"id": "mteb-q-02", "query": "What is product quantization in vector search?", "gold_ids": ["mteb-doc-02"]},
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
                            category="direct_lookup",
                        )
                    )
        return queries
