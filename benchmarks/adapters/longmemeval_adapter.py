"""LongMemEval adapter for long-range temporal updates and multi-session memory."""

import json
from pathlib import Path
from benchmarks.adapters.base import BaseDatasetAdapter, CorpusDocument, BenchmarkQuery


class LongMemEvalAdapter(BaseDatasetAdapter):
    """
    Adapter for LongMemEval and MemoryAgentBench suites.
    Evaluates temporal updates, historical / as-of state retrieval, and selective forgetting.
    """

    def __init__(self, cache_dir: Path | str):
        super().__init__(cache_dir)
        self.dataset_dir = self.cache_dir / "longmemeval"

    def dataset_name(self) -> str:
        return "longmemeval"

    def download_or_prepare(self) -> None:
        self.dataset_dir.mkdir(parents=True, exist_ok=True)

    def load_corpus(self) -> list[CorpusDocument]:
        corpus_path = self.dataset_dir / "sessions.jsonl"
        if not corpus_path.exists():
            raise FileNotFoundError(f"LongMemEval sessions missing in {self.dataset_dir}. Run setup first.")

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
        queries_path = self.dataset_dir / "eval_questions.jsonl"
        if not queries_path.exists():
            raise FileNotFoundError(f"LongMemEval questions missing in {self.dataset_dir}. Run setup first.")

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
                            text=item.get("question", ""),
                            gold_doc_ids=[str(x) for x in item.get("evidence_session_ids", [])],
                            category=mapped_category,
                            metadata={"as_of_date": item.get("as_of_date")},
                        )
                    )
        return queries
