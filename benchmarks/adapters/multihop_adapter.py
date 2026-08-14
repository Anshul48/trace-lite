"""MultiHop-RAG / HotpotQA adapter for multi-source evidence benchmarks."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class MultiHopAdapter(BaseDatasetAdapter):
    """
    Adapter for multi-hop retrieval benchmarks (e.g. MultiHop-RAG, HotpotQA, MuSiQue).
    Evaluates whether the retriever recovers ALL supporting evidence hops across multiple documents.
    """

    def __init__(self, dataset_name: str, cache_dir: Path | str):
        super().__init__(cache_dir)
        self._name = dataset_name.lower()
        self.dataset_dir = self.cache_dir / self._name

    def dataset_name(self) -> str:
        return f"multihop-{self._name}"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def load_corpus(self) -> list[CorpusDocument]:
        corpus_path = self.dataset_dir / "corpus.json"
        if not corpus_path.exists():
            corpus_path = self.dataset_dir / "corpus.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"MultiHop corpus missing in {self.dataset_dir}. Run setup first.")

        docs: list[CorpusDocument] = []
        if corpus_path.suffix == ".jsonl":
            with open(corpus_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        docs.append(
                            CorpusDocument(
                                doc_id=str(item.get("id", item.get("doc_id", ""))),
                                title=item.get("title", ""),
                                text=item.get("text", item.get("passage", "")),
                            )
                        )
        else:
            with open(corpus_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    docs.append(
                        CorpusDocument(
                            doc_id=str(item.get("id", item.get("doc_id", ""))),
                            title=item.get("title", ""),
                            text=item.get("text", item.get("passage", "")),
                        )
                    )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        queries_path = self.dataset_dir / "queries.json"
        if not queries_path.exists():
            queries_path = self.dataset_dir / "queries.jsonl"
        if not queries_path.exists():
            raise FileNotFoundError(f"MultiHop queries missing in {self.dataset_dir}. Run setup first.")

        queries: list[BenchmarkQuery] = []
        if queries_path.suffix == ".jsonl":
            with open(queries_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        queries.append(
                            BenchmarkQuery(
                                query_id=str(item.get("id", item.get("query_id", ""))),
                                text=item.get("question", item.get("query", "")),
                                gold_doc_ids=[str(x) for x in item.get("evidence_doc_ids", item.get("gold_ids", []))],
                                category="multi_hop",
                                metadata={"hop_count": len(item.get("evidence_doc_ids", item.get("gold_ids", [])))},
                            )
                        )
        else:
            with open(queries_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    queries.append(
                        BenchmarkQuery(
                            query_id=str(item.get("id", item.get("query_id", ""))),
                            text=item.get("question", item.get("query", "")),
                            gold_doc_ids=[str(x) for x in item.get("evidence_doc_ids", item.get("gold_ids", []))],
                            category="multi_hop",
                            metadata={"hop_count": len(item.get("evidence_doc_ids", item.get("gold_ids", [])))},
                        )
                    )
        return queries
