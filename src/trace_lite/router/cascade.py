"""Cascade orchestrator: Tier 1 lexical -> Tier 2 faceted beam -> Tier 3 hybrid, with abstention."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from ..filing.engine import FilingEngine
from .beam import FacetedBeam
from .hybrid import FlatHybrid
from .lexical import is_syntax_dense, lexical_search

THETA_FLOOR = 0.35
LEXICAL_MIN_HITS = 3
LEXICAL_MIN_SCORE = 0.5


@dataclass
class QueryResult:
    query: str
    anchors: list[dict] = field(default_factory=list)
    tier_used: int = 3
    elapsed_ms: float = 0.0
    verdict: str = "insufficient_evidence"

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "anchors": self.anchors,
            "tier_used": self.tier_used,
            "elapsed_ms": self.elapsed_ms,
            "sufficiency_state": "answerable" if self.verdict == "answerable" else "insufficient_evidence",
        }


class CascadeRouter:
    """3-tier dual-dispatch router. Call warm() once after ingest for MED-01 compliance."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        engine: FilingEngine | None = None,
        theta_floor: float = THETA_FLOOR,
    ) -> None:
        self.conn = conn
        self.engine = engine if engine is not None else FilingEngine(conn)
        self.beam = FacetedBeam(conn, self.engine)
        self.hybrid = FlatHybrid(conn)
        self.theta_floor = theta_floor
        self.warmed = False

    def warm(self) -> dict[str, int]:
        """Preload facet centroids + dense matrix into RAM. Returns warm counts."""
        centroids = self.engine.warm_centroids()
        # Refresh centroids for facets that have members but no persisted blob yet.
        unbuilt = [
            r[0]
            for r in self.conn.execute("SELECT facet_id FROM facets WHERE centroid_blob IS NULL").fetchall()
        ]
        for fid in unbuilt:
            self.engine.refresh_centroid(fid)
        vectors = self.hybrid.warm()
        # Tier 2 shares Tier 3's warmed matrix: zero per-query vector recompute.
        matrix, pos = self.hybrid.matrix_view()
        self.beam.set_vectors(matrix, pos)
        self.warmed = True
        return {"centroids": len(self.engine._centroids), "vectors": vectors}

    def route(self, query: str, limit: int = 10, mode: str = "hybrid") -> QueryResult:
        """Dispatch tiers by plugin mode: tree → lexical+beam, flat → lexical+hybrid.

        Unknown modes fall back to the full hybrid cascade.
        """
        start = time.perf_counter()
        query = query.strip()
        if mode not in ("hybrid", "tree", "flat"):
            mode = "hybrid"
        allow_beam = mode in ("hybrid", "tree")
        allow_flat = mode in ("hybrid", "flat")
        if not query:
            return QueryResult(query=query, elapsed_ms=self._ms(start), verdict="insufficient_evidence")

        # Tier 1: lexical short-circuit for syntax-dense queries (all modes).
        if is_syntax_dense(query):
            hits = lexical_search(self.conn, query, limit=limit)
            confident = [h for h in hits if h["score"] >= LEXICAL_MIN_SCORE]
            if len(confident) >= LEXICAL_MIN_HITS:
                return QueryResult(
                    query=query, anchors=confident[:limit], tier_used=1,
                    elapsed_ms=self._ms(start), verdict="answerable",
                )

        # Tier 2: faceted beam over warmed centroids.
        if allow_beam:
            beam_hits = self.beam.search(query, limit=limit)
            if beam_hits and beam_hits[0]["score"] >= self.theta_floor:
                return QueryResult(
                    query=query, anchors=beam_hits, tier_used=2,
                    elapsed_ms=self._ms(start), verdict="answerable",
                )
            if not allow_flat:
                return QueryResult(
                    query=query, anchors=[], tier_used=2,
                    elapsed_ms=self._ms(start), verdict="insufficient_evidence",
                )

        # Tier 3: global flat hybrid fallback.
        if allow_flat:
            hybrid_hits = self.hybrid.search(query, limit=limit)
            if hybrid_hits and hybrid_hits[0]["score"] >= self.theta_floor:
                return QueryResult(
                    query=query, anchors=hybrid_hits, tier_used=3,
                    elapsed_ms=self._ms(start), verdict="answerable",
                )
        return QueryResult(
            query=query,
            anchors=[],
            tier_used=3,
            elapsed_ms=self._ms(start),
            verdict="insufficient_evidence",
        )

    @staticmethod
    def _ms(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0
