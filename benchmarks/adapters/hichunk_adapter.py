"""HiCBench / HiChunk adapter for hierarchical chunk and multi-level retrieval."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class HiChunkAdapter(BaseDatasetAdapter):
    """
    Adapter for HiCBench / HiChunk datasets.
    Evaluates multi-level tree chunking, granular atom boundaries, and evidence-dense retrieval.
    """

    def __init__(self, cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.dataset_dir = self.cache_dir / "hichunk"

    def dataset_name(self) -> str:
        return "hichunk-benchmark"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        corpus_path = self.dataset_dir / "corpus.jsonl"
        queries_path = self.dataset_dir / "queries.jsonl"

        if not corpus_path.exists():
            sample_chunks = [
                {
                    "id": "hic-sec-01",
                    "section_title": "Storage Engine: Write-Ahead Log",
                    "content": "The Write-Ahead Log (WAL) records every transaction commit before flushing to in-memory memtables.",
                    "hierarchy_level": 1,
                    "parent_id": None,
                },
                {
                    "id": "hic-subsec-01-01",
                    "section_title": "WAL Direct IO and Durability",
                    "content": "WAL direct IO uses O_DIRECT and fdatasync to guarantee crash consistency across power failure events.",
                    "hierarchy_level": 2,
                    "parent_id": "hic-sec-01",
                },
                {
                    "id": "hic-sec-02",
                    "section_title": "Memory Substrate: Immutable Spine",
                    "content": "The source spine stores raw byte payloads and assigns immutable SHA-256 provenance hashes.",
                    "hierarchy_level": 1,
                    "parent_id": None,
                },
                {
                    "id": "hic-subsec-02-01",
                    "section_title": "Hierarchical Tree Summaries",
                    "content": "RAPTOR summarization organizes leaf atoms into multi-level summary nodes using agglomerative clustering.",
                    "hierarchy_level": 2,
                    "parent_id": "hic-sec-02",
                },
            ]
            with open(corpus_path, "w", encoding="utf-8") as f:
                for c in sample_chunks:
                    f.write(json.dumps(c) + "\n")

        if not queries_path.exists():
            sample_queries = [
                {
                    "id": "hic-q-01",
                    "query": "How does WAL direct IO ensure durability on power loss?",
                    "gold_chunk_ids": ["hic-subsec-01-01"],
                    "category": "direct_lookup",
                },
                {
                    "id": "hic-q-02",
                    "query": "What clustering method is used for hierarchical tree summaries?",
                    "gold_chunk_ids": ["hic-subsec-02-01"],
                    "category": "global_context",
                },
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
                            text=item.get("query", item.get("question", "")),
                            gold_doc_ids=[str(x) for x in item.get("gold_chunk_ids", item.get("gold_ids", []))],
                            category="direct_lookup",
                            metadata={"evidence_type": "hierarchical_chunk"},
                        )
                    )
        return queries
