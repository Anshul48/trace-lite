"""Multi-membership indexing and faceted query over the Hearst forest."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import struct

from .taxonomy import Taxonomy, UnknownFacetError

CENTROID_DIM = 128
_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def text_vector(text: str, dim: int = CENTROID_DIM) -> list[float]:
    """Deterministic hashed bag-of-words vector, L2-normalized. No ML dependency."""
    vec = [0.0] * dim
    for tok in tokenize(text):
        vec[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}d", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    n = len(blob) // 8
    return list(struct.unpack(f"<{n}d", blob))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class FilingEngine:
    """Assigns atoms to facets; resolves AND/OR queries with subtree expansion."""

    def __init__(self, conn: sqlite3.Connection, taxonomy: Taxonomy | None = None) -> None:
        self.conn = conn
        self.taxonomy = taxonomy if taxonomy is not None else Taxonomy(conn)
        self._centroids: dict[str, list[float]] = {}

    # -- writes -------------------------------------------------------
    def assign_facets(
        self, atom_id: int, facet_ids: list[str], confidence: float = 1.0
    ) -> None:
        if not 0.0 < confidence <= 1.0:
            raise ValueError("confidence must be in (0, 1]")
        if self.conn.execute("SELECT 1 FROM atom WHERE id = ?", (atom_id,)).fetchone() is None:
            raise KeyError(f"unknown atom {atom_id}")
        for fid in facet_ids:
            self.taxonomy.get_facet(fid)  # raises UnknownFacetError when missing
        self.conn.executemany(
            "INSERT INTO memberships (atom_id, facet_id, confidence) VALUES (?, ?, ?)"
            " ON CONFLICT(atom_id, facet_id) DO UPDATE SET confidence = excluded.confidence",
            [(atom_id, fid, confidence) for fid in facet_ids],
        )
        self.conn.commit()
        for fid in facet_ids:
            self._centroids.pop(fid, None)

    def assign_facets_bulk(
        self, pairs: list[tuple[int, str]], confidence: float = 1.0, chunk: int = 5000
    ) -> int:
        """Bulk membership load: validates facets once, one transaction per chunk.

        Skips per-atom existence checks (callers bulk-insert atoms first); raises
        on unknown facet ids before writing anything.
        """
        if not 0.0 < confidence <= 1.0:
            raise ValueError("confidence must be in (0, 1]")
        for fid in {fid for _, fid in pairs}:
            self.taxonomy.get_facet(fid)  # raises UnknownFacetError when missing
        sql = (
            "INSERT INTO memberships (atom_id, facet_id, confidence) VALUES (?, ?, ?)"
            " ON CONFLICT(atom_id, facet_id) DO UPDATE SET confidence = excluded.confidence"
        )
        for i in range(0, len(pairs), chunk):
            self.conn.executemany(
                sql, [(aid, fid, confidence) for aid, fid in pairs[i:i + chunk]]
            )
            self.conn.commit()
        for _, fid in pairs:
            self._centroids.pop(fid, None)
        return len(pairs)

    # -- reads --------------------------------------------------------
    def facets_of_atom(self, atom_id: int) -> list[tuple[str, float]]:
        return [
            (r[0], r[1])
            for r in self.conn.execute(
                "SELECT facet_id, confidence FROM memberships WHERE atom_id = ?", (atom_id,)
            ).fetchall()
        ]

    def query_facets(
        self, facets: list[str], match_all: bool = True, limit: int | None = None
    ) -> list[int]:
        """Atom ids matching ALL (AND) or ANY (OR) of the facets, subtree-expanded.

        `limit` caps rows scanned per facet group (index-ordered, deterministic).
        With AND + limit the intersection is over capped groups (documented
        approximation for bounded-latency retrieval); omit it for exact results.
        """
        if not facets:
            return []
        expanded = [self.taxonomy.subtree_ids(fid) for fid in facets]  # UnknownFacetError propagates
        if match_all:
            sets = [self._atoms_in_facets(fids, limit) for fids in expanded]
            result = sets[0]
            for s in sets[1:]:
                result &= s
            return sorted(result)
        union: set[int] = set()
        for fids in expanded:
            union |= self._atoms_in_facets(fids, limit)
        return sorted(union)

    def _atoms_in_facets(self, facet_ids: set[str], limit: int | None = None) -> set[int]:
        if not facet_ids:
            return set()
        marks = ",".join("?" for _ in facet_ids)
        # (atom_id, facet_id) is the PK: rows are unique per facet, so no DISTINCT
        # sort is needed; the covering index serves ORDER+LIMIT with early stop.
        sql = f"SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id"
        params: tuple = tuple(facet_ids)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        return {r[0] for r in self.conn.execute(sql, params).fetchall()}

    # -- warmed centroids (MED-01: Tier 2 must not fault centroids from disk) --
    def facet_centroid(self, facet_id: str) -> list[float] | None:
        cached = self._centroids.get(facet_id)
        if cached is not None:
            return cached
        row = self.conn.execute(
            "SELECT centroid_blob FROM facets WHERE facet_id = ?", (facet_id,)
        ).fetchone()
        if row is not None and row[0] is not None:
            vec = unpack_vector(bytes(row[0]))
            self._centroids[facet_id] = vec
            return vec
        return None

    def refresh_centroid(self, facet_id: str, chunk: int = 900) -> list[float] | None:
        """Recompute a facet centroid from current member atom texts; persists the blob."""
        self.taxonomy.get_facet(facet_id)
        member_ids = sorted(self._atoms_in_facets({facet_id}))
        if not member_ids:
            return None
        texts: list[str] = []
        for i in range(0, len(member_ids), chunk):
            window = member_ids[i:i + chunk]
            marks = ",".join("?" for _ in window)
            texts.extend(
                r[0]
                for r in self.conn.execute(
                    f"SELECT text FROM atom WHERE id IN ({marks})", tuple(window)
                ).fetchall()
            )
        agg = [0.0] * CENTROID_DIM
        for text in texts:
            for i, v in enumerate(text_vector(text)):
                agg[i] += v
        norm = math.sqrt(sum(v * v for v in agg))
        if norm > 0:
            agg = [v / norm for v in agg]
        self.conn.execute(
            "UPDATE facets SET centroid_blob = ? WHERE facet_id = ?", (pack_vector(agg), facet_id)
        )
        self.conn.commit()
        self._centroids[facet_id] = agg
        return agg

    def warm_centroids(self) -> int:
        """Preload every persisted centroid blob into RAM. Returns count warmed."""
        count = 0
        for (fid, blob) in self.conn.execute(
            "SELECT facet_id, centroid_blob FROM facets WHERE centroid_blob IS NOT NULL"
        ).fetchall():
            self._centroids[fid] = unpack_vector(bytes(blob))
            count += 1
        return count
