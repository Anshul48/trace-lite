"""BEIR dataset adapter for broad zero-shot retrieval benchmarking."""

import csv
import json
import urllib.request
import zipfile
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery, download_file


class BeirAdapter(BaseDatasetAdapter):
    """
    Adapter for BEIR benchmark datasets (e.g., scifact, nfcorpus, fiqa, arguana).
    Parses corpus.jsonl, queries.jsonl, and qrels/test.tsv.
    """

    BEIR_BASE_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{dataset}.zip"

    def __init__(self, dataset: str, cache_dir: Path | str):
        super().__init__(cache_dir)
        self.dataset = dataset.lower()
        self.dataset_dir = self.cache_dir / self.dataset

    def dataset_name(self) -> str:
        return f"beir-{self.dataset}"

    def download_or_prepare(self) -> None:
        if self.dataset_dir.exists() and (self.dataset_dir / "corpus.jsonl").exists():
            return

        zip_path = self.cache_dir / f"{self.dataset}.zip"
        url = self.BEIR_BASE_URL.format(dataset=self.dataset)
        if not zip_path.exists():
            try:
                download_file(url, zip_path)
            except Exception as e:
                raise RuntimeError(f"Failed to download BEIR dataset '{self.dataset}' from {url}: {e}")

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(self.cache_dir)


    def load_corpus(self) -> list[CorpusDocument]:
        corpus_path = self.dataset_dir / "corpus.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"BEIR corpus file not found: {corpus_path}. Run setup first.")

        docs: list[CorpusDocument] = []
        with open(corpus_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                docs.append(
                    CorpusDocument(
                        doc_id=str(item.get("_id", item.get("id", ""))),
                        title=item.get("title", ""),
                        text=item.get("text", ""),
                        metadata=item.get("metadata", {}),
                    )
                )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        queries_path = self.dataset_dir / "queries.jsonl"
        qrels_path = self.dataset_dir / "qrels" / "test.tsv"
        if not qrels_path.exists():
            qrels_path = self.dataset_dir / "qrels" / "dev.tsv"

        if not queries_path.exists() or not qrels_path.exists():
            raise FileNotFoundError(f"BEIR queries or qrels missing in {self.dataset_dir}. Run setup first.")

        # Load query texts
        query_texts: dict[str, str] = {}
        with open(queries_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                q_id = str(item.get("_id", item.get("id", "")))
                query_texts[q_id] = item.get("text", "")

        # Load qrels
        query_golds: dict[str, list[str]] = {}
        query_scores: dict[str, dict[str, int]] = {}
        with open(qrels_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, None)  # query-id, corpus-id, score
            for row in reader:
                if len(row) < 3:
                    continue
                q_id, doc_id, score_str = row[0], row[1], row[2]
                score = int(score_str)
                if score > 0:
                    query_golds.setdefault(q_id, []).append(doc_id)
                    query_scores.setdefault(q_id, {})[doc_id] = score

        queries: list[BenchmarkQuery] = []
        for q_id, gold_ids in query_golds.items():
            if q_id in query_texts:
                queries.append(
                    BenchmarkQuery(
                        query_id=q_id,
                        text=query_texts[q_id],
                        gold_doc_ids=gold_ids,
                        relevance_scores=query_scores.get(q_id, {}),
                        category="direct_lookup",
                    )
                )
        return queries
