"""Cascade orchestrator: Tier 1 lexical -> Tier 2 faceted beam -> Tier 3 hybrid, with abstention."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field

from ..filing.engine import FilingEngine
from .beam import FacetedBeam
from .hybrid import FlatHybrid
from .lexical import has_lexical_support, is_syntax_dense, lexical_search

THETA_FLOOR = 0.35
LEXICAL_MIN_HITS = 3
LEXICAL_MIN_SCORE = 0.5
#: Cap on holon co-member extras blended into one served result (bounded).
HOLON_EXPAND_CAP = 5
#: Seeds (top primary hits) expanded through shared holon membership.
HOLON_EXPAND_SEEDS = 3
#: Served in-memory vector builds above this atom count are skipped (the
#: full-corpus file publication in ``index_adapter``/``projection`` stays the
#: large-corpus path); the cascade then serves the named lexical fallbacks.
SERVED_VECTOR_MAX_ATOMS = 20000


@dataclass
class QueryResult:
    query: str
    anchors: list[dict] = field(default_factory=list)
    tier_used: int = 3
    elapsed_ms: float = 0.0
    verdict: str = "insufficient_evidence"
    # Honest branch label for the primary branch that served this result:
    # lexical | vector | beam | hybrid | none. ``tier_used`` stays the legacy
    # position bucket (vector reports 2, same cascade slot as the beam) for
    # backward compatibility; ``route`` is the authoritative branch label.
    route: str = "none"
    routes: dict = field(default_factory=dict)  # per-anchor route counts
    vector_adequate: bool | None = None  # None = vector branch unavailable
    vector_generation_id: str | None = None
    # Vector-base generation the attached view was built from (None =
    # unknown/legacy or branch detached). Pin (vector_generation_id) is the
    # caller-requested filing generation; base is what the vectors cover.
    # Skew (pin != base) after a post-seed edit without compaction is the
    # honest signal, not a mislabel.
    vector_base_generation_id: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "anchors": self.anchors,
            "tier_used": self.tier_used,
            "elapsed_ms": self.elapsed_ms,
            "sufficiency_state": "answerable" if self.verdict == "answerable" else "insufficient_evidence",
            "route": self.route,
            "routes": dict(self.routes),
            "vector_adequate": self.vector_adequate,
            "vector_generation_id": self.vector_generation_id,
            "vector_base_generation_id": self.vector_base_generation_id,
            "note": self.note,
        }


class CascadeRouter:
    """3-tier dual-dispatch router. Call warm() once after ingest for MED-01 compliance."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        engine: FilingEngine | None = None,
        theta_floor: float = THETA_FLOOR,
        profile: str = "quality",
        beam_candidate_cap: int | None = 400,
        beam_sample_strategy: str = "balanced",
    ) -> None:
        self.conn = conn
        self.engine = engine if engine is not None else FilingEngine(conn)
        self.theta_floor = theta_floor
        if profile not in ("quality", "latency"):
            raise ValueError(f"unknown profile: {profile!r}")
        self.profile = profile
        self.rank_pools = profile == "quality"
        self.dense_scan_cap = None if profile == "quality" else 2000
        # Tier 2 candidate budget is capped at 400 (F3 specification).
        # Balanced head+tail sampling ensures recent and foundational documents across
        # large facets are represented without being truncated out by rowid order.
        self.beam_candidate_cap = beam_candidate_cap
        self.beam_sample_strategy = beam_sample_strategy
        self.beam = FacetedBeam(
            conn,
            self.engine,
            candidate_cap=self.beam_candidate_cap,
            sample_strategy=self.beam_sample_strategy,
        )
        self.hybrid = FlatHybrid(conn)
        self.sparse_pool = 100 if profile == "quality" else None
        self.warmed = False
        # Served R03 ANN sidecar (INT-2): an in-memory VectorIndex/IVF over the
        # pinned encoder space, built from the reader's snapshot and pinned to
        # the serving generation. None until attached/built — the cascade then
        # serves exactly the legacy branches (named baselines, unchanged).
        self._vector = None
        self._vector_generation_id: str | None = None
        self._vector_note: str = "vector branch unavailable: no index attached"
        # Vector-base generation the attached view was built from (additive
        # introspection; None = unknown/legacy-unset). The pin label
        # (_vector_generation_id) is the caller-requested generation; the base
        # is what the vectors actually cover. Coordinator surfaces both.
        self._vector_base_generation: str | None = None
        # Pin-keyed live-id cache for _vector_search: live rows change only
        # on publication, and every publication re-pins the branch — so one
        # (id, pin) keyed fetch per pin replaces the per-query full id scan.
        self._live_cache_key: tuple | None = None
        self._live_cache: frozenset = frozenset()
        # Holon-guided expansion (ARCH §5 H→C): co-members of live
        # thematic_cluster holons blended as route="holon" candidates. Off by
        # default (library behavior unchanged); the served path enables it.
        self.holon_guided: bool = False

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
        matrix, pos, idf = self.hybrid.matrix_view()
        self.beam.set_vectors(matrix, pos, idf)
        self.warmed = True
        return {"centroids": len(self.engine._centroids), "vectors": vectors}

    def add_atom(self, atom_id: int, text: str) -> None:
        """Incrementally index an atom and update shared views without a full re-scan."""
        self.hybrid.add_atom(atom_id, text)
        matrix, pos, idf = self.hybrid.matrix_view()
        self.beam.set_vectors(matrix, pos, idf)

    def remove_atom(self, atom_id: int) -> None:
        """Incrementally unindex an atom and update shared views."""
        self.hybrid.remove_atom(atom_id)
        matrix, pos, idf = self.hybrid.matrix_view()
        self.beam.set_vectors(matrix, pos, idf)

    # -- served R03 ANN sidecar (INT-2 glue; R03 index semantics untouched) --
    @property
    def vector_generation_id(self) -> str | None:
        """Generation the attached vector view is pinned to (None = none)."""
        return self._vector_generation_id

    def attach_vector(self, index, generation_id: str) -> None:
        """Pin a caller-built VectorIndex as the served ANN branch."""
        self._vector = index
        self._vector_generation_id = str(generation_id)
        self._vector_note = ""
        self._vector_base_generation = None
        self._live_cache_key = None
        self._live_cache = frozenset()

    def detach_vector(self, note: str = "vector branch detached") -> None:
        self._vector = None
        self._vector_generation_id = None
        self._vector_note = note
        self._vector_base_generation = None
        self._live_cache_key = None
        self._live_cache = frozenset()

    def refresh_served_vector(
        self,
        generation_id: str,
        *,
        encoder=None,
        max_atoms: int = SERVED_VECTOR_MAX_ATOMS,
    ) -> dict:
        """Build the served ANN view from this reader's snapshot, pinned to
        ``generation_id`` (call when the serving generation swaps).

        The encoder seam: pass any object with the ``HashBowEncoder`` surface
        (``fit_idf``/``encode_weighted``/``config_hash``); the default pinned
        degraded encoder applies unless INT-3 sets a served default via
        ``router.learned.set_served_default`` (None = fallback, unchanged).
        INT-3 plugs learned weights behind this same seam — no weight
        fetching happens here.
        """
        from .encoder import HashBowEncoder
        from .index_adapter import VectorIndex

        total = int(self.conn.execute("SELECT COUNT(*) FROM atom").fetchone()[0])
        if total == 0:
            self.detach_vector("vector branch unavailable: empty corpus")
            return {"built": False, "reason": "empty", "atoms": 0}
        if total > max_atoms:
            self.detach_vector(
                "vector branch unavailable: corpus exceeds served build cap"
            )
            return {"built": False, "reason": "corpus_too_large", "atoms": total}
        enc = encoder
        if enc is None:
            try:
                from .learned import served_default as _served_default
            except Exception:
                _served_default = lambda: None  # noqa: E731
            try:
                enc = _served_default()
            except Exception:
                enc = None
        if enc is None:
            enc = HashBowEncoder()
        rows = self.conn.execute(
            "SELECT id, text FROM atom ORDER BY id"
        ).fetchall()
        texts = [str(r[1]) for r in rows]
        enc.fit_idf(texts)
        import numpy as _np

        ids = [int(r[0]) for r in rows]
        if hasattr(enc, "encode_many"):
            mat = _np.asarray(enc.encode_many(texts), dtype=_np.float32)
        else:
            mat = _np.asarray(
                [enc.encode_weighted(t) for t in texts], dtype=_np.float32
            )
        max_id = max(ids)
        index = VectorIndex(enc)
        index.build(
            ids, mat, generation_id=str(generation_id),
            watermark=f"atoms={total}:maxid={max_id}",
        )
        self.attach_vector(index, str(generation_id))
        # Fresh build from this reader's snapshot: the base fully covers the pin.
        self._vector_base_generation = str(generation_id)
        return {
            "built": True, "atoms": total,
            "generation_id": str(generation_id),
            "encoder": enc.spec.label(),
            "encoder_config_hash": enc.config_hash(),
        }

    def _vector_search(self, query: str, limit: int) -> tuple[list[dict], dict]:
        """IVF search with a current-revision allow-list (ARCH §7).

        Live ``atom`` rows ARE the current set (superseded/retracted rows are
        deleted by the R01 lexical delta), so the allow-list is both a version
        filter and a staleness guard for an index built at an older pin: ids
        the index holds but the snapshot no longer serves count as
        ``filtered_out`` and flip the R03 ``adequate`` flag instead of
        claiming "no evidence".
        """
        assert self._vector is not None
        qvec = self._vector.encoder.encode_weighted(query)
        key = (self._vector_generation_id, id(self._vector))
        if key != self._live_cache_key:
            self._live_cache = frozenset(
                r[0] for r in self.conn.execute("SELECT id FROM atom").fetchall()
            )
            self._live_cache_key = key
        allowed = self._live_cache & self._vector.live_ids()
        hits, info = self._vector.search(qvec, k=limit, allowed=allowed)
        if not hits:
            return [], info
        marks = ",".join("?" for _ in hits)
        by_id = {
            r[0]: dict(r)
            for r in self.conn.execute(
                f"SELECT * FROM atom WHERE id IN ({marks})",
                tuple(aid for aid, _ in hits),
            ).fetchall()
        }
        scored = dict(hits)
        out = []
        for aid, _ in hits:
            row = by_id.get(aid)
            if row is None:  # snapshot skew inside one pin: never serve ghosts
                info["filtered_out"] = int(info.get("filtered_out", 0)) + 1
                continue
            row["score"] = float(scored[aid])
            row["route"] = "vector"
            out.append(row)
        # Served adequacy is narrower than the raw R03 flag: the R03 flag
        # flips whenever a filtered search returns short, even when the
        # filter removed nothing (genuinely few relevant docs). Served reads
        # fall through only when the filter actually hid candidates.
        info["served_adequate"] = bool(info.get("adequate", True)) or int(
            info.get("filtered_out", 0)
        ) == 0
        return out, info

    def _holon_expand(
        self,
        seed_scores: dict[int, float],
        exclude: set[int],
        cap: int = HOLON_EXPAND_CAP,
    ) -> list[dict]:
        """Co-members of live thematic_cluster holons holding the seeds.

        Only the pipeline-maintained corpus grouping guides retrieval (ARCH
        §5 H→C): derived syntheses and deleted holons never contribute. Empty
        when grouping tables are absent (pre-projection files) — never an
        error on the query path.
        """
        seeds = sorted(seed_scores)[:HOLON_EXPAND_SEEDS]
        if not seeds:
            return []
        try:
            names = {r[0] for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN"
                " ('holon_members', 'holon_state', 'atom')").fetchall()}
        except Exception:
            return []
        if names != {"holon_members", "holon_state", "atom"}:
            return []
        try:
            marks = ",".join("?" for _ in seeds)
            rows = self.conn.execute(
                "SELECT DISTINCT m2.atom_id FROM holon_members m1"
                " JOIN holon_members m2 ON m1.holon_id = m2.holon_id"
                " JOIN holon_state s ON s.holon_id = m1.holon_id"
                " WHERE m1.atom_id IN (%s) AND s.status = 'active'"
                " AND s.kind = 'thematic_cluster' ORDER BY m2.atom_id" % marks,
                [int(a) for a in seeds],
            ).fetchall()
        except Exception:
            return []
        floor = min(seed_scores.values())
        extras = []
        for (aid,) in rows:
            aid = int(aid)
            if aid in exclude or aid in seed_scores:
                continue
            atom = self.conn.execute(
                "SELECT * FROM atom WHERE id = ?", (aid,)).fetchone()
            if atom is None:
                continue
            row = dict(atom)
            row["score"] = round(0.5 * floor, 6)
            row["route"] = "holon"
            extras.append(row)
            if len(extras) >= cap:
                break
        return extras

    @staticmethod
    def _blend(primary: list[dict], extras: list[dict], limit: int) -> list[dict]:
        """Deterministic score blend, primary order kept on ties by id."""
        combined = sorted(primary + extras, key=lambda a: (-a["score"], a["id"]))
        return combined[:limit]

    @staticmethod
    def _routes(anchors: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for a in anchors:
            counts[str(a.get("route", "?"))] = counts.get(str(a.get("route", "?")), 0) + 1
        return counts

    def route(self, query: str, limit: int = 10, mode: str = "hybrid") -> QueryResult:
        """Dispatch tiers by plugin mode: tree → lexical+beam, flat → lexical+hybrid.

        Unknown modes fall back to the full hybrid cascade. Hybrid mode
        additionally tries the served R03 ANN (VectorIndex/IVF) branch between
        Tier 1 and the beam: on success the result is labeled route="vector"
        (tier_used 2, same cascade slot); on abstain or inadequate coverage
        the named beam/hybrid fallbacks serve instead — never silent.
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
            hits = lexical_search(
                self.conn, query, limit=limit, ranked=self.rank_pools,
                pool=self.sparse_pool,
            )
            confident = [h for h in hits if h["score"] >= LEXICAL_MIN_SCORE]
            if len(confident) >= LEXICAL_MIN_HITS:
                anchors = self._label(confident[:limit], "lexical")
                anchors = self._maybe_expand(query, anchors, limit)
                return QueryResult(
                    query=query, anchors=anchors, tier_used=1,
                    elapsed_ms=self._ms(start), verdict="answerable",
                    route="lexical", routes=self._routes(anchors),
                    vector_adequate=self._vector_adequacy(),
                    vector_generation_id=self._vector_generation_id,
                    vector_base_generation_id=self._vector_base_generation,
                )

        # Tier 2a (hybrid mode only): served R03 ANN over the pinned
        # generation. Same theta + anchor-corroboration gates as the other
        # dense branch; inadequate filtered coverage falls through to the
        # named fallbacks (ARCH §7 filtered-fallback rule).
        vector_info: dict | None = None
        if mode == "hybrid" and self._vector is not None:
            vhits, vector_info = self._vector_search(query, limit=limit)
            adequate = bool(vector_info.get("served_adequate", True))
            if not adequate:
                vhits = []  # filter hid candidates: named fallback below
            if vhits and vhits[0]["score"] >= self.theta_floor:
                if has_lexical_support(self.conn, query, anchor_text=str(vhits[0].get("text", ""))):
                    anchors = self._maybe_expand(query, vhits[:limit], limit)
                    return QueryResult(
                        query=query, anchors=anchors, tier_used=2,
                        elapsed_ms=self._ms(start), verdict="answerable",
                        route="vector", routes=self._routes(anchors),
                        vector_adequate=adequate,
                        vector_generation_id=self._vector_generation_id,
                        vector_base_generation_id=self._vector_base_generation,
                    )
            elif vhits:
                vhits = []  # gated out: abstain honestly, try named fallbacks

        # Tier 2b: faceted beam over warmed centroids.
        # Corroboration: check term overlap with the top candidate anchor.
        if allow_beam:
            beam_hits = self.beam.search(query, limit=limit)
            if beam_hits and beam_hits[0]["score"] >= self.theta_floor:
                if has_lexical_support(self.conn, query, anchor_text=str(beam_hits[0].get("text", ""))):
                    anchors = self._label(beam_hits, "beam")
                    anchors = self._maybe_expand(query, anchors, limit)
                    return QueryResult(
                        query=query, anchors=anchors, tier_used=2,
                        elapsed_ms=self._ms(start), verdict="answerable",
                        route="beam", routes=self._routes(anchors),
                        vector_adequate=self._vector_adequacy(vector_info),
                        vector_generation_id=self._vector_generation_id,
                        vector_base_generation_id=self._vector_base_generation,
                    )
            if not allow_flat:
                return QueryResult(
                    query=query, anchors=[], tier_used=2,
                    elapsed_ms=self._ms(start), verdict="insufficient_evidence",
                    vector_adequate=self._vector_adequacy(vector_info),
                    vector_generation_id=self._vector_generation_id,
                    vector_base_generation_id=self._vector_base_generation,
                    note=self._vector_note if self._vector is None else "",
                )

        # Tier 3: global flat hybrid fallback (named baseline, kept).
        if allow_flat:
            hybrid_hits = self.hybrid.search(
                query, limit=limit, ranked=self.rank_pools,
                scan_cap=self.dense_scan_cap, pool=self.sparse_pool,
            )
            if hybrid_hits and hybrid_hits[0]["score"] >= self.theta_floor:
                if has_lexical_support(self.conn, query, anchor_text=str(hybrid_hits[0].get("text", ""))):
                    anchors = self._label(hybrid_hits, "hybrid")
                    anchors = self._maybe_expand(query, anchors, limit)
                    return QueryResult(
                        query=query, anchors=anchors, tier_used=3,
                        elapsed_ms=self._ms(start), verdict="answerable",
                        route="hybrid", routes=self._routes(anchors),
                        vector_adequate=self._vector_adequacy(vector_info),
                        vector_generation_id=self._vector_generation_id,
                        vector_base_generation_id=self._vector_base_generation,
                    )
        return QueryResult(
            query=query,
            anchors=[],
            tier_used=3,
            elapsed_ms=self._ms(start),
            verdict="insufficient_evidence",
            vector_adequate=self._vector_adequacy(vector_info),
            vector_generation_id=self._vector_generation_id,
            vector_base_generation_id=self._vector_base_generation,
            note=self._vector_note if self._vector is None else "",
        )

    @staticmethod
    def _label(hits: list[dict], route: str) -> list[dict]:
        for h in hits:
            h["route"] = route
        return hits

    def _maybe_expand(self, query: str, anchors: list[dict], limit: int) -> list[dict]:
        """Blend holon-guided co-members when enabled (served path only)."""
        del query
        if not self.holon_guided or not anchors:
            return anchors
        seeds = {int(a["id"]): float(a.get("score", 0.0)) for a in anchors}
        exclude = {int(a["id"]) for a in anchors}
        extras = self._holon_expand(seeds, exclude)
        if not extras:
            return anchors
        return self._blend(anchors, extras, limit)

    def _vector_adequacy(self, info: dict | None = None) -> bool | None:
        if self._vector is None and info is None:
            return None
        if info is None:
            return True
        return bool(info.get("served_adequate", info.get("adequate", True)))

    @staticmethod
    def _ms(start: float) -> float:
        return (time.perf_counter() - start) * 1000.0
