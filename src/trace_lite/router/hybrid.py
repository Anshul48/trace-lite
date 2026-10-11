"""Tier 3: global flat hybrid — BM25 + hashed-BoW dense vectors fused via RRF (<= 25ms)."""

from __future__ import annotations

import hashlib
import math
import sqlite3

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from ..filing.engine import CENTROID_DIM, text_vector
from ..store.database import ensure_atom_delta_log
from .fusion import rrf_fuse
from .lexical import extract_terms, lexical_search

DENSE_POOL = 2000

#: Retained change-log rows per prune. Readers whose watermark falls below
#: the retained window detect the gap and fall back to one exact full
#: rebuild (correct, just slower); at G1 scale pruning never triggers.
ATOM_DELTA_RETAIN = 20000


def prune_atom_delta_log(conn: sqlite3.Connection,
                         retain: int = ATOM_DELTA_RETAIN) -> int:
    """Bound the atom change log. WRITE path only; returns rows pruned.

    G2 cycle 2: pruning is a write (DELETE + commit) and must never run on
    a query reader — readers call :meth:`FlatHybrid.warm`, which no longer
    prunes. The worker/drain calls this after a pass (same bound, same
    gap-fallback contract for lagging readers).
    """
    row = conn.execute("SELECT COUNT(*) FROM atom_delta").fetchone()
    total = int(row[0]) if row else 0
    if total <= retain:
        return 0
    conn.execute(
        "DELETE FROM atom_delta WHERE seq <= "
        "(SELECT MAX(seq) - ? FROM atom_delta)",
        (retain,))
    if conn.in_transaction:
        conn.commit()
    return max(0, total - retain)


def _delta_log_present(conn: sqlite3.Connection) -> bool:
    """Read-only presence check for the change log (never takes a write lock)."""
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'atom_delta'"
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _digest16(text: str) -> bytes:
    """16-byte content fingerprint for delta-warm change detection."""
    return hashlib.sha256(text.encode("utf-8")).digest()[:16]


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
        # Content fingerprints parallel to _ids (holes mirror _ids holes):
        # the delta-warm path re-encodes only new/changed rows.
        self._digests: list[bytes] = []
        # Honest account of the most recent warm (full vs delta, encode count).
        self.last_warm: dict = {
            "full": False, "encodes": 0, "reused": 0, "removed": 0, "live": 0,
        }
        # Change-log watermark: max atom_delta.seq consumed. Delta refreshes
        # read only rows past this point (O(delta) I/O, O(delta) encodes).
        self._delta_seq: int = 0
        # Fused ids skipped by the last tolerant fill (race with a
        # concurrent delete/publication; 0 in the quiet case).
        self.last_fill_skipped: int = 0
        # R3 shared-view freeze: True while _matrix/_ids/_pos/_idf/_digests
        # are references to a process-wide pooled view owned by the served
        # layer (never owned here). Every mutating path thaws first
        # (copy-on-write), so a delta update can never scribble on the
        # arrays other threads are reading. Reads need no lock: numpy
        # element reads are thread-safe and the pooled objects are never
        # mutated in place by anyone once published.
        self._shared_frozen: bool = False

    def matrix_view(self):
        """Shared read-only view of the warmed dense matrix + id→row map + IDF."""
        return self._matrix, self._pos, self._idf

    def attach_shared(self, matrix, ids: list[int], pos: dict[int, int],
                      idf: list[float] | None, digests: list[bytes],
                      delta_seq: int) -> int:
        """Attach a pooled warmed view by reference (zero encodes, zero copy).

        The served layer publishes exactly one immutable view per
        generation; readers attach it instead of re-encoding the corpus.
        The instance is frozen until its next mutation, which thaws to a
        private copy first — the pooled arrays are never written in place.
        Returns the LIVE row count. Vectors are bit-identical to a private
        warm: the pooled view was built by the same warm() code.
        """
        self._matrix = matrix
        self._ids = ids
        self._pos = pos
        self._idf = idf
        self._digests = digests
        self._delta_seq = int(delta_seq)
        self._shared_frozen = True
        live = len(self._pos)
        self.last_warm = {"full": False, "encodes": 0, "reused": live,
                          "removed": 0, "live": live, "shared": True}
        return live

    def shared_snapshot(self) -> tuple:
        """References to this instance's warmed structures for pooling.

        The caller publishes them as immutable; this instance must treat
        them as frozen from here on (callers set the flag via
        :meth:`attach_shared` semantics — see :meth:`freeze_shared`).
        """
        return (self._matrix, self._ids, self._pos, self._idf,
                self._digests, self._delta_seq)

    def freeze_shared(self) -> None:
        """Mark owned structures as pooled-shared (copy-on-write from now on)."""
        self._shared_frozen = True

    def _thaw(self) -> None:
        """Copy-on-write: take private ownership before any mutation."""
        if not self._shared_frozen:
            return
        if self._matrix is not None and np is not None:
            self._matrix = self._matrix.copy()
        elif isinstance(self._matrix, list):  # pragma: no cover
            self._matrix = [list(r) if isinstance(r, list) else r
                            for r in self._matrix]
        self._ids = list(self._ids)
        self._pos = dict(self._pos)
        self._idf = list(self._idf) if self._idf is not None else None
        self._digests = list(self._digests)
        self._shared_frozen = False

    def _weighted(self, vec: list[float]) -> list[float]:
        """Apply the frozen IDF epoch + L2 renormalize (delta-path weighting).

        Same formula as add_atom: delta refreshes never silently refit the
        epoch — pass full=True to warm() for an exact IDF rebuild.
        """
        assert self._idf is not None, "no IDF epoch: full warm() must run first"
        vec = [v * w for v, w in zip(vec, self._idf)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def warm(self, full: bool = False) -> int:
        """Rebuild the dense matrix. Callers sharing the view (Tier 2 beam) must
        re-share via CascadeRouter.warm() afterwards — the old view goes stale.

        Delta-aware: after the first full build, a refresh consumes only the
        atom change log past its watermark (O(delta) I/O, O(delta) encodes,
        zero IDF refit — the epoch stays frozen exactly as with add_atom).
        No whole-table scan, no corpus-wide re-hash. Pass full=True for an
        exact corpus-wide rebuild + IDF refit. Returns the LIVE row count.
        """
        if (not full and self._matrix is not None and self._idf is not None
                and len(self._digests) == len(self._ids)):
            try:
                return self._warm_delta()
            except Exception:
                # Any mid-delta anomaly falls back to the exact full
                # rebuild (a partial delta would leave a corrupt view;
                # last_warm reports full=True so the fallback is visible).
                pass
        return self._warm_full()

    def _warm_full(self) -> int:
        # float16: 1M docs × 128 dims = 256MB (float32 would breach the RSS gate
        # on its own). Ranking is order-based; fp16 rounding (~1e-3) is far below
        # the abstention margin.
        # A full rebuild replaces every structure with fresh objects, so a
        # previously attached shared view is simply dropped (never mutated);
        # the instance owns the result privately from here on.
        self._shared_frozen = False
        dtype = np.float16 if np is not None else None
        # Stream rows: fetchall on 1M atoms would transiently double memory.
        total = int(self.conn.execute("SELECT COUNT(*) FROM atom").fetchone()[0])
        cur = self.conn.execute("SELECT id, text FROM atom ORDER BY id")
        self._ids = []
        self._pos = {}
        self._digests = []
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
                self._digests.append(_digest16(text))
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
        # Read-mostly ensure: the writer/reader open path already created the
        # log; a redundant DDL write txn here would take the write lock on
        # the query path. Only ensure when actually absent (fresh files),
        # and never fail the warm when the log cannot be created yet.
        if not _delta_log_present(self.conn):
            try:
                ensure_atom_delta_log(self.conn)
            except Exception:
                pass
        self._delta_seq = self._max_delta_seq() if _delta_log_present(
            self.conn) else 0
        self.last_warm = {"full": True, "encodes": len(self._ids),
                          "reused": 0, "removed": 0, "live": len(self._pos)}
        return len(self._pos)

    def _max_delta_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM atom_delta").fetchone()
        return int(row[0]) if row else 0

    def _warm_delta(self) -> int:
        """Refresh changed rows only; the IDF epoch stays frozen (add_atom semantics).

        Consumes the ``atom_delta`` change log past ``self._delta_seq``:
        O(delta) indexed I/O, O(delta) encodes, no whole-table scan and no
        corpus-wide re-hash. Lifecycle mapping (R01 writes delete + insert,
        never in-place mutation): insert → upsert row, edit → delete + upsert
        rows, retract → delete row, replay → no rows (exact no-op). A pruned
        window or a torn parallel index raises so ``warm()`` falls back to
        one exact full rebuild.
        """
        assert self._matrix is not None and self._idf is not None
        # Read-mostly warm (G2 cycle 2): never force DDL on the query path.
        # When the log is absent raise into warm()'s exact-full-rebuild
        # fallback (which ensures read-mostly).
        if not _delta_log_present(self.conn):
            raise RuntimeError("change log absent; full rebuild required")
        # Parallel-index integrity: removals leave holes in _ids/_digests by
        # design (rows are zeroed, never compacted), so only the digest/id
        # lengths must agree and every live row must be in bounds. Anything
        # else raises into warm()'s exact-full-rebuild fallback.
        if len(self._digests) != len(self._ids):
            raise RuntimeError("parallel index torn; full rebuild required")
        if self._pos and max(self._pos.values()) >= len(self._ids):
            raise RuntimeError("parallel index row out of bounds")
        # No prune here: pruning writes and readers never take write locks
        # (G2 cycle 2); the worker prunes after a pass via
        # prune_atom_delta_log().
        floor = self.conn.execute(
            "SELECT COALESCE(MIN(seq), 0) FROM atom_delta").fetchone()
        floor_seq = int(floor[0]) if floor else 0
        if floor_seq and self._delta_seq < floor_seq - 1:
            raise RuntimeError("change-log window pruned past watermark")
        # Coalesce: last op per atom wins (an upsert-then-delete in one
        # window nets a delete with zero encodes).
        ops: dict[int, tuple[str, str | None]] = {}
        high = self._delta_seq
        cur = self.conn.execute(
            "SELECT seq, atom_id, op, text FROM atom_delta WHERE seq > ? ORDER BY seq",
            (self._delta_seq,))
        while True:
            batch = cur.fetchmany(20000)
            if not batch:
                break
            for seq, atom_id, op, text in batch:
                high = max(high, int(seq))
                ops[int(atom_id)] = (str(op), str(text) if text is not None else None)
        dtype = (
            self._matrix.dtype
            if (self._matrix is not None and np is not None)
            else (np.float16 if np is not None else None)
        )
        if ops:
            # Copy-on-write: delta refreshes write rows in place, so a
            # shared-attached instance must thaw to private arrays first —
            # the pooled view other threads read is never mutated.
            self._thaw()
        removed = 0
        for aid, (op, _text) in ops.items():
            if op == "delete" and aid in self._pos:
                self.remove_atom(aid)
                removed += 1
        fresh: list[tuple[int, list[float]]] = []
        fresh_text: dict[int, str] = {}
        upserts = [(aid, text) for aid, (op, text) in ops.items() if op != "delete"]
        encoded = 0
        for aid, text in upserts:
            if text is None:  # defensive: resolve live text, else tombstone
                row = self.conn.execute(
                    "SELECT text FROM atom WHERE id = ?", (aid,)).fetchone()
                if row is None:
                    if aid in self._pos:
                        self.remove_atom(aid)
                        removed += 1
                    continue
                text = str(row[0])
            wvec = self._weighted(text_vector(text))
            encoded += 1
            row = self._pos.get(aid)
            if row is not None:
                if np is not None and self._matrix is not None:
                    self._matrix[row] = np.asarray(wvec, dtype=dtype)
                elif isinstance(self._matrix, list):  # pragma: no cover
                    self._matrix[row] = wvec
                self._digests[row] = _digest16(text)
            else:
                fresh.append((aid, wvec))
                fresh_text[aid] = text
        if fresh:
            fresh.sort(key=lambda t: t[0])
            if np is not None:
                block = np.asarray([v for _, v in fresh], dtype=dtype)
                if self._matrix is None or len(self._matrix) == 0:
                    self._matrix = block
                else:
                    self._matrix = np.concatenate([self._matrix, block], axis=0)
            else:  # pragma: no cover
                if self._matrix is None:
                    self._matrix = []
                self._matrix.extend(v for _, v in fresh)
            for aid, _ in fresh:
                self._pos[aid] = len(self._ids)
                self._ids.append(aid)
                self._digests.append(_digest16(fresh_text[aid]))
        self._delta_seq = high
        live = len(self._pos)
        self.last_warm = {"full": False, "encodes": encoded,
                          "reused": live - len(fresh),
                          "removed": removed, "live": live}
        return live

    def _prune_delta_log(self) -> int:
        """Bound the change log; returns rows pruned (0 at G1 scale).

        Kept for callers on the WRITE path (tests, tooling); the query-path
        :meth:`warm` never prunes (see :func:`prune_atom_delta_log`, owned
        by the worker/drain).
        """
        return prune_atom_delta_log(self.conn)

    def add_atom(self, atom_id: int, text: str) -> None:
        """Incrementally index an atom into the dense matrix without a full re-scan."""
        self._thaw()  # copy-on-write: never mutate a pooled shared view
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
            if len(self._digests) == len(self._ids):
                self._digests[pos] = _digest16(text)
            return

        pos = len(self._ids)
        self._ids.append(atom_id)
        self._pos[atom_id] = pos
        if len(self._digests) == pos:
            self._digests.append(_digest16(text))
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
        self._thaw()  # copy-on-write: never mutate a pooled shared view
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
        # Tolerant fill (G2 cycle 2): a fused id can vanish between the
        # snapshot reads (R01 delete committed, generation not yet
        # published; pooled-view/pin skew under concurrent publication).
        # Skipping serves the remaining evidence instead of a KeyError 500;
        # the count is observable via last_fill_skipped.
        ordered = []
        skipped = 0
        for aid, rrf in fused:
            row = by_id.get(aid)
            if row is None:
                skipped += 1
                continue
            confidence = max(dense_conf.get(aid, 0.0), sparse_conf.get(aid, 0.0))
            ordered.append({**row, "score": confidence, "rrf": rrf})
        self.last_fill_skipped = skipped
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
