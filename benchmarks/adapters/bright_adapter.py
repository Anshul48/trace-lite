"""BRIGHT dataset adapter for reasoning-intensive hierarchical retrieval."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class BrightAdapter(BaseDatasetAdapter):
    """
    Adapter for BRIGHT reasoning-intensive benchmarks (e.g., coding, biology, earth_science, economics).
    Loads reasoning queries with multi-level candidates.
    """

    def __init__(self, task: str, cache_dir: Path | str):
        super().__init__(cache_dir)
        self.task = task.lower()
        self.dataset_dir = self.cache_dir / f"bright_{self.task}"

    def dataset_name(self) -> str:
        return f"bright-{self.task}"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        # Note: If external HuggingFace datasets package is available, can fetch bright/{task}
        # Otherwise reads from pre-cached files in dataset_dir

    def load_corpus(self) -> list[CorpusDocument]:
        corpus_path = self.dataset_dir / "documents.json"
        if not corpus_path.exists():
            corpus_path = self.dataset_dir / "corpus.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"BRIGHT documents file missing in {self.dataset_dir}. Run setup first.")

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
                                text=item.get("text", item.get("content", "")),
                                metadata=item.get("metadata", {}),
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
                            text=item.get("text", item.get("content", "")),
                            metadata=item.get("metadata", {}),
                        )
                    )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        queries_path = self.dataset_dir / "queries.json"
        if not queries_path.exists():
            queries_path = self.dataset_dir / "queries.jsonl"
        if not queries_path.exists():
            raise FileNotFoundError(f"BRIGHT queries file missing in {self.dataset_dir}. Run setup first.")

        queries: list[BenchmarkQuery] = []
        if queries_path.suffix == ".jsonl":
            with open(queries_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        queries.append(
                            BenchmarkQuery(
                                query_id=str(item.get("id", item.get("query_id", ""))),
                                text=item.get("query", item.get("text", "")),
                                gold_doc_ids=[str(x) for x in item.get("gold_ids", item.get("gold_doc_ids", []))],
                                category="cross_domain",
                                metadata=item.get("metadata", {}),
                            )
                        )
        else:
            with open(queries_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    queries.append(
                        BenchmarkQuery(
                            query_id=str(item.get("id", item.get("query_id", ""))),
                            text=item.get("query", item.get("text", "")),
                            gold_doc_ids=[str(x) for x in item.get("gold_ids", item.get("gold_doc_ids", []))],
                            category="cross_domain",
                            metadata=item.get("metadata", {}),
                        )
                    )
        return queries
