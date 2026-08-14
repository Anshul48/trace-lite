"""LongMemEval adapter for long-range temporal updates and multi-session memory."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class LongMemEvalAdapter(BaseDatasetAdapter):
    """
    Adapter for LongMemEval and MemoryAgentBench suites.
    Evaluates temporal updates, historical / as-of state retrieval, and selective forgetting.
    """

    def __init__(self, cache_dir: Path | str = "benchmarks/cache"):
        super().__init__(cache_dir)
        self.dataset_dir = self.cache_dir / "longmemeval"

    def dataset_name(self) -> str:
        return "longmemeval"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)
        corpus_path = self.dataset_dir / "sessions.jsonl"
        queries_path = self.dataset_dir / "eval_questions.jsonl"

        if not corpus_path.exists():
            sample_sessions = [
                {
                    "id": "session-2026-01-10",
                    "session_date": "2026-01-10",
                    "conversation_text": "Team decided to use LZ4 compression algorithm for L0 SSTables with 4KB block sizes.",
                    "timestamp": 1768000000,
                },
                {
                    "id": "session-2026-02-15",
                    "session_date": "2026-02-15",
                    "conversation_text": "Updated compression policy: Cold archival levels L5 and L6 now switch to Zstandard level 3.",
                    "timestamp": 1771100000,
                },
            ]
            with open(corpus_path, "w", encoding="utf-8") as f:
                for s in sample_sessions:
                    f.write(json.dumps(s) + "\n")

        if not queries_path.exists():
            sample_questions = [
                {
                    "id": "lme-q-01",
                    "question": "What compression algorithm was chosen in January 2026 for L0 SSTables?",
                    "gold_session_ids": ["session-2026-01-10"],
                    "task_type": "historical_retrieval",
                },
                {
                    "id": "lme-q-02",
                    "question": "What was the updated compression policy enacted in February 2026?",
                    "gold_session_ids": ["session-2026-02-15"],
                    "task_type": "update_tracking",
                },
            ]
            with open(queries_path, "w", encoding="utf-8") as f:
                for q in sample_questions:
                    f.write(json.dumps(q) + "\n")

    def load_corpus(self) -> list[CorpusDocument]:
        self.download_or_prepare()
        corpus_path = self.dataset_dir / "sessions.jsonl"
        docs: list[CorpusDocument] = []
        with open(corpus_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    docs.append(
                        CorpusDocument(
                            doc_id=str(item.get("id", item.get("session_id", ""))),
                            title=item.get("session_date", ""),
                            text=item.get("conversation_text", item.get("text", "")),
                            metadata={"timestamp": item.get("timestamp")},
                        )
                    )
        return docs

    def load_queries(self) -> list[BenchmarkQuery]:
        self.download_or_prepare()
        queries_path = self.dataset_dir / "eval_questions.jsonl"
        queries: list[BenchmarkQuery] = []
        with open(queries_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    category_str = item.get("task_type", "historical")
                    mapped_category = "historical"
                    if "abstention" in category_str.lower():
                        mapped_category = "out_of_scope"
                    elif "update" in category_str.lower():
                        mapped_category = "contradiction"

                    queries.append(
                        BenchmarkQuery(
                            query_id=str(item.get("id", item.get("question_id", ""))),
                            text=item.get("question", item.get("query", "")),
                            gold_doc_ids=[str(x) for x in item.get("gold_session_ids", [])],
                            category=mapped_category,
                            metadata={"task_type": category_str},
                        )
                    )
        return queries
