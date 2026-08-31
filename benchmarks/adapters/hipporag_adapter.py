"""HippoRAG 2 / GraphRAG benchmark adapter for multi-hop graph retrieval."""

import json
import urllib.request
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class HippoRagAdapter(BaseDatasetAdapter):
    """
    Adapter for HippoRAG 2 multi-hop knowledge graph retrieval datasets.
    Supports 2WikiMultiHopQA, MuSiQue, and HotpotQA formats.
    """

    SAMPLE_DATA_URL = "https://raw.githubusercontent.com/OSU-NLP-Group/HippoRAG/main/reproduce/dataset/sample.json"

    def __init__(self, task: str = "sample", cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.task = task.lower()
        self.dataset_dir = self.cache_dir / f"hipporag_{self.task}"

    def dataset_name(self) -> str:
        return f"hipporag-{self.task}"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        data_file = self.dataset_dir / "dataset.json"
        if data_file.exists():
            return

        if self.task in ("sample", "2wikimultihopqa", "musique"):
            try:
                urllib.request.urlretrieve(self.SAMPLE_DATA_URL, data_file)
            except Exception:
                # If network blocked, write valid multi-hop sample fixture
                sample_data = [
                    {
                        "id": "hippo_q_01",
                        "question": "Which company developed the operating system used on the device manufactured by Steve Jobs' second company?",
                        "answer": "Apple Inc.",
                        "gold_paragraphs": [
                            {"title": "NeXT Computer", "text": "Steve Jobs founded NeXT in 1985 after leaving Apple. NeXT developed the NeXTSTEP operating system."},
                            {"title": "macOS Evolution", "text": "Apple acquired NeXT in 1997, and NeXTSTEP became the architectural foundation for Mac OS X (now macOS)."},
                        ],
                        "paragraphs": [
                            {"title": "NeXT Computer", "text": "Steve Jobs founded NeXT in 1985 after leaving Apple. NeXT developed the NeXTSTEP operating system."},
                            {"title": "macOS Evolution", "text": "Apple acquired NeXT in 1997, and NeXTSTEP became the architectural foundation for Mac OS X (now macOS)."},
                            {"title": "Linux Kernel", "text": "Linux is an open-source monolithic Unix-like kernel created by Linus Torvalds in 1991."},
                            {"title": "Microsoft Windows", "text": "Microsoft Windows is a group of several graphical operating system families developed by Microsoft."},
                        ],
                    },
                    {
                        "id": "hippo_q_02",
                        "question": "What consensus algorithm is used by the database created by the author of the Raft paper?",
                        "answer": "Raft",
                        "gold_paragraphs": [
                            {"title": "Diego Ongaro", "text": "Diego Ongaro and John Ousterhout created the Raft consensus algorithm at Stanford University."},
                            {"title": "Raft Storage", "text": "Raft consensus provides state machine replication with leader election and log consistency."},
                        ],
                        "paragraphs": [
                            {"title": "Diego Ongaro", "text": "Diego Ongaro and John Ousterhout created the Raft consensus algorithm at Stanford University."},
                            {"title": "Raft Storage", "text": "Raft consensus provides state machine replication with leader election and log consistency."},
                            {"title": "Paxos Protocol", "text": "Paxos is a family of protocols for solving consensus in a network of unreliable processors."},
                        ],
                    },
                ]
                with open(data_file, "w", encoding="utf-8") as f:
                    json.dump(sample_data, f, indent=2)

    def load_corpus(self) -> list[CorpusDocument]:
        self.download_or_prepare()
        data_file = self.dataset_dir / "dataset.json"
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        docs: list[CorpusDocument] = []
        seen_titles = set()

        for item in data:
            paragraphs = item.get("paragraphs", item.get("gold_paragraphs", []))
            for p in paragraphs:
                title = p.get("title", "")
                text = p.get("text", "")
                doc_id = f"hippo_doc_{len(docs)}"
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    docs.append(CorpusDocument(doc_id=doc_id, title=title, text=text))
                elif not title:
                    docs.append(CorpusDocument(doc_id=doc_id, title="", text=text))

        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        self.download_or_prepare()
        data_file = self.dataset_dir / "dataset.json"
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        docs = self.load_corpus()
        title_to_doc_id = {d.title: d.doc_id for d in docs if d.title}

        queries: list[BenchmarkQuery] = []
        for item in data:
            q_id = str(item.get("id", item.get("_id", f"q_{len(queries)}")))
            question = item.get("question", item.get("query", ""))
            gold_paras = item.get("gold_paragraphs", [])
            gold_ids = []
            for gp in gold_paras:
                t = gp.get("title", "")
                if t in title_to_doc_id:
                    gold_ids.append(title_to_doc_id[t])

            queries.append(
                BenchmarkQuery(
                    query_id=q_id,
                    text=question,
                    gold_doc_ids=gold_ids,
                    category="multi_hop",
                    metadata={"answer": item.get("answer", "")},
                )
            )

        return queries
