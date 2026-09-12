"""Tier 3: global flat hybrid — BM25 + hashed-BoW dense vectors fused via RRF (<= 25ms)."""

from __future__ import annotations

import math
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
    """Warmed dense matrix (numpy) + FTS5 sparse side, fused with RRF.

    Matrix rows are IDF-weighted hashed-BoW vectors: without IDF, a 300-token
    abstract sharing 2 rare query terms drowns under collision mass from 298
    other tokens. IDF is estimated from the indexed corpus at warm() time.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._ids: list[int] = []
        self._pos: dict[int, int] = {}
        self._matrix = None
        self._idf: list[float] | None = None

    def matrix_view(self):
        """Shared read-only view of the warmed dense matrix + id→row map + IDF."""
        return self._matrix, self._pos, self._idf

    def warm(self) -> int:
        """Rebuild the dense matrix. Callers sharing the view (Tier 2 beam) must
        re-share via CascadeRouter.warm() afterwards — the old view goes stale."""
        # float16: 1M docs × 128 dims = 256MB (float32 would breach the RSS gate
        # on its own). Ranking is order-based; fp16 rounding (~1e-3) is far below
        # the abstention margin.
        dtype = np.float16 if np is not None else None
        # Stream rows: fetchall on 1M atoms would transiently double memory.
        total = int(self.conn.execute("SELECT COUNT(*) FROM atom").fetchone()[0])
        cur = self.conn.execute("SELECT id, text FROM atom ORDER BY id")
        self._ids = []
        self._pos = {}
        mat = np.zeros((total, CENTROID_DIM), dtype=dtype) if np is not None else []
        assert (np is None) == (dtype is None)
        df = [0] * CENTROID_DIM
        pos = 0
        while True:
            batch = cur.fetchmany(20000)
            if not batch:
                break
            for atom_id, text in batch:
                self._ids.append(atom_id)
                self._pos[atom_id] = pos
                vec = text_vector(text)
                if np is not None:
                    assert dtype is not None
                    mat[pos] = np.asarray(vec, dtype=dtype)
                else:  # pragma: no cover
                    mat.append(vec)
                for d, v in enumerate(vec):
                    if v:
                        df[d] += 1
                pos += 1
        # IDF reweight + renormalize (vectorized; single pass over rows).
        self._idf = [math.log((total + 1) / (d + 1)) + 1.0 for d in df]
        if np is not None:
            assert dtype is not None
            mat *= np.asarray(self._idf, dtype=dtype)
            norms = np.sqrt((mat.astype(np.float32) ** 2).sum(axis=1))
            norms[norms == 0] = 1.0
            mat /= norms[:, None].astype(dtype)
        else:  # pragma: no cover
            for row in mat:
                norm = math.sqrt(sum((v * w) ** 2 for v, w in zip(row, self._idf))) or 1.0
                for i, w in enumerate(self._idf):
                    row[i] = row[i] * w / norm
        self._matrix = mat
        return len(self._ids)

    def add_atom(self, atom_id: int, text: str) -> None:
        """Incrementally index an atom into the dense matrix without a full re-scan."""
        vec = text_vector(text)
        if self._idf is not None:
            vec = [v * w for v, w in zip(vec, self._idf)]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
        dtype = (
            self._matrix.dtype
            if (self._matrix is not None and np is not None)
            else (np.float16 if np is not None else None)
        )

        if atom_id in self._pos:
            pos = self._pos[atom_id]
            if np is not None and self._matrix is not None:
                self._matrix[pos] = np.asarray(vec, dtype=dtype)
            elif isinstance(self._matrix, list):
                self._matrix[pos] = vec
            return

        pos = len(self._ids)
        self._ids.append(atom_id)
        self._pos[atom_id] = pos
        if np is not None:
            row = np.asarray([vec], dtype=dtype)
            if self._matrix is None or len(self._matrix) == 0:
                self._matrix = row
            else:
                self._matrix = np.concatenate([self._matrix, row], axis=0)
        else:
            if self._matrix is None:
                self._matrix = []
            self._matrix.append(vec)

    def remove_atom(self, atom_id: int) -> None:
        """Remove an atom from pos indexing and zero out its matrix row."""
        if atom_id not in self._pos:
            return
        pos = self._pos.pop(atom_id)
        if self._matrix is not None and np is not None:
            self._matrix[pos] = 0.0
        elif self._matrix is not None and isinstance(self._matrix, list):
            self._matrix[pos] = [0.0] * CENTROID_DIM

    def weighted_query(self, query: str):
        """IDF-weighted normalized query vector (same space as matrix rows)."""
        vec = text_vector(query)
        if self._idf is not None:
            vec = [v * w for v, w in zip(vec, self._idf)]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vec = [v / norm for v in vec]
        if np is not None and self._matrix is not None:
            return np.asarray(vec, dtype=self._matrix.dtype)
        return vec

    def search(
        self, query: str, limit: int = 10, ranked: bool = True,
        scan_cap: int | None = None, pool: int | None = None,
    ) -> list[dict]:
        # BM25-ranked sparse pool: unranked rowid windows miss true docs whenever
        # common terms dominate (measured sparse recall 2/60 on BEIR). Rank-sort
        # cost tracks match count; set ranked=False only under an explicit
        # latency profile (huge corpus + common-term queries).
        sparse = lexical_search(
            self.conn, query, limit=max(limit * 5, 50), ranked=ranked,
            pool=pool,
        )
        sparse_conf = {r["id"]: r["score"] for r in sparse}
        sparse_ids = [r["id"] for r in sparse]
        dense_ids = self._dense_ranked(query, pool=DENSE_POOL, scan_cap=scan_cap)
        # Wide-net retrieve, confidence rerank: RRF top-10 alone buries single-side
        # strong docs (dense rank 27 + no sparse support loses to dual-mediocre).
        # RRF stays as the retrieval fusion; calibrated confidence orders the final.
        fused = rrf_fuse(dense_ids, sparse_ids)[: max(limit * 10, 100)]
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
        # NOTE: `ranked` (the bool profile flag) selects the final order;
        # `ordered` is the result list. Do not reuse one name for both.
        ordered = []
        for aid, rrf in fused:
            confidence = max(dense_conf.get(aid, 0.0), sparse_conf.get(aid, 0.0))
            ordered.append({**by_id[aid], "score": confidence, "rrf": rrf})
        if ranked:
            # Quality profile: BM25 leads the final order, dense supplements.
            # Hashed-BoW cosine is near-lexical — fusing it as an equal vote
            # (max of uncalibrated scales) measurably loses to BM25 alone
            # (0.60 vs 0.66 end-to-end on SciFact). Dense-only docs still fill
            # past the sparse pool: the recall net for vocab-mismatch queries.
            sparse_pos = {aid: i for i, aid in enumerate(sparse_ids)}
            edge = len(sparse_ids)
            ordered.sort(key=lambda a: (sparse_pos.get(a["id"], edge), -a["rrf"]))
        else:
            # Latency profile: confidence order; higher RRF wins ties.
            ordered.sort(key=lambda a: (-a["score"], -a["rrf"]))
        return ordered[:limit]

    def _dense_confidence(self, query: str, atom_ids: list[int]) -> dict[int, float]:
        if not atom_ids:
            return {}
        if np is not None:
            import numpy as _np

            q = _np.asarray(self.weighted_query(query), dtype=self._matrix.dtype)
            if not bool(_np.any(q)):
                return {}
            idx = [self._pos[aid] for aid in atom_ids if aid in self._pos]
            sims = self._matrix[idx] @ q
            return {aid: float(s) for aid, s in zip([a for a in atom_ids if a in self._pos], sims.tolist())}
        from ..filing.engine import cosine

        qvec = self.weighted_query(query)
        if not any(qvec):
            return {}
        return {aid: cosine(qvec, self._matrix[self._pos[aid]]) for aid in atom_ids if aid in self._pos}

    def _dense_ranked(
        self, query: str, pool: int = DENSE_POOL, scan_cap: int | None = None
    ) -> list[int]:
        # Full-matrix scan with argpartition top-k: slicing matrix[:pool] (first-N
        # by id) silently blinds dense retrieval to everything past row N.
        # scan_cap (latency profile) stride-samples rows: bounded traffic with
        # unbiased coverage, at the cost of exact recall.
        if not self._ids:
            return []
        if np is not None:
            import numpy as _np

            q = self.weighted_query(query)
            if not _np.any(q):
                return []
            # Chunked fp32 compute over fp16 storage: numpy has no fast fp16
            # matmul; converting 64k-row blocks keeps BLAS on the hot path.
            q32 = _np.asarray(q, dtype=_np.float32)
            n = len(self._ids)
            rows = (
                _np.linspace(0, n - 1, scan_cap).astype(_np.int64)
                if scan_cap and n > scan_cap
                else None
            )
            k = min(pool if pool else n, len(rows) if rows is not None else n)
            if k <= 0:
                return []
            cand_idx: list = []
            cand_scores: list = []
            view = self._matrix[rows] if rows is not None else self._matrix
            for start in range(0, len(view), 65536):
                blk = view[start:start + 65536].astype(_np.float32) @ q32
                kk = min(k, len(blk))
                cut = _np.argpartition(-blk, kth=kk - 1)[:kk]
                if rows is not None:
                    cand_idx.append(rows[start:start + 65536][cut])
                else:
                    cand_idx.append(cut + start)
                cand_scores.append(blk[cut])
            idx = _np.concatenate(cand_idx)
            scores = _np.concatenate(cand_scores)
            order = _np.argsort(-scores, kind="stable")[:k]
            return [self._ids[int(idx[i])] for i in order.tolist()
                    if float(scores[i]) > 0]
        qvec = self.weighted_query(query)
        if not any(qvec):
            return []
        from ..filing.engine import cosine

        scored = sorted(
            ((cosine(qvec, row), aid) for row, aid in zip(self._matrix, self._ids)),
            key=lambda t: -t[0],
        )
        return [aid for s, aid in scored[:pool] if s > 0]
