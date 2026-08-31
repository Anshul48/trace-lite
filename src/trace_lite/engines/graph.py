"""Personalized PageRank Graph Activation Engine and Deterministic Edge Generation for Trace-Lite."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from trace_lite.cortex.forest import TreeNode
    from trace_lite.spine.models import Atom


class GraphActivationEngine:
    """Spreads activation energy from query seed matches across typed graph edges."""

    def __init__(
        self,
        damping: float = 0.85,
        max_iterations: int = 20,
        convergence_epsilon: float = 1e-8,
    ):
        self.damping = damping
        self.max_iterations = max_iterations
        self.convergence_epsilon = convergence_epsilon

    def personalized_pagerank(
        self,
        seeds: dict[str, float],  # {node_id: initial_normalized_score}
        adjacency: dict[str, list[tuple[str, float]]],  # {source: [(target, weight)]}
    ) -> dict[str, float]:
        """Compute Personalized PageRank scores via sparse power-iteration."""
        if not seeds or not adjacency:
            return {}

        total_seed = sum(seeds.values()) or 1.0
        personalization = {node: score / total_seed for node, score in seeds.items()}
        current = dict(personalization)
        nodes = set(adjacency.keys()) | set(seeds.keys())
        for src_node, edges in adjacency.items():
            for dst, _ in edges:
                nodes.add(dst)

        for _ in range(self.max_iterations):
            following = {
                node: (1.0 - self.damping) * personalization.get(node, 0.0)
                for node in nodes
            }
            for src, val in current.items():
                edges = adjacency.get(src, [])
                if not edges:
                    following[src] = following.get(src, 0.0) + (self.damping * val)
                    continue
                normalizer = sum(w for _, w in edges) or 1.0
                for dst, w in edges:
                    following[dst] = following.get(dst, 0.0) + (
                        self.damping * val * (w / normalizer)
                    )

            delta = sum(abs(following[node] - current.get(node, 0.0)) for node in nodes)
            current = following
            if delta < self.convergence_epsilon:
                break

        return current


# Standard English stopwords
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "with",
    "by", "about", "against", "between", "into", "through", "during", "before",
    "after", "above", "below", "from", "up", "down", "of", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why",
    "how", "all", "any", "both", "each", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "s", "t", "can", "will", "just", "don", "should", "now", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "having", "do", "does",
    "did", "doing", "this", "that", "these", "those", "it", "its", "as", "what",
    "which", "who", "whom", "also", "into", "their", "they", "them", "which",
}


def extract_distinct_terms(text: str) -> set[str]:
    """Extract distinct non-stopword technical keywords / entities from text."""
    tokens = re.findall(r"[A-Za-z0-9_#\-\.]{3,}", text)
    terms = set()
    for tok in tokens:
        cleaned = tok.strip(".-_").lower()
        if len(cleaned) >= 3 and cleaned not in _STOPWORDS and not cleaned.isdigit():
            terms.add(cleaned)
    return terms


def generate_sequential_edges(atoms: Sequence[Atom]) -> list[dict]:
    """Generate deterministic NEXT and PREV edges between consecutive atoms in the same artifact."""
    by_artifact: dict[str, list[Atom]] = defaultdict(list)
    for a in atoms:
        by_artifact[a.source_artifact_id].append(a)

    edges: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for art_id, art_atoms in by_artifact.items():
        sorted_atoms = sorted(art_atoms, key=lambda a: a.sequence_index)
        for i in range(len(sorted_atoms) - 1):
            a_cur = sorted_atoms[i]
            a_nxt = sorted_atoms[i + 1]
            edges.append({
                "edge_id": f"seq-next-{a_cur.atom_id}-{a_nxt.atom_id}",
                "source_atom_id": a_cur.atom_id,
                "target_atom_id": a_nxt.atom_id,
                "relation_type": "NEXT",
                "weight": 1.0,
                "confidence": 1.0,
                "created_at": now,
            })
            edges.append({
                "edge_id": f"seq-prev-{a_nxt.atom_id}-{a_cur.atom_id}",
                "source_atom_id": a_nxt.atom_id,
                "target_atom_id": a_cur.atom_id,
                "relation_type": "PREV",
                "weight": 1.0,
                "confidence": 1.0,
                "created_at": now,
            })
    return edges


def generate_hierarchical_edges(nodes: Sequence[TreeNode]) -> list[dict]:
    """Generate bi-directional PARENT_OF and CHILD_OF edges between parent nodes and child nodes/atoms."""
    edges: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for node in nodes:
        if node.children_ids:
            for child_id in node.children_ids:
                edges.append({
                    "edge_id": f"hier-parent-{node.node_id}-{child_id}",
                    "source_atom_id": node.node_id,
                    "target_atom_id": child_id,
                    "relation_type": "PARENT_OF",
                    "weight": 1.0,
                    "confidence": 1.0,
                    "created_at": now,
                })
                edges.append({
                    "edge_id": f"hier-child-{child_id}-{node.node_id}",
                    "source_atom_id": child_id,
                    "target_atom_id": node.node_id,
                    "relation_type": "CHILD_OF",
                    "weight": 1.0,
                    "confidence": 1.0,
                    "created_at": now,
                })
        if node.level >= 1 and node.atom_ids:
            for aid in node.atom_ids:
                edges.append({
                    "edge_id": f"hier-summary-atom-{node.node_id}-{aid}",
                    "source_atom_id": node.node_id,
                    "target_atom_id": aid,
                    "relation_type": "PARENT_OF",
                    "weight": 0.8,
                    "confidence": 1.0,
                    "created_at": now,
                })
                edges.append({
                    "edge_id": f"hier-atom-summary-{aid}-{node.node_id}",
                    "source_atom_id": aid,
                    "target_atom_id": node.node_id,
                    "relation_type": "CHILD_OF",
                    "weight": 0.8,
                    "confidence": 1.0,
                    "created_at": now,
                })
    return edges


def generate_co_occurrence_edges(
    atoms: Sequence[Atom], max_atom_frequency_ratio: float = 0.5
) -> list[dict]:
    """Generate CO_OCCURS edges between atoms sharing distinct keywords/entities."""
    if not atoms:
        return []
    atom_terms: dict[str, set[str]] = {}
    term_to_atoms: dict[str, set[str]] = defaultdict(set)
    for a in atoms:
        terms = extract_distinct_terms(a.content)
        atom_terms[a.atom_id] = terms
        for t in terms:
            term_to_atoms[t].add(a.atom_id)

    max_freq = max(2, int(len(atoms) * max_atom_frequency_ratio))
    informative_terms = {
        t: aids for t, aids in term_to_atoms.items()
        if 2 <= len(aids) <= max_freq
    }

    pair_weights: dict[tuple[str, str], float] = defaultdict(float)
    for t, aids in informative_terms.items():
        term_weight = 1.0 / (len(aids) ** 0.5)
        aid_list = sorted(aids)
        for i in range(len(aid_list)):
            for j in range(i + 1, len(aid_list)):
                a1 = aid_list[i]
                a2 = aid_list[j]
                pair_weights[(a1, a2)] += term_weight

    edges: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    for (a1, a2), weight in pair_weights.items():
        if weight > 0.1:
            w = min(1.0, float(weight))
            edges.append({
                "edge_id": f"co-occur-{a1}-{a2}",
                "source_atom_id": a1,
                "target_atom_id": a2,
                "relation_type": "CO_OCCURS",
                "weight": w,
                "confidence": 1.0,
                "created_at": now,
            })
            edges.append({
                "edge_id": f"co-occur-{a2}-{a1}",
                "source_atom_id": a2,
                "target_atom_id": a1,
                "relation_type": "CO_OCCURS",
                "weight": w,
                "confidence": 1.0,
                "created_at": now,
            })
    return edges


def generate_all_edges(
    atoms: Sequence[Atom], nodes: Sequence[TreeNode] | None = None
) -> list[dict]:
    """Combine sequential, hierarchical, and term co-occurrence edges."""
    edges: list[dict] = []
    if atoms:
        edges.extend(generate_sequential_edges(atoms))
        edges.extend(generate_co_occurrence_edges(atoms))
    if nodes:
        edges.extend(generate_hierarchical_edges(nodes))
    return edges
