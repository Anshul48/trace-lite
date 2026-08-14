"""HiCBench / HiChunk adapter for hierarchical chunk and multi-level retrieval."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class HiChunkAdapter(BaseDatasetAdapter):
    """
    Adapter for HiCBench / HiChunk datasets.
    Evaluates multi-level tree chunking, granular atom boundaries, and evidence-dense retrieval.
    """

    def __init__(self, cache_dir: Path | str):
        super().__init__(cache_dir)
        self.dataset_dir = self.cache_dir / "hichunk"

    def dataset_name(self) -> str:
        return "hichunk-benchmark"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def load_corpus(self) -> list[CorpusDocument]:
        corpus_path = self.dataset_dir / "corpus.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"HiChunk corpus missing in {self.dataset_dir}. Run setup first.")

        docs: list[CorpusDocument] = []
        with open(corpus_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    docs.append(
                        CorpusDocument(
                            doc_id=str(item.get("id", item.get("chunk_id", ""))),
                            title=item.get("section_title", item.get("title", "")),
                            text=item.get("content", item.get("text", "")),
                            metadata={
                                "level": item.get("hierarchy_level", 0),
                                "parent_id": item.get("parent_id"),
                            },
                        )
                    )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        queries_path = self.dataset_dir / "queries.jsonl"
        if not queries_path.exists():
            raise FileNotFoundError(f"HiChunk queries missing in {self.dataset_dir}. Run setup first.")

        queries: list[BenchmarkQuery] = []
        with open(queries_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    queries.append(
                        BenchmarkQuery(
                            query_id=str(item.get("id", item.get("query_id", ""))),
                            text=item.get("query", item.get("question", "")),
                            gold_doc_ids=[str(x) for x in item.get("target_chunk_ids", item.get("gold_ids", []))],
                            category="global_context",
                            metadata={"granularity": item.get("granularity", "leaf")},
                        )
                    )
        return queries
