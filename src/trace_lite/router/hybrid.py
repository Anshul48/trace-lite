"""Tier 3: global flat hybrid — BM25 + hashed-BoW dense vectors fused via RRF (<= 25ms)."""

from __future__ import annotations

import sqlite3

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from ..filing.engine import CENTROID_DIM, text_vector
from .fusion import rrf_fuse
from .lexical import extract_terms, lexical_search

DENSE_POOL = 2000


class FlatHybrid:
    """Warmed dense matrix (numpy) + FTS5 sparse side, fused with RRF."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._ids: list[int] = []
        self._pos: dict[int, int] = {}
        self._matrix = None

    def warm(self) -> int:
        rows = self.conn.execute("SELECT id, text FROM atom ORDER BY id").fetchall()
        self._ids = [r[0] for r in rows]
        self._pos = {aid: i for i, aid in enumerate(self._ids)}
        if np is not None:
            mat = np.zeros((len(rows), CENTROID_DIM), dtype=np.float32)
            for i, r in enumerate(rows):
                mat[i] = np.asarray(text_vector(r[1]), dtype=np.float32)
            self._matrix = mat
        else:  # pragma: no cover
            self._matrix = [text_vector(r[1]) for r in rows]
        return len(self._ids)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        sparse = lexical_search(self.conn, query, limit=max(limit * 5, 50))
        sparse_conf = {r["id"]: r["score"] for r in sparse}
        sparse_ids = [r["id"] for r in sparse]
        dense_ids = self._dense_ranked(query, pool=min(len(self._ids), DENSE_POOL))
        fused = rrf_fuse(dense_ids, sparse_ids)[:limit]
        if not fused:
            return []
        dense_conf = self._dense_confidence(query, [aid for aid, _ in fused])
        by_id = {r["id"]: r for r in sparse}
        missing = [aid for aid, _ in fused if aid not in by_id]
        if missing:
            marks = ",".join("?" for _ in missing)
            for r in self.conn.execute(
                f"SELECT * FROM atom WHERE id IN ({marks})", tuple(missing)
            ).fetchall():
                by_id[r[0]] = dict(r)
        ranked = []
        for aid, rrf in fused:
            confidence = max(dense_conf.get(aid, 0.0), sparse_conf.get(aid, 0.0))
            ranked.append({**by_id[aid], "score": confidence, "rrf": rrf})
        return ranked

    def _dense_confidence(self, query: str, atom_ids: list[int]) -> dict[int, float]:
        qvec = text_vector(query)
        if not any(qvec) or not atom_ids:
            return {}
        if np is not None:
            import numpy as _np

            q = _np.asarray(qvec, dtype=_np.float32)
            idx = [self._pos[aid] for aid in atom_ids if aid in self._pos]
            sims = self._matrix[idx] @ q
            return {aid: float(s) for aid, s in zip([a for a in atom_ids if a in self._pos], sims.tolist())}
        from ..filing.engine import cosine

        return {aid: cosine(qvec, self._matrix[self._pos[aid]]) for aid in atom_ids if aid in self._pos}

    def _dense_ranked(self, query: str, pool: int) -> list[int]:
        if not self._ids or not pool:
            return []
        if np is not None:
            import numpy as _np

            q = _np.asarray(text_vector(query), dtype=_np.float32)
            if not q.any():
                return []
            sims = self._matrix[:pool] @ q
            order = _np.argsort(-sims, kind="stable")
            return [self._ids[i] for i in order[:pool].tolist() if float(sims[i]) > 0]
        qvec = text_vector(query)
        if not any(qvec):
            return []
        from ..filing.engine import cosine

        scored = sorted(
            (
                (cosine(qvec, self._matrix[i]), self._ids[i])
                for i in range(pool)
            ),
            key=lambda t: -t[0],
        )
        return [aid for s, aid in scored if s > 0]
