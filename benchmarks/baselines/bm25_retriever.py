"""BM25 Lexical Baseline Retriever (with pure-Python fallback)."""

import math
import re
from collections import Counter
from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer."""
    return re.findall(r"\b[a-zA-Z0-9_]+\b", text.lower())


class BM25Retriever(BaseRetriever):
    """
    Standard Okapi BM25 lexical retriever.
    Uses rank_bm25 if available, with a fast, self-contained pure-Python fallback.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: list[IndexedDocument] = []
        self.doc_len: list[int] = []
        self.avg_doc_len: float = 0.0
        self.doc_freqs: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.tf: list[dict[str, int]] = []
        self.num_docs: int = 0

    def name(self) -> str:
        return "bm25"

    def index(self, documents: list[IndexedDocument]) -> None:
        self.documents = list(documents)
        self.num_docs = len(self.documents)
        if self.num_docs == 0:
            return

        self.doc_len = []
        self.tf = []
        self.doc_freqs = Counter()

        total_len = 0
        for doc in self.documents:
            tokens = tokenize(doc.text + " " + doc.title)
            t_len = len(tokens)
            self.doc_len.append(t_len)
            total_len += t_len

            counts = Counter(tokens)
            self.tf.append(dict(counts))
            for term in counts:
                self.doc_freqs[term] += 1

        self.avg_doc_len = total_len / self.num_docs if self.num_docs > 0 else 0.0

        # Compute Robertson-Spärck Jones IDF with smoothing
        self.idf = {}
        for term, df in self.doc_freqs.items():
            self.idf[term] = math.log(1.0 + (self.num_docs - df + 0.5) / (df + 0.5))

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        if self.num_docs == 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores = [0.0] * self.num_docs
        for token in query_tokens:
            if token not in self.idf:
                continue
            idf_val = self.idf[token]
            for i in range(self.num_docs):
                doc_tf = self.tf[i].get(token, 0)
                if doc_tf > 0:
                    L = self.doc_len[i] / self.avg_doc_len if self.avg_doc_len > 0 else 1.0
                    num = doc_tf * (self.k1 + 1.0)
                    denom = doc_tf + self.k1 * (1.0 - self.b + self.b * L)
                    scores[i] += idf_val * (num / denom)

        ranked_indices = sorted(range(self.num_docs), key=lambda idx: scores[idx], reverse=True)
        results: list[RetrievalCandidate] = []
        for idx in ranked_indices[:top_k]:
            if scores[idx] > 0.0:
                doc = self.documents[idx]
                results.append(
                    RetrievalCandidate(
                        doc_id=doc.doc_id,
                        score=scores[idx],
                        text=doc.text,
                        metadata=doc.metadata,
                    )
                )
        return results
