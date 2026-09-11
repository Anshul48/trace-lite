"""Tier 2: top-down faceted beam search over warmed in-memory centroids (<= 35ms)."""

from __future__ import annotations

import sqlite3

from ..filing.engine import FilingEngine, cosine, text_vector

CANDIDATE_CAP = 400


class FacetedBeam:
    """Scores warmed facet centroids, then cosine-ranks atoms in the winning facets."""

    def __init__(self, conn: sqlite3.Connection, engine: FilingEngine) -> None:
        self.conn = conn
        self.engine = engine

    def search(
        self, query: str, limit: int = 10, beam_width: int = 3
    ) -> list[dict]:
        qvec = text_vector(query)
        if not any(qvec):
            return []
        ranked_facets = []
        for fid, cvec in self.engine._centroids.items():
            ranked_facets.append((cosine(qvec, cvec), fid))
        ranked_facets.sort(key=lambda t: -t[0])
        candidates: dict[int, None] = {}
        for _, fid in ranked_facets[:beam_width]:
            for aid in self.engine.query_facets([fid], match_all=False):
                candidates.setdefault(aid)
                if len(candidates) >= CANDIDATE_CAP:
                    break
            if len(candidates) >= CANDIDATE_CAP:
                break
        if not candidates:
            return []
        marks = ",".join("?" for _ in candidates)
        rows = self.conn.execute(
            f"SELECT id, doc_id, text FROM atom WHERE id IN ({marks})", tuple(candidates)
        ).fetchall()
        scored = []
        for atom_id, doc_id, text in rows:
            score = cosine(qvec, text_vector(text))
            if score > 0:
                scored.append(
                    {"id": atom_id, "doc_id": doc_id, "text": text, "score": score}
                )
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        return scored[:limit]
