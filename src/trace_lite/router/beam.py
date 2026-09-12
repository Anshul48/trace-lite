"""Tier 2: top-down faceted beam search over warmed in-memory centroids (<= 35ms)."""

from __future__ import annotations

import sqlite3

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from ..filing.engine import FilingEngine, cosine, text_vector
from ..filing.taxonomy import UnknownFacetError

CANDIDATE_CAP = 400


class FacetedBeam:
    """Scores warmed facet centroids, then cosine-ranks atoms in the winning facets."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        engine: FilingEngine,
        candidate_cap: int | None = CANDIDATE_CAP,
        sample_strategy: str = "balanced",
    ) -> None:
        self.conn = conn
        self.engine = engine
        self.candidate_cap = candidate_cap
        self.sample_strategy = sample_strategy
        self._matrix = None
        self._pos: dict[int, int] = {}
        self._idf: list[float] | None = None

    def set_vectors(self, matrix, pos: dict[int, int], idf: list[float] | None = None) -> None:
        """Share the warmed dense matrix (owned by Tier 3) — zero per-query recompute."""
        self._matrix = matrix
        self._pos = pos
        self._idf = idf

    def _query_vector(self, query: str) -> list[float]:
        """Query vector in the shared matrix space (IDF-weighted when shared)."""
        import math as _math

        vec = text_vector(query)
        if self._idf is not None:
            vec = [v * w for v, w in zip(vec, self._idf)]
            norm = _math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
        return vec

    def candidate_ids(
        self, query: str, beam_width: int = 3, qvec: list[float] | None = None
    ) -> list[int]:
        """Pool sourced round-robin across the top-`beam_width` facets (F3)."""
        if qvec is None:
            qvec = self._query_vector(query)
        if not any(qvec):
            return []
        ranked_facets = []
        for fid, cvec in self.engine._centroids.items():
            ranked_facets.append((cosine(qvec, cvec), fid))
        ranked_facets.sort(key=lambda t: -t[0])

        # Filter out facets with 0 members BEFORE capping to beam_width,
        # so ghost facets cannot saturate the beam window and starve legitimate facets.
        shortlists = []
        stale_facets = []
        limit = self.candidate_cap
        for _, fid in ranked_facets:
            try:
                ids = self.engine.query_facets(
                    [fid], match_all=False, limit=limit, strategy=self.sample_strategy
                )
            except UnknownFacetError:
                ids = []
            if ids:
                shortlists.append(ids)
                if len(shortlists) >= beam_width:
                    break
            else:
                stale_facets.append(fid)

        if stale_facets:
            for fid in stale_facets:
                self.engine._centroids.pop(fid, None)
                self.conn.execute("UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (fid,))
            self.conn.commit()

        cap = self.candidate_cap if self.candidate_cap is not None else 100000
        candidates: dict[int, None] = {}
        for round_ids in zip(*shortlists):
            for aid in round_ids:
                candidates.setdefault(aid)
                if len(candidates) >= cap:
                    break
            if len(candidates) >= cap:
                break
        if len(candidates) < cap and shortlists:
            # Quota round-robin: each facet contributes at most CAP // nfacets
            # before any facet may claim beyond-quota remainder.
            quota = max(1, cap // len(shortlists))
            added = [0] * len(shortlists)
            cursors = [0] * len(shortlists)
            while len(candidates) < cap:
                progress = False
                for i, shortlist in enumerate(shortlists):
                    while cursors[i] < len(shortlist) and shortlist[cursors[i]] in candidates:
                        cursors[i] += 1
                    if cursors[i] < len(shortlist) and added[i] < quota:
                        candidates[shortlist[cursors[i]]] = None
                        cursors[i] += 1
                        added[i] += 1
                        progress = True
                    if len(candidates) >= cap:
                        break
                if not progress:
                    break
            # Remainder top-up ignoring quota (small facets exhausted).
            if len(candidates) < cap:
                for shortlist in shortlists:
                    for aid in shortlist:
                        candidates.setdefault(aid)
                        if len(candidates) >= cap:
                            break
                    if len(candidates) >= cap:
                        break
        return list(candidates)

    def search(
        self, query: str, limit: int = 10, beam_width: int = 3
    ) -> list[dict]:
        qvec = self._query_vector(query)
        if not any(qvec):
            return []
        candidates = self.candidate_ids(query, beam_width, qvec=qvec)
        if not candidates:
            return []
        marks = ",".join("?" for _ in candidates)
        rows = self.conn.execute(
            f"SELECT id, doc_id, text FROM atom WHERE id IN ({marks})", tuple(candidates)
        ).fetchall()
        dense = self._batch_cosine(qvec, [r[0] for r in rows])
        scored = []
        for (atom_id, doc_id, text), score in zip(rows, dense):
            if score is None:
                score = cosine(qvec, text_vector(text))
            if score > 0:
                scored.append(
                    {"id": atom_id, "doc_id": doc_id, "text": text, "score": score}
                )
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        return scored[:limit]

    def _batch_cosine(self, qvec: list[float], atom_ids: list[int]) -> list[float | None]:
        """Vectorized cosine via the shared warmed matrix; None per atom on miss."""
        if self._matrix is None or np is None:
            return [None] * len(atom_ids)
        import numpy as _np

        q = _np.asarray(qvec, dtype=self._matrix.dtype)
        idx = [self._pos[aid] for aid in atom_ids if aid in self._pos]
        if not idx:
            return [None] * len(atom_ids)
        sims = self._matrix[idx] @ q
        by_id = {aid: float(s) for aid, s in zip(
            [a for a in atom_ids if a in self._pos], sims.tolist())}
        return [by_id.get(aid) for aid in atom_ids]
