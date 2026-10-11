"""Multi-membership indexing and faceted query over the Hearst forest.

R06 incremental maintenance (ARCH §5–§6): facet centroids are maintained as
O(k*d) running accumulators (unnormalized sum + member count) instead of
O(M*d) full-corpus rescans on every edit. The persisted form is unchanged
(``facets.centroid_blob`` holds the L2-normalized centroid, NULL when
empty); the unnormalized sum lives in RAM and is re-seeded by one exact
scan per facet per process. ``refresh_centroid`` is intentionally left
untouched as the independent exact oracle for parity checks. Membership
rows are still the source of truth: any out-of-band membership change
(direct ``Database.delete_atom``, bulk loads) is detected by
``check_centroid_drift`` / healed by ``reseed_accum``; the writer paths in
``api/app.py`` use ``detach_atom`` / ``assign_facets`` so accums stay exact.

Long-sequence floating-point drift is NOT reconciled here: ``check_drift``
is the hook and R09 measures drift over 100k-edit sequences (packet
recovery clause). No background reconciler in this packet.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import struct
import time
from collections import Counter
from datetime import datetime, timezone

from .taxonomy import Taxonomy, UnknownFacetError

CENTROID_DIM = 128
#: Parity tolerance for incremental-vs-exact centroid agreement (packet C6-1).
CENTROID_TOL = 1e-5
#: Default work budget for dependency-closure walks (packet C6-4).
INVALIDATION_BUDGET = 10000
_TOKEN_RE = re.compile(r"[a-z0-9_]+", re.IGNORECASE)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvalidationBudgetError(RuntimeError):
    """Raised when a dependency-invalidation closure exceeds its work budget."""


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def vector_tokens(text: str) -> list[str]:
    """Tokens admitted to hashed vectors: all-digit tokens (per-doc serial numbers,
    years-as-identifiers) are excluded — at df=1 they take maximum IDF and turn
    every accidental collision into a dominant false match. FTS still indexes them
    for exact lookup."""
    return [t for t in tokenize(text) if not t.isdigit()]


def text_vector(text: str, dim: int = CENTROID_DIM) -> list[float]:
    """Deterministic hashed bag-of-words vector, L2-normalized. No ML dependency."""
    vec = [0.0] * dim
    for tok in vector_tokens(text):
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
        # R06 running accumulators: facet_id -> {"sum": [float]*DIM, "count": int}.
        # Unnormalized sums; normalized only for routing/persistence.
        self._accum: dict[str, dict] = {}
        # Exact member scans performed to seed accums (evidence that edits
        # after seeding are O(k*d): this counter stays flat across edits).
        self._accum_seed_scans = 0
        # R06 query-path snapshot: facet_id -> path (for batched subtree
        # expansion without per-facet SQL roundtrips) + COUNT(*) fingerprint.
        self._facet_paths: dict[str, str] | None = None
        self._facet_paths_count: int | None = None
        self._subtree_cache: dict[str, frozenset[str]] = {}

    def bind_database(self, db) -> None:
        """Bind Database instance so that atom deletions automatically evict stale centroids from RAM."""
        def _on_empty(fid: str) -> None:
            self._centroids.pop(fid, None)
            self._accum.pop(fid, None)
        db.on_empty_facet = _on_empty

    # -- writes -------------------------------------------------------
    def assign_facets(
        self, atom_id: int, facet_ids: list[str], confidence: float = 1.0,
        *, commit: bool = True,
    ) -> None:
        """Incremental membership write. ``commit=False`` defers to the caller's
        transaction (R06 single-transaction note replacement)."""
        if not 0.0 < confidence <= 1.0:
            raise ValueError("confidence must be in (0, 1]")
        row = self.conn.execute("SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown atom {atom_id}")
        for fid in facet_ids:
            self.taxonomy.get_facet(fid)  # raises UnknownFacetError when missing
        unique_fids = list(dict.fromkeys(facet_ids))
        old = dict(self.facets_of_atom(atom_id))
        # Seed accums against the pre-write state so post-write bumps are exact.
        for fid in unique_fids:
            self._ensure_accum(fid)
        self.conn.executemany(
            "INSERT INTO memberships (atom_id, facet_id, confidence) VALUES (?, ?, ?)"
            " ON CONFLICT(atom_id, facet_id) DO UPDATE SET confidence = excluded.confidence",
            [(atom_id, fid, confidence) for fid in unique_fids],
        )
        if commit:
            self.conn.commit()
        text = str(row[0])
        for fid in unique_fids:
            if fid not in old:
                self._bump(fid, text, +1)
            elif old[fid] != confidence:
                # Weight change: subtract-old/add-new (no-op for the current
                # unweighted centroid, but keeps the O(k*d) path exact).
                self._bump(fid, text, -1)
                self._bump(fid, text, +1)
            self._persist_accum(fid, commit=commit)
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
            # Bulk load invalidates the running sums; they re-seed with one
            # exact scan on the next incremental edit (see _ensure_accum).
            self._accum.pop(fid, None)
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
        self,
        facets: list[str],
        match_all: bool = True,
        limit: int | None = None,
        strategy: str = "head",
    ) -> list[int]:
        """Atom ids matching ALL (AND) or ANY (OR) of the facets, subtree-expanded.

        `limit` caps rows scanned per facet group (index-ordered, deterministic).
        `strategy` controls selection when capped: 'head' (earliest ids), 'tail'
        (latest ids), or 'balanced' (bimodal head + tail split to prevent blinding
        the beam to documents past row `limit`).
        With AND + limit the intersection is over capped groups (documented
        approximation for bounded-latency retrieval); omit it for exact results.
        """
        if not facets:
            return []
        expanded = self._expand_facets(facets)  # UnknownFacetError propagates
        if match_all:
            sets = [self._atoms_in_facets(fids, limit, strategy=strategy) for fids in expanded]
            result = sets[0]
            for s in sets[1:]:
                result &= s
            return sorted(result)
        union: set[int] = set()
        for fids in expanded:
            union |= self._atoms_in_facets(fids, limit, strategy=strategy)
        return sorted(union)

    def _facet_snapshot(self) -> dict[str, str]:
        """facet_id -> path snapshot, refreshed when the facet population changes.

        Runtime facet rows are insert-only (``create_facet``; moves and deletes
        never occur outside tests), so a ``COUNT(*)`` fingerprint detects every
        structural change with one cheap roundtrip. This replaces two SQL
        statements per facet per query (``get_facet`` + subtree ``LIKE``) with
        one snapshot reused across the whole query. Unknown ids raise
        :class:`UnknownFacetError` exactly as ``Taxonomy.get_facet`` would.
        """
        count = int(self.conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0])
        if self._facet_paths is None or self._facet_paths_count != count:
            self._facet_paths = {
                r[0]: r[1]
                for r in self.conn.execute("SELECT facet_id, path FROM facets").fetchall()
            }
            self._facet_paths_count = count
            self._subtree_cache = {}
        return self._facet_paths

    @staticmethod
    def _like_prefix(path: str, prefix: str) -> bool:
        """Exact mirror of ``path LIKE <prefix>%`` for a fully escaped literal
        prefix: byte-wise comparison folding ASCII A-Z only (SQLite ``LIKE``
        folds ASCII case and compares every other byte exactly)."""
        if len(path) < len(prefix):
            return False
        for a, b in zip(path[:len(prefix)], prefix):
            if a == b:
                continue
            oa, ob = ord(a), ord(b)
            if 65 <= oa <= 90:
                oa += 32
            if 65 <= ob <= 90:
                ob += 32
            if oa != ob:
                return False
        return True

    def _expand_facets(self, facet_ids: list[str]) -> list[set[str]]:
        """One-shot subtree expansion over the facet snapshot (C6-3).

        Returns, per requested facet, the facet plus all descendants — the
        same set ``Taxonomy.subtree_ids`` computes (``path = P OR path LIKE
        P-escaped/%``), without per-facet roundtrips.
        """
        paths = self._facet_snapshot()
        out: list[set[str]] = []
        for fid in facet_ids:
            if fid not in paths:
                raise UnknownFacetError(fid)
            cached = self._subtree_cache.get(fid)
            if cached is None:
                base = paths[fid]
                prefix = base + "/"
                cached = frozenset(
                    cand
                    for cand, p in paths.items()
                    if p == base or self._like_prefix(p, prefix)
                )
                self._subtree_cache[fid] = cached
            out.append(set(cached))
        return out

    def _atoms_in_facets(
        self,
        facet_ids: set[str],
        limit: int | None = None,
        strategy: str = "head",
    ) -> set[int]:
        if not facet_ids:
            return set()
        marks = ",".join("?" for _ in facet_ids)
        params: tuple = tuple(facet_ids)
        if limit is None:
            sql = f"SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id"
            return {r[0] for r in self.conn.execute(sql, params).fetchall()}
        if strategy == "balanced":
            half = limit // 2
            other_half = limit - half
            sql = (
                f"SELECT atom_id FROM ("
                f"  SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id ASC LIMIT ?"
                f") UNION SELECT atom_id FROM ("
                f"  SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id DESC LIMIT ?"
                f")"
            )
            return {r[0] for r in self.conn.execute(sql, (*params, half, *params, other_half)).fetchall()}
        if strategy == "tail":
            sql = f"SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id DESC LIMIT ?"
            return {r[0] for r in self.conn.execute(sql, (*params, limit)).fetchall()}
        if strategy == "head":
            sql = f"SELECT atom_id FROM memberships WHERE facet_id IN ({marks}) ORDER BY atom_id LIMIT ?"
            return {r[0] for r in self.conn.execute(sql, (*params, limit)).fetchall()}
        raise ValueError(f"unknown strategy: {strategy!r}")

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
            self.conn.execute(
                "UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (facet_id,)
            )
            self.conn.commit()
            self._centroids.pop(facet_id, None)
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

    def clear_stale_centroids(self, *, commit: bool = True) -> list[str]:
        """Clear centroid blobs in DB and RAM for facets with zero members."""
        db_fids = {
            r[0]
            for r in self.conn.execute(
                "SELECT facet_id FROM facets WHERE centroid_blob IS NOT NULL "
                "AND facet_id NOT IN (SELECT DISTINCT facet_id FROM memberships)"
            ).fetchall()
        }
        active_fids = {
            r[0] for r in self.conn.execute("SELECT DISTINCT facet_id FROM memberships").fetchall()
        }
        ram_empty_fids = set(self._centroids.keys()) - active_fids
        accum_empty_fids = set(self._accum.keys()) - active_fids
        all_empty = db_fids | ram_empty_fids | accum_empty_fids
        for fid in all_empty:
            self._centroids.pop(fid, None)
            self._accum.pop(fid, None)
            self.conn.execute("UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (fid,))
        if all_empty and commit:
            self.conn.commit()
        return list(all_empty)

    def warm_centroids(self) -> int:
        """Preload every persisted centroid blob into RAM, pruning empty facets. Returns count warmed."""
        self.clear_stale_centroids()
        count = 0
        for (fid, blob) in self.conn.execute(
            "SELECT facet_id, centroid_blob FROM facets WHERE centroid_blob IS NOT NULL"
        ).fetchall():
            self._centroids[fid] = unpack_vector(bytes(blob))
            count += 1
        return count

    # -- R06 incremental accumulators (O(k*d) edits) -------------------
    def _scan_facet_sum(self, facet_id: str, chunk: int = 900) -> tuple[list[float], int]:
        """Exact unnormalized sum + member count (oracle-independent scan).

        Deliberately separate from ``refresh_centroid`` so the parity oracle
        and the accumulator seed path share no code: agreement between them
        is evidence, not tautology.
        """
        member_ids = sorted(self._atoms_in_facets({facet_id}))
        agg = [0.0] * CENTROID_DIM
        for i in range(0, len(member_ids), chunk):
            window = member_ids[i:i + chunk]
            marks = ",".join("?" for _ in window)
            for (text,) in self.conn.execute(
                f"SELECT text FROM atom WHERE id IN ({marks})", tuple(window)
            ).fetchall():
                for j, v in enumerate(text_vector(str(text))):
                    agg[j] += v
        self._accum_seed_scans += 1
        return agg, len(member_ids)

    def _ensure_accum(self, facet_id: str) -> dict:
        entry = self._accum.get(facet_id)
        if entry is None:
            total, count = self._scan_facet_sum(facet_id)
            entry = {"sum": total, "count": count}
            self._accum[facet_id] = entry
        return entry

    def reseed_accum(self, facet_id: str) -> dict:
        """Discard the running sum and re-seed from an exact scan.

        Use after out-of-band membership changes (direct ``Database``
        deletes, foreign writers). Returns the fresh entry.
        """
        self._accum.pop(facet_id, None)
        return self._ensure_accum(facet_id)

    def _bump(self, facet_id: str, text: str, sign: int) -> dict:
        """Add (+1) or subtract (-1) one member vector. O(d)."""
        entry = self._accum.get(facet_id)
        if entry is None:
            raise KeyError(f"accumulator for facet {facet_id!r} is not seeded")
        total = entry["sum"]
        for j, v in enumerate(text_vector(text)):
            total[j] += sign * v
        entry["count"] = int(entry["count"]) + sign
        if int(entry["count"]) < 0:
            raise RuntimeError(f"accumulator for facet {facet_id!r} went negative")
        return entry

    def _persist_accum(self, facet_id: str, *, commit: bool = True) -> list[float] | None:
        """Normalize the running sum into ``centroid_blob`` + RAM cache.

        Empty facets clear the blob (ghost-facet exclusion, consistent with
        R05 empty-clears-descriptors). Mirrors ``refresh_centroid`` output
        semantics exactly: normalized unit vector, raw zeros when the norm
        vanishes, NULL when there are no members.
        """
        entry = self._accum[facet_id]
        total = entry["sum"]
        count = int(entry["count"])
        if count <= 0:
            entry["sum"] = [0.0] * CENTROID_DIM
            entry["count"] = 0
            self.conn.execute(
                "UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (facet_id,)
            )
            if commit:
                self.conn.commit()
            self._centroids.pop(facet_id, None)
            return None
        norm = math.sqrt(sum(v * v for v in total))
        vec = [v / norm for v in total] if norm > 0 else list(total)
        self.conn.execute(
            "UPDATE facets SET centroid_blob = ? WHERE facet_id = ?",
            (pack_vector(vec), facet_id),
        )
        if commit:
            self.conn.commit()
        self._centroids[facet_id] = vec
        return vec

    def remove_members(self, facet_id: str, texts: list[str], *, commit: bool = True):
        """Fold already-deleted members out of a facet (O(removed*d)).

        For the out-of-band delete case (source publish/retract removes atom
        rows in its own transaction): the caller passes the pre-delete texts.
        A seeded accumulator is exact after the fold-out; a cold facet seeds
        once from post-delete truth (nothing to fold). A failed fold (stale
        accumulator) falls back to one exact reseed. Always persists
        (including the emptied-facet NULL branch).
        """
        if facet_id in self._accum:
            try:
                for text in texts:
                    self._bump(facet_id, text, -1)
            except (KeyError, RuntimeError):
                self.reseed_accum(facet_id)
        else:
            self._ensure_accum(facet_id)
        return self._persist_accum(facet_id, commit=commit)

    def detach_atom(self, atom_id: int, *, commit: bool = True) -> dict:
        """Remove one atom's contributions (subtract-old) and its membership rows.

        Call BEFORE the atom row itself is deleted: membership rows are the
        source of truth for counts, so they are deleted here while the atom
        text is still readable; the later atom-row delete then finds no
        memberships (cascade no-op) and the running sums already agree with
        it. ``commit=False`` defers to the caller's transaction (R06
        single-transaction note replacement).
        """
        row = self.conn.execute("SELECT text FROM atom WHERE id = ?", (atom_id,)).fetchone()
        if row is None:
            return {"atom_id": atom_id, "facets": [], "detached": False}
        text = str(row[0])
        fids = [r[0] for r in self.conn.execute(
            "SELECT facet_id FROM memberships WHERE atom_id = ?", (atom_id,)).fetchall()]
        for fid in fids:
            self._ensure_accum(fid)
        if fids:
            self.conn.execute("DELETE FROM memberships WHERE atom_id = ?", (atom_id,))
        for fid in fids:
            self._bump(fid, text, -1)
            self._persist_accum(fid, commit=commit)
        if commit:
            self.conn.commit()
        return {"atom_id": atom_id, "facets": sorted(fids), "detached": True}

    def note_text_change(self, atom_id: int, old_text: str, new_text: str,
                         *, commit: bool = True) -> dict:
        """Fold an in-place premise edit into the running sums (subtract-old/add-new).

        The caller owns the ``atom`` row + FTS update (before or after; the
        accumulator only uses the passed texts). Membership is unchanged.
        Returns the touched facets. This is the premise-edit primitive that
        pairs with :meth:`invalidate_dependents` below.
        """
        if self.conn.execute("SELECT 1 FROM atom WHERE id = ?", (atom_id,)).fetchone() is None:
            raise KeyError(f"unknown atom {atom_id}")
        fids = [fid for fid, _ in self.facets_of_atom(atom_id)]
        if old_text != new_text:
            for fid in fids:
                self._ensure_accum(fid)
            for fid in fids:
                self._bump(fid, old_text, -1)
                self._bump(fid, new_text, +1)
                self._persist_accum(fid, commit=commit)
            if commit:
                self.conn.commit()
        return {"atom_id": atom_id, "facets": sorted(fids), "changed": old_text != new_text}

    def check_centroid_drift(self, facet_id: str, tol: float = CENTROID_TOL) -> dict:
        """Drift-check hook: incremental sum vs independent exact recompute.

        Compares the running accumulator against a fresh exact scan AND the
        persisted blob. A failing report is the trigger for periodic
        reconciliation (R09 measures long-sequence drift; no background
        reconciler is implemented in this packet).
        """
        self.taxonomy.get_facet(facet_id)
        exact_sum, exact_count = self._scan_facet_sum(facet_id)
        entry = self._accum.get(facet_id)
        norm = math.sqrt(sum(v * v for v in exact_sum))
        exact = [v / norm for v in exact_sum] if (exact_count and norm > 0) else None
        if entry is not None and int(entry["count"]) > 0:
            total = entry["sum"]
            inorm = math.sqrt(sum(v * v for v in total))
            inc = [v / inorm for v in total] if inorm > 0 else list(total)
        else:
            inc = None
        blob_row = self.conn.execute(
            "SELECT centroid_blob FROM facets WHERE facet_id = ?", (facet_id,)
        ).fetchone()
        blob = unpack_vector(bytes(blob_row[0])) if blob_row and blob_row[0] is not None else None
        if exact is None or inc is None:
            diff = 0.0 if exact is None and inc is None else float("inf")
            l2 = diff
        else:
            diff = max(abs(a - b) for a, b in zip(exact, inc))
            l2 = math.sqrt(sum((a - b) ** 2 for a, b in zip(exact, inc)))
        blob_diff = (
            max(abs(a - b) for a, b in zip(blob, inc))
            if blob is not None and inc is not None
            else (0.0 if blob is None and inc is None else float("inf"))
        )
        count_ok = (int(entry["count"]) if entry is not None else 0) == exact_count
        blob_ok = (blob is None) == (exact is None)
        ok = diff <= tol and blob_diff <= tol and count_ok and blob_ok
        return {
            "facet_id": facet_id,
            "max_abs_diff": diff,
            "l2_diff": l2,
            "blob_diff": blob_diff,
            "count_ok": count_ok,
            "blob_ok": blob_ok,
            "exact_count": exact_count,
            "accum_count": int(entry["count"]) if entry is not None else 0,
            "tol": tol,
            "ok": ok,
        }

    def ghost_facet_ids(self) -> list[str]:
        """Facets holding a centroid (RAM or blob) with zero members.

        Tier-2 beams must never spend slots on these (packet C6-2: 0 ghost
        slots). Empty-holon descriptor clearing (R05) is the holon-side
        analogue; this is the facet-side audit.
        """
        active = {
            r[0] for r in self.conn.execute("SELECT DISTINCT facet_id FROM memberships").fetchall()
        }
        ghosts = {
            r[0]
            for r in self.conn.execute(
                "SELECT facet_id FROM facets WHERE centroid_blob IS NOT NULL"
            ).fetchall()
            if r[0] not in active
        }
        ghosts |= {fid for fid in self._centroids if fid not in active}
        ghosts |= {fid for fid, e in self._accum.items()
                   if fid not in active and (int(e["count"]) > 0)}
        return sorted(ghosts)

    # -- R06 bounded invalidation + repair (ARCH §6) -------------------
    def invalidate_dependents(
        self,
        requires: list[tuple[str, str]],
        *,
        exclude: set[str] | frozenset = frozenset(),
        budget: int = INVALIDATION_BUDGET,
    ) -> dict:
        """Mark dependent syntheses ``stale`` immediately (version check).

        BFS over the ``dependency`` reverse index (every derived kind is
        traversed transitively) plus holon-containment parents, with a
        visited set and a work budget — mirroring the R05 closure walk but
        writing the packet-mandated ``stale`` status (R05 readers filter
        ``status = 'active'``, so ``stale`` rows are hidden from active-only
        reads exactly like ``invalidated`` ones; R05 ``revalidate()`` stays
        a no-op on them — repair below owns ``stale → active``). The edited
        object itself (``exclude``) stays active; only dependents change.
        Non-holon derived rows (assertion/mention/relation/run) are
        traversed but never status-flipped (they carry no status column).
        On budget exhaustion the transaction rolls back and
        :class:`InvalidationBudgetError` is raised: invalidation is
        all-or-nothing, repair is where partial progress lives.
        """
        queue = [(str(k), str(i)) for k, i in requires]
        visited: set[tuple[str, str]] = set(queue)
        invalidated: list[str] = []
        steps = 0
        try:
            while queue:
                kind, rid = queue.pop(0)
                derived = [
                    (str(r[0]), str(r[1]))
                    for r in self.conn.execute(
                        "SELECT derived_kind, derived_id FROM dependency"
                        " WHERE requires_kind = ? AND requires_id = ?",
                        (kind, rid),
                    ).fetchall()
                ]
                if kind == "holon":
                    derived += [
                        ("holon", str(r[0]))
                        for r in self.conn.execute(
                            "SELECT parent_holon_id FROM holon_containment"
                            " WHERE child_holon_id = ?",
                            (rid,),
                        ).fetchall()
                    ]
                for dk, did in sorted(set(derived)):
                    steps += 1
                    if steps > budget:
                        raise InvalidationBudgetError(
                            f"invalidation closure exceeded budget {budget}"
                        )
                    if (dk, did) in visited:
                        continue
                    visited.add((dk, did))
                    if dk == "holon":
                        if did in exclude:
                            queue.append((dk, did))
                            continue
                        srow = self.conn.execute(
                            "SELECT status FROM holon_state WHERE holon_id = ?",
                            (did,),
                        ).fetchone()
                        if srow is None or str(srow[0]) != "active":
                            continue
                        self.conn.execute(
                            "UPDATE holon_state SET status = 'stale', updated_at = ?"
                            " WHERE holon_id = ?",
                            (_utcnow(), did),
                        )
                        _snapshot_holon_state(self.conn, did)
                        invalidated.append(did)
                    queue.append((dk, did))
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return {
            "requires": [[k, i] for k, i in requires],
            "invalidated": sorted(invalidated),
            "closure_size": len(visited),
            "steps": steps,
            "budget": budget,
        }

    def repair_stale_holons(self, holon_ids: list[str], *, budget: int) -> dict:
        """Repair stale syntheses in a bounded batch (one repair job).

        Each repaired holon is re-derived from its LIVE members with the
        exact R05 formulas (weighted S_h/W_h, top-3 centroid-nearest
        representatives, token-csv descriptor, member digest) and flipped
        ``stale → active`` with a version-snapshot row. ``budget`` caps how
        many holons this call repairs; leftovers are ``deferred`` and the
        call reports ``status = 'incomplete'`` (ARCH §6). Full rebuild
        remains the oracle — see :meth:`verify_rebuild_equivalence`.
        Returns touched objects/bytes + queue drain time (EVAL §5).
        """
        start = time.perf_counter()
        todo = sorted(set(holon_ids))
        repaired: list[str] = []
        skipped: list[str] = []
        touched_members = 0
        touched_bytes = 0
        allowance = max(0, budget)
        for hid in todo:
            srow = self.conn.execute(
                "SELECT status FROM holon_state WHERE holon_id = ?", (hid,)
            ).fetchone()
            if srow is None:
                raise KeyError(f"unknown holon {hid}")
            if str(srow[0]) != "stale":
                skipped.append(hid)
                continue
            if len(repaired) >= allowance:
                continue
            members = self.conn.execute(
                "SELECT m.atom_id, m.weight, m.role, m.member_kind,"
                " m.member_version, a.text FROM holon_members m"
                " JOIN atom a ON a.id = m.atom_id WHERE m.holon_id = ?",
                (hid,),
            ).fetchall()
            dim = _holon_vector_dim(self.conn, hid)
            S = [0.0] * dim
            W = 0.0
            for m in members:
                w = float(m[1])
                for j, v in enumerate(text_vector(str(m[5]), dim)):
                    S[j] += w * v
                W += w
            texts = [str(m[5]) for m in members]
            aids = [int(m[0]) for m in members]
            if members:
                norm = math.sqrt(sum(v * v for v in S))
                centroid = [v / norm for v in S] if norm > 0 else list(S)
                scored = sorted(
                    (-sum(a * b for a, b in zip(centroid, text_vector(t, dim))), aid)
                    for aid, t in zip(aids, texts)
                )
                reps = [aid for _, aid in scored[:3]]
                blob: bytes | None = pack_vector(S)
                desc = _holon_descriptor(texts)
            else:  # empty holons clear descriptors (R05 empty-clears rule)
                blob, desc, reps = None, "", []
            digest = _holon_digest(members)
            touched_members += len(members)
            touched_bytes += sum(len(t.encode("utf-8")) for t in texts)
            self.conn.execute(
                "UPDATE holon_state SET status = 'active', descriptor = ?,"
                " vector_sum = ?, weight_sum = ?, member_count = ?,"
                " representatives_json = ?, member_digest = ?, updated_at = ?"
                " WHERE holon_id = ?",
                (desc, blob, W, len(members), json.dumps(reps), digest,
                 _utcnow(), hid),
            )
            _snapshot_holon_state(self.conn, hid)
            repaired.append(hid)
        self.conn.commit()
        deferred = [h for h in todo if h not in repaired and h not in skipped]
        return {
            "repaired": sorted(repaired),
            "skipped": sorted(skipped),
            "deferred": sorted(deferred),
            "status": "complete" if not deferred else "incomplete",
            "budget": budget,
            "touched_objects": len(repaired) + touched_members,
            "touched_members": touched_members,
            "touched_bytes": touched_bytes,
            "elapsed_s": time.perf_counter() - start,
        }

    def verify_rebuild_equivalence(self, holon_ids: list[str]) -> dict:
        """Full-rebuild oracle check: stored rows vs exact recompute.

        Re-derives every listed holon from live members with the exact R05
        formulas and diffs all persisted accumulator fields. This is the
        equivalence test against full rebuild (packet: full rebuild remains
        the oracle).
        """
        mismatches: list[str] = []
        max_diff = 0.0
        for hid in sorted(set(holon_ids)):
            srow = self.conn.execute(
                "SELECT descriptor, vector_sum, weight_sum, member_count,"
                " representatives_json, member_digest FROM holon_state"
                " WHERE holon_id = ?",
                (hid,),
            ).fetchone()
            if srow is None:
                mismatches.append(f"{hid}: missing holon_state row")
                continue
            members = self.conn.execute(
                "SELECT m.atom_id, m.weight, m.role, m.member_kind,"
                " m.member_version, a.text FROM holon_members m"
                " JOIN atom a ON a.id = m.atom_id WHERE m.holon_id = ?",
                (hid,),
            ).fetchall()
            dim = _holon_vector_dim(self.conn, hid)
            S = [0.0] * dim
            W = 0.0
            for m in members:
                w = float(m[1])
                for j, v in enumerate(text_vector(str(m[5]), dim)):
                    S[j] += w * v
                W += w
            stored_S = unpack_vector(bytes(srow[1])) if srow[1] is not None else None
            if members and stored_S is not None and len(stored_S) == dim:
                diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(S, stored_S)))
                max_diff = max(max_diff, diff)
                if diff > 1e-9:
                    mismatches.append(f"{hid}: vector_sum diff {diff:.3g}")
            elif bool(members) != (stored_S is not None):
                mismatches.append(f"{hid}: vector presence mismatch")
            if abs(float(srow[2]) - W) > 1e-9:
                mismatches.append(f"{hid}: weight_sum {srow[2]!r} != {W!r}")
            if int(srow[3]) != len(members):
                mismatches.append(f"{hid}: member_count {srow[3]!r} != {len(members)}")
            texts = [str(m[5]) for m in members]
            if str(srow[0]) != _holon_descriptor(texts):
                mismatches.append(f"{hid}: descriptor mismatch")
            if str(srow[5]) != _holon_digest(members):
                mismatches.append(f"{hid}: digest mismatch")
        return {
            "checked": len(set(holon_ids)),
            "mismatches": mismatches,
            "max_vector_diff": max_diff,
            "ok": not mismatches,
        }


def _snapshot_holon_state(conn: sqlite3.Connection, holon_id: str) -> int:
    """Append a ``holon_version`` row mirroring the R05 snapshot shape.

    Same columns R05 ``_snapshot`` writes (kind/status/descriptor/
    derivation/weight_sum/member_count/representatives/member_digest);
    additive row, never a rewrite — R05 history stays intact.
    """
    state = conn.execute(
        "SELECT kind, status, descriptor, derivation, weight_sum, member_count,"
        " representatives_json, member_digest FROM holon_state WHERE holon_id = ?",
        (holon_id,),
    ).fetchone()
    prev = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM holon_version WHERE holon_id = ?",
        (holon_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO holon_version (holon_id, version, kind, status, descriptor,"
        " derivation, weight_sum, member_count, representatives_json,"
        " member_digest, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (holon_id, int(prev) + 1, str(state[0]), str(state[1]), str(state[2]),
         str(state[3]), float(state[4]), int(state[5]), str(state[6]),
         str(state[7]), _utcnow()),
    )
    return int(prev) + 1


def _holon_vector_dim(conn: sqlite3.Connection, holon_id: str) -> int:
    """Accumulator dimension for a holon, read from its stored vector.

    Adapts automatically if a future encoder-space generation changes the
    dimension (ARCH §5: encoder changes create a new generation); falls back
    to ``CENTROID_DIM`` (== R05 ``ACC_DIM``) for empty holons.
    """
    row = conn.execute(
        "SELECT vector_sum FROM holon_state WHERE holon_id = ?", (holon_id,)
    ).fetchone()
    if row is not None and row[0] is not None and len(bytes(row[0])) >= 8:
        return len(bytes(row[0])) // 8
    return CENTROID_DIM


def _holon_descriptor(texts: list[str]) -> str:
    """Short token summary (top-3 weighted tokens) — never fused prose.

    Mirrors the R05 descriptor formula exactly (Counter over vector tokens,
    sort by (-count, token), first three).
    """
    if not texts:
        return ""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(vector_tokens(text))
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return ",".join(tok for tok, _ in top)


def _holon_digest(members) -> str:
    """Member digest mirroring the R05 formula (kind:id:weight:role, sha256).

    Empty holons carry no digest (cleared, not hashed).
    """
    members = list(members)
    if not members:
        return ""
    parts = sorted(f"{r[3]}:{r[0]}:{float(r[1]):.6f}:{r[2] or ''}" for r in members)
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:32]
