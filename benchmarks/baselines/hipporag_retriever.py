"""HippoRAG Personalized PageRank (PPR) Graph-Based Baseline Retriever."""

import re
import numpy as np
from collections import Counter
from benchmarks.baselines.base import BaseRetriever, IndexedDocument, RetrievalCandidate
from benchmarks.baselines.dense_retriever import DenseRetriever


def extract_entities(text: str) -> list[str]:
    """Extract candidate entity noun phrases and capitalized terms."""
    # Matches capitalized sequences and alphanumeric key phrases
    phrases = re.findall(r"\b[A-Z][a-zA-Z0-9_-]*(?:\s+[A-Z][a-zA-Z0-9_-]*)*\b", text)
    tokens = re.findall(r"\b[a-zA-Z0-9_]{3,}\b", text.lower())
    return [p.lower() for p in phrases] + tokens


class HippoRagPPRRetriever(BaseRetriever):
    """
    Graph-based associative memory retriever inspired by HippoRAG.
    Constructs an entity-document bipartite graph and computes Personalized PageRank (PPR).
    """

    def __init__(self, damping: float = 0.85, max_iter: int = 20, tol: float = 1e-4):
        self.damping = damping
        self.max_iter = max_iter
        self.tol = tol
        self.documents: list[IndexedDocument] = []
        self.entities: list[str] = []
        self.entity_to_idx: dict[str, int] = {}
        self.doc_entity_adj: list[list[int]] = []  # doc_idx -> list of entity_indices
        self.entity_doc_adj: list[list[int]] = []  # entity_idx -> list of doc_indices
        self.dense = DenseRetriever(model_name="all-MiniLM-L6-v2")

    def name(self) -> str:
        return "hipporag_ppr"

    def index(self, documents: list[IndexedDocument]) -> None:
        self.documents = list(documents)
        n_docs = len(self.documents)
        if n_docs == 0:
            return

        self.dense.index(documents)

        # 1. Extract entities and build graph
        all_entities = set()
        doc_entities_raw: list[list[str]] = []
        for doc in self.documents:
            ents = extract_entities(doc.title + " " + doc.text)
            doc_entities_raw.append(ents)
            all_entities.update(ents)

        self.entities = sorted(list(all_entities))
        self.entity_to_idx = {e: idx for idx, e in enumerate(self.entities)}
        n_entities = len(self.entities)

        self.doc_entity_adj = [[] for _ in range(n_docs)]
        self.entity_doc_adj = [[] for _ in range(n_entities)]

        for d_idx, ents in enumerate(doc_entities_raw):
            for e in ents:
                e_idx = self.entity_to_idx[e]
                self.doc_entity_adj[d_idx].append(e_idx)
                self.entity_doc_adj[e_idx].append(d_idx)

    def retrieve(self, query: str, top_k: int = 10) -> list[RetrievalCandidate]:
        n_docs = len(self.documents)
        n_entities = len(self.entities)
        if n_docs == 0 or n_entities == 0:
            return []

        # 1. Query entity linking / seed activation
        q_ents = extract_entities(query)
        seed_entity_indices = [self.entity_to_idx[e] for e in q_ents if e in self.entity_to_idx]

        # If no explicit entity overlap, fallback to dense seeds
        if not seed_entity_indices:
            dense_cands = self.dense.retrieve(query, top_k=min(5, n_docs))
            if not dense_cands:
                return []
            p_docs = {c.doc_id: c.score for c in dense_cands}
            return dense_cands[:top_k]

        # 2. Personalized PageRank random walk on bipartite graph
        # Total nodes = n_entities + n_docs
        # Vector p of size (n_entities + n_docs)
        total_nodes = n_entities + n_docs
        p = np.zeros(total_nodes, dtype=np.float32)
        seed_prob = 1.0 / len(seed_entity_indices)
        for e_idx in seed_entity_indices:
            p[e_idx] = seed_prob

        p_teleport = p.copy()

        for _ in range(self.max_iter):
            p_next = np.zeros_like(p)

            # Flow from entities to docs
            for e_idx in range(n_entities):
                deg = len(self.entity_doc_adj[e_idx])
                if deg > 0:
                    flow = (p[e_idx] * self.damping) / deg
                    for d_idx in self.entity_doc_adj[e_idx]:
                        p_next[n_entities + d_idx] += flow

            # Flow from docs to entities
            for d_idx in range(n_docs):
                deg = len(self.doc_entity_adj[d_idx])
                if deg > 0:
                    flow = (p[n_entities + d_idx] * self.damping) / deg
                    for e_idx in self.doc_entity_adj[d_idx]:
                        p_next[e_idx] += flow

            # Teleportation jump
            p_next += (1.0 - self.damping) * p_teleport
            norm = np.sum(p_next)
            if norm > 0:
                p_next /= norm

            if np.linalg.norm(p_next - p) < self.tol:
                p = p_next
                break
            p = p_next

        # 3. Extract final doc scores
        doc_scores = p[n_entities:]
        top_indices = np.argsort(-doc_scores)[:top_k]

        results: list[RetrievalCandidate] = []
        for idx in top_indices:
            score_val = float(doc_scores[idx])
            doc = self.documents[idx]
            results.append(
                RetrievalCandidate(
                    doc_id=doc.doc_id,
                    score=score_val,
                    text=doc.text,
                    metadata={"ppr_rank": True, **doc.metadata},
                )
            )
        return results
