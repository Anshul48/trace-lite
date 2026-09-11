"""Reciprocal Rank Fusion for the Tier 3 flat hybrid fallback."""

from __future__ import annotations

K = 60


def rrf_fuse(
    dense_ranked: list[int], sparse_ranked: list[int], k: int = K
) -> list[tuple[int, float]]:
    """Fuse two rank lists. Returns (atom_id, fused_score) sorted best-first, stable."""
    scores: dict[int, float] = {}
    for rank, atom_id in enumerate(dense_ranked):
        scores[atom_id] = scores.get(atom_id, 0.0) + 1.0 / (k + rank)
    for rank, atom_id in enumerate(sparse_ranked):
        scores[atom_id] = scores.get(atom_id, 0.0) + 1.0 / (k + rank)
    order = {aid: i for i, aid in enumerate(dense_ranked + sparse_ranked)}
    return sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))
