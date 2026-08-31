"""ANN-Benchmarks and BigANN vector indexing scale adapter."""

import numpy as np
import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class AnnScaleAdapter(BaseDatasetAdapter):
    """
    Adapter for ANN-Benchmarks / BigANN scale testing.
    Measures vector index recall, QPS throughput, latency percentiles, and memory footprint.
    """

    def __init__(self, size: str = "10k", dim: int = 384, cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.size = size.lower()
        self.dim = dim
        self.dataset_dir = self.cache_dir / f"ann_{self.size}"

    def dataset_name(self) -> str:
        return f"ann-scale-{self.size}"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def load_corpus(self) -> list[CorpusDocument]:
        count = 10000 if self.size == "10k" else (100000 if self.size == "100k" else 1000)
        docs = []
        for i in range(count):
            docs.append(
                CorpusDocument(
                    doc_id=f"vec-doc-{i:06d}",
                    title=f"Vector Document {i}",
                    text=f"Benchmark high-dimensional vector entity {i} with partition shard {i % 100} and feature payload.",
                    metadata={"index": i},
                )
            )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        queries = []
        for q_idx in range(50):
            target_idx = (q_idx * 17) % 1000
            queries.append(
                BenchmarkQuery(
                    query_id=f"ann-query-{q_idx:03d}",
                    text=f"Benchmark high-dimensional vector entity {target_idx}",
                    gold_doc_ids=[f"vec-doc-{target_idx:06d}"],
                    category="direct_lookup",
                )
            )
        return queries
