"""S4 persistent vector serving: cached embeddings + published base + bounded delta.

Steady-state contract (steering S4 row + line 85 methodology):

- Embedding outputs are cached keyed by (content sha256, encoder config
  hash); distinct source occurrences keep per-atom entries that may share
  one cached vector. A one-source edit encodes only the changed content
  (steady state: 1 encode, never a corpus re-encode).
- Vector *generation* (delta overlay + background compaction) is separate
  from the *published base*: readers open the CURRENT generation and keep
  serving it while a new generation is staged; the CURRENT flip is the
  single atomic publication step (``os.replace``), reusing the R03
  save -> manifest -> CURRENT -> generation-row protocol.
- The served view is ``published base + bounded delta/tombstones``. Delta
  size is bounded (``delta_bound``); breaching it requires compaction
  (merge into a new published generation). Tuning the bound is out of
  scope (S4 contract); the default is a plain constant.
- Hashbow IDF is frozen per served epoch (steering line 65): the epoch
  ``(config_hash, idf, doc_count)`` is recorded in the per-generation
  sidecar and inherited by the delta overlay. Compaction merges under the
  SAME epoch (never a silent IDF change); a new IDF epoch needs one
  explicit background rebuild (the transitional-repair path), never a
  query-triggered one.
- First query never builds: :meth:`ServedVectors.open` and
  :func:`ensure_served_persistent` only open what is published. With no
  published base they report detached/fallback instead of encoding.
- Crash coherence at every publication boundary comes from atomic file
  replacement plus generation-scoped sidecars (``s4-delta-<gen>.json``):
  a torn write can only leave a ``*.tmp`` file the opener ignores, and a
  CURRENT flip without its sidecar heals deterministically because the
  flipped generation already contains the merged content (see
  :meth:`ServedVectors.recover`).

File layout inside ``index_dir`` (shares the R03 directory; every S4 file
uses an ``s4-`` prefix so R03 orphan scans never match it):

- R03 files, untouched: ``segments/seg-<gen>.*.npy``,
  ``manifest-<gen>.json``, ``tombstones-<gen>.json``, ``CURRENT``.
- ``s4-delta-<gen>.json``: per-generation sidecar carrying the frozen IDF
  epoch plus the bounded overlay (upserts keyed by atom id, tombstones).
  Always written, even when the overlay is empty.
- ``s4-cache.json`` (optional): persisted embedding cache. Losing it is
  safe (a miss re-encodes); mixing encoder configs is refused on load.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from .encoder import HashBowEncoder
from .index_adapter import (
    GENERATION_KIND,
    VectorIndex,
    _atomic_write_json,
    _fsync_dir,
)

#: Default bound on overlay entries (upserts + tombstones) before a
#: compaction publish is required. A constant, not a tuned value: S4
#: parameter tuning is explicitly out of scope.
S4_DELTA_BOUND = 256

SIDECAR_FORMAT = "s4-delta/1"
CACHE_FORMAT = "s4-cache/1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sidecar_name(generation_id: str) -> str:
    return f"s4-delta-{generation_id}.json"


# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------

class EmbeddingCache:
    """Content-keyed cache of RAW (unweighted) encoder vectors.

    The cache stores the IDF-free base form (:meth:`HashBowEncoder.encode`);
    weighting with the generation's frozen IDF happens in
    :class:`ServedVectors`, so one cached entry serves every IDF epoch.
    ``encodes`` counts real encoder calls (cache misses); ``hits`` counts
    deduped re-encodes avoided. Both are the S4 update-cost evidence.
    """

    def __init__(self, encoder: HashBowEncoder | None = None) -> None:
        self.encoder = encoder or HashBowEncoder()
        self._vectors: dict[str, list[float]] = {}
        self.encodes = 0
        self.hits = 0

    @property
    def config_hash(self) -> str:
        return self.encoder.config_hash()

    def get_raw(self, text: str) -> list[float]:
        """Return the cached raw vector, encoding exactly once per content."""
        key = _content_key(text)
        vec = self._vectors.get(key)
        if vec is not None:
            self.hits += 1
            return vec
        vec = [float(v) for v in self.encoder.encode(text)]
        self._vectors[key] = vec
        self.encodes += 1
        return vec

    def stats(self) -> dict:
        return {
            "encodes": self.encodes,
            "hits": self.hits,
            "entries": len(self._vectors),
            "config_hash": self.config_hash,
        }

    def save(self, path: str | Path) -> dict:
        """Persist atomically (tmp + replace): a torn write keeps the old file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, {
            "format": CACHE_FORMAT,
            "config_hash": self.config_hash,
            "dim": self.encoder.dim,
            "entries": self._vectors,
        })
        return {"path": str(path), "entries": len(self._vectors)}

    @classmethod
    def load(cls, path: str | Path,
             encoder: HashBowEncoder | None = None) -> "EmbeddingCache":
        """Load a persisted cache; config mismatch starts empty (never mixes)."""
        cache = cls(encoder)
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        if body.get("format") != CACHE_FORMAT:
            raise ValueError(f"unknown cache format in {path}")
        if body.get("config_hash") != cache.config_hash:
            raise ValueError("encoder config hash mismatch: cache refused")
        if int(body.get("dim", -1)) != cache.encoder.dim:
            raise ValueError("encoder dim mismatch: cache refused")
        entries = body.get("entries", {})
        cache._vectors = {str(k): [float(v) for v in vlist]
                          for k, vlist in entries.items()}
        return cache


# ---------------------------------------------------------------------------
# Served vectors: published base + bounded delta
# ---------------------------------------------------------------------------

class ServedVectors:
    """S4 serving layer over one R03 index directory.

    ``open()`` loads the CURRENT published base plus its sidecar and never
    encodes or writes. Mutation (``ingest_atom``/``delete_atom``) touches
    only the bounded in-memory overlay and its sidecar file. ``publish()``
    compacts base + overlay into a new published generation through the
    crash-safe R03 protocol. All corpus text encoding flows through the
    shared :class:`EmbeddingCache`, which is also the update-cost meter.
    """

    def __init__(
        self,
        index_dir: str | Path,
        encoder: HashBowEncoder | None = None,
        cache: EmbeddingCache | None = None,
        delta_bound: int = S4_DELTA_BOUND,
    ) -> None:
        if np is None:  # pragma: no cover
            raise RuntimeError("numpy is required for ServedVectors")
        self.index_dir = Path(index_dir)
        self.encoder = encoder or HashBowEncoder()
        self.cache = cache if cache is not None else EmbeddingCache(self.encoder)
        if self.cache.config_hash != self.encoder.config_hash():
            raise ValueError("cache encoder config does not match serving encoder")
        self.delta_bound = int(delta_bound)
        # Published base (None until a generation is published).
        self.base: VectorIndex | None = None
        self.generation_id = ""
        # Frozen IDF epoch serving this view (None until seeded).
        self._idf: list[float] | None = None
        self._doc_count = 0
        # Bounded overlay: atom_id -> weighted serving vector; tombstoned ids.
        self._upserts: dict[int, list[float]] = {}
        self._tombstones: set[int] = set()
        self._note = "no published base opened"
        # Dedicated query encoder holding the frozen IDF (shares the spec,
        # never mutates the serving encoder's state).
        self._qenc = HashBowEncoder(self.encoder.spec)
        # Content version: bumped on every base/overlay mutation. The
        # materialized attach template is valid only at the version it was
        # built from; every attach at the same version reuses it instead of
        # re-merging + retraining (the request path then holds the snapshot
        # lock only for a pointer-speed cache hit).
        self._content_version = 0
        self._mat_template: VectorIndex | None = None
        self._mat_template_version = -1
        self._last_materialize_cached = False

    def _invalidate(self) -> None:
        """Drop the attach template: content moved, cached view is stale."""
        self._content_version += 1
        self._mat_template = None

    def clone(self) -> "ServedVectors":
        """Thread-private copy for background catch-up (G1 immutable serving).

        Shares the immutable published base, encoder, and content cache by
        reference (never mutated through the clone); copies the generation
        label, frozen epoch, overlay, and tombstones. The clone owns a fresh
        query encoder and an empty attach template. Mutating the clone never
        affects readers retaining this snapshot; the caller publishes the
        finished clone with one atomic pointer swap.
        """
        if np is None:  # pragma: no cover
            raise RuntimeError("numpy is required for ServedVectors")
        fresh = ServedVectors.__new__(ServedVectors)
        fresh.index_dir = self.index_dir
        fresh.encoder = self.encoder
        fresh.cache = self.cache
        fresh.delta_bound = self.delta_bound
        fresh.base = self.base
        fresh.generation_id = self.generation_id
        fresh._idf = list(self._idf) if self._idf is not None else None
        fresh._doc_count = self._doc_count
        fresh._upserts = dict(self._upserts)
        fresh._tombstones = set(self._tombstones)
        fresh._note = self._note
        fresh._qenc = HashBowEncoder(self.encoder.spec)
        fresh._content_version = self._content_version
        fresh._mat_template = None
        fresh._mat_template_version = -1
        fresh._last_materialize_cached = False
        return fresh

    def served_ids(self) -> set[int]:
        """Live served ids: base live rows plus overlay minus tombstones."""
        ids: set[int] = set()
        try:
            if self.base is not None:
                ids.update(int(a) for a in self.base.live_ids())
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            ids.update(int(a) for a in self._upserts)
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            ids.difference_update(int(a) for a in self._tombstones)
        except (AttributeError, TypeError, ValueError):
            pass
        return ids

    def apply_batch(self, upserts: list[tuple[int, str]],
                    deletes: list[int]) -> dict:
        """Apply an overlay batch with ONE sidecar write (G1 catch-up path).

        Same per-atom semantics as :meth:`ingest_atom`/:meth:`delete_atom`
        (content-deduped encodes, replay-stable, tombstone-aware) but the
        sidecar persists once and the content version bumps once, so a
        500-atom catch-up costs one file sync instead of 500. Intended for a
        thread-private :meth:`clone`, never for a published snapshot.
        """
        self._require_epoch()
        enc0 = self.cache.stats()["encodes"]
        changed_up = 0
        changed_del = 0
        for atom_id in deletes:
            atom_id = int(atom_id)
            had_upsert = self._upserts.pop(atom_id, None) is not None
            if atom_id in self._tombstones and not had_upsert:
                continue
            self._tombstones.add(atom_id)
            changed_del += 1
        for atom_id, text in upserts:
            weighted = self._weight(self.cache.get_raw(str(text)))
            atom_id = int(atom_id)
            prev = self._upserts.get(atom_id)
            if prev is not None and len(prev) == len(weighted) and all(
                    abs(a - b) < 1e-9 for a, b in zip(prev, weighted)):
                if atom_id in self._tombstones:
                    self._tombstones.discard(atom_id)
                    changed_up += 1
                continue
            self._upserts[atom_id] = weighted
            self._tombstones.discard(atom_id)
            changed_up += 1
        if changed_up or changed_del:
            self._write_sidecar(self.generation_id or "unpublished")
            self._invalidate()
        return {"encoded": self.cache.stats()["encodes"] - enc0,
                "upserts": changed_up, "deletes": changed_del,
                "delta_size": self.delta_size}

    @property
    def content_version(self) -> int:
        """Monotonic content version (bumped on every base/overlay change).

        Attach templates, pin-validity checks, and honest staleness
        accounting key off this: equal versions mean identical served bytes.
        """
        return self._content_version

    # -- state ------------------------------------------------------
    @property
    def servable(self) -> bool:
        """True when queries can be served (frozen epoch present)."""
        return self._idf is not None

    @property
    def note(self) -> str:
        return self._note

    @property
    def delta_size(self) -> int:
        return len(self._upserts) + len(self._tombstones)

    def needs_compaction(self) -> bool:
        return self.delta_size > self.delta_bound

    def _weight(self, raw: list[float]) -> list[float]:
        """Apply the frozen IDF epoch (same formula as ``encode_weighted``)."""
        assert self._idf is not None, "no frozen IDF epoch"
        import math

        vec = [v * w for v, w in zip(raw, self._idf)]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _query_vec(self, text: str):
        assert self._idf is not None, "no frozen IDF epoch"
        self._qenc.set_idf(list(self._idf), self._doc_count)
        return np.asarray(self._qenc.encode_weighted(text), dtype=np.float32)

    # -- open (never builds, never encodes) --------------------------
    @classmethod
    def open(
        cls,
        index_dir: str | Path,
        encoder: HashBowEncoder | None = None,
        cache: EmbeddingCache | None = None,
        delta_bound: int = S4_DELTA_BOUND,
    ) -> "ServedVectors":
        """Open the CURRENT published generation. Fail-closed, never a build.

        - No CURRENT pointer and no sidecars: empty, unservable view with an
          honest note (the caller serves named fallbacks; zero encodes).
        - CURRENT present: base loaded hash-verified with the encoder config
          enforced (stale-config generations raise instead of serving).
        - Sidecar for CURRENT missing (crash between CURRENT flip and
          sidecar write): adopt the newest sidecar with a matching config
          as the epoch with an empty overlay — coherent because the flipped
          generation already merged that overlay's content. The note records
          the adoption; :meth:`recover` rewrites the proper sidecar.
        - Sidecar present: epoch + overlay loaded; config mismatch raises.
        """
        served = cls(index_dir, encoder=encoder, cache=cache,
                     delta_bound=delta_bound)
        current = served.index_dir / "CURRENT"
        if not current.exists():
            served._note = "no published base: CURRENT pointer absent"
            return served
        base, info = VectorIndex.open(
            served.index_dir, expected_config_hash=served.encoder.config_hash())
        served.base = base
        served.generation_id = str(info["generation_id"])
        sidecar = served.index_dir / _sidecar_name(served.generation_id)
        if sidecar.exists():
            served._load_sidecar(sidecar)
            served._note = ""
        else:
            adopted = served._adopt_newest_epoch()
            served._note = (
                "sidecar absent for CURRENT generation"
                f" {served.generation_id}; adopted epoch"
                f" from {adopted} with empty overlay (crash heal pending)"
                if adopted else
                "sidecar absent for CURRENT generation and no epoch to"
                " adopt: pre-S4 generation needs one explicit background"
                " rebuild (transitional repair)"
            )
        return served

    def _load_sidecar(self, path: Path) -> None:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("format") != SIDECAR_FORMAT:
            raise ValueError(f"unknown sidecar format in {path}")
        if body.get("config_hash") != self.encoder.config_hash():
            raise ValueError(
                f"encoder config hash mismatch in {path.name}: sidecar epoch"
                " is stale, refusing to serve")
        if body.get("generation_id") != self.generation_id and self.generation_id:
            raise ValueError(
                f"generation mismatch in {path.name}: expected"
                f" {self.generation_id}, found {body.get('generation_id')}")
        self._idf = [float(v) for v in body["idf"]]
        self._doc_count = int(body.get("doc_count", 0))
        self._upserts = {int(k): [float(v) for v in vlist]
                         for k, vlist in body.get("upserts", {}).items()}
        self._tombstones = {int(t) for t in body.get("tombstones", [])}

    def _adopt_newest_epoch(self) -> str | None:
        """Adopt the newest config-matching sidecar epoch (crash heal path)."""
        candidates = sorted(self.index_dir.glob("s4-delta-*.json"))
        for path in reversed(candidates):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if body.get("config_hash") != self.encoder.config_hash():
                continue
            try:
                idf = [float(v) for v in body["idf"]]
            except (KeyError, TypeError, ValueError):
                continue
            self._idf = idf
            self._doc_count = int(body.get("doc_count", 0))
            self._upserts = {}
            self._tombstones = set()
            return path.name
        return None

    def _write_sidecar(self, generation_id: str) -> Path:
        assert self._idf is not None, "no frozen IDF epoch to persist"
        path = self.index_dir / _sidecar_name(generation_id)
        _atomic_write_json(path, {
            "format": SIDECAR_FORMAT,
            "generation_id": generation_id,
            "config_hash": self.encoder.config_hash(),
            "idf": list(self._idf),
            "doc_count": self._doc_count,
            "upserts": {str(k): list(v) for k, v in self._upserts.items()},
            "tombstones": sorted(self._tombstones),
            "updated_at": _utcnow(),
        })
        return path

    # -- seeding: the one explicit background build (transitional) ----
    def seed_from_db(self, conn: sqlite3.Connection, generation_id: str,
                     watermark: str = "", batch: int = 2000,
                     nprobe: int = 8) -> dict:
        """Build the first published generation from the live atom table.

        This is the transitional full build: every DISTINCT content encodes
        once through the cache, the IDF epoch freezes here, and the result
        publishes through the crash-safe R03 protocol. Steady state after
        this call is incremental (``ingest_atom``/``delete_atom``/``publish``)
        — repeated full re-encoding must not be the edit strategy.
        """
        from .projection import iter_atom_batches

        assert np is not None
        texts: list[str] = []
        ids: list[int] = []
        raws: list[list[float]] = []
        for chunk in iter_atom_batches(conn, batch):
            for aid, text in chunk:
                ids.append(aid)
                texts.append(text)
                raws.append(self.cache.get_raw(text))
        # Freeze the IDF epoch for this generation (explicit, recorded).
        self.encoder.fit_idf(texts)
        self._idf = list(self.encoder._idf or [])
        self._doc_count = len(texts)
        mat = np.asarray([self._weight(r) for r in raws], dtype=np.float32)
        index = VectorIndex(self.encoder, nprobe=nprobe)
        index.build(ids, mat, generation_id=generation_id, watermark=watermark)
        receipt = index.publish(self.index_dir, conn,
                                generation_id=generation_id, watermark=watermark)
        self.base = index
        self.generation_id = str(generation_id)
        self._upserts = {}
        self._tombstones = set()
        self._write_sidecar(str(generation_id))
        self._note = ""
        self._invalidate()
        receipt.update({
            "seeded": True, "atoms": len(ids),
            "cache": self.cache.stats(),
        })
        return receipt

    # -- steady-state mutation: overlay only, never a rebuild --------
    def _require_epoch(self) -> None:
        if self._idf is None:
            raise RuntimeError(
                "no frozen IDF epoch: seed_from_db() (explicit background"
                " build) must run before steady-state mutation")

    def ingest_atom(self, atom_id: int, text: str) -> dict:
        """Overlay one atom's vector. Encodes only on unseen content.

        Same atom + same content replays cost zero encodes and no write;
        distinct occurrences of identical content share one cached vector
        while keeping per-atom entries.
        """
        self._require_epoch()
        before = self.cache.stats()
        weighted = self._weight(self.cache.get_raw(text))
        encoded = self.cache.stats()["encodes"] - before["encodes"]
        atom_id = int(atom_id)
        prev = self._upserts.get(atom_id)
        if prev is not None and len(prev) == len(weighted) and all(
                abs(a - b) < 1e-9 for a, b in zip(prev, weighted)):
            if atom_id in self._tombstones:
                # Untombstoning resurrects the atom: content moved even
                # though the reported vector is unchanged.
                self._tombstones.discard(atom_id)
                self._invalidate()
            return {"encoded": False, "changed": False,
                    "delta_size": self.delta_size}
        self._upserts[atom_id] = weighted
        self._tombstones.discard(atom_id)
        self._write_sidecar(self.generation_id or "unpublished")
        self._invalidate()
        return {"encoded": bool(encoded), "changed": True,
                "delta_size": self.delta_size}

    def delete_atom(self, atom_id: int) -> dict:
        """Tombstone one atom. Segments stay immutable; zero encodes."""
        self._require_epoch()
        atom_id = int(atom_id)
        had_upsert = self._upserts.pop(atom_id, None) is not None
        if atom_id in self._tombstones and not had_upsert:
            return {"tombstoned": True, "changed": False,
                    "delta_size": self.delta_size}
        self._tombstones.add(atom_id)
        self._write_sidecar(self.generation_id or "unpublished")
        self._invalidate()
        return {"tombstoned": True, "changed": True,
                "delta_size": self.delta_size}

    # -- serving: base IVF + bounded delta merge ----------------------
    def _merged(self) -> tuple[list[int], "np.ndarray"]:
        """Merged serving matrix (base live rows minus overlay, plus upserts)."""
        assert np is not None and self._idf is not None
        ids: list[int] = []
        rows: list[np.ndarray] = []
        if self.base is not None and self.base._mat is not None:
            live = self.base.live_ids() - self._tombstones - set(self._upserts)
            pos = {aid: row for row, aid in enumerate(self.base.ids)}
            for aid in self.base.ids:
                if aid in live:
                    ids.append(aid)
                    rows.append(self.base._mat[pos[aid]])
        for aid in sorted(self._upserts):
            if aid in self._tombstones:
                continue
            ids.append(aid)
            rows.append(np.asarray(self._upserts[aid], dtype=np.float32))
        if rows:
            mat = np.stack(rows).astype(np.float32)
        else:
            mat = np.zeros((0, self.encoder.dim), dtype=np.float32)
        return ids, mat

    def search(self, query_text: str, k: int = 10,
               allowed: set[int] | None = None) -> tuple[list[tuple[int, float]], dict]:
        """Serve base IVF over-retrieved plus brute-forced delta, merged.

        Never encodes corpus content (one query-vector encode only) and
        never writes. Tombstoned ids and ids outside ``allowed`` count as
        ``filtered_out`` and flip ``adequate`` instead of claiming silence.
        """
        info = {"generation_id": self.generation_id,
                "base_scanned": 0, "delta_scanned": len(self._upserts),
                "filtered_out": 0, "adequate": True, "served": False}
        if not self.servable:
            info["note"] = self._note or "unservable: no frozen epoch"
            return [], info
        q = self._query_vec(query_text)
        if not bool(np.any(q)):
            return [], info
        candidates: dict[int, float] = {}
        if self.base is not None and self.base.count:
            over = k + self.delta_size
            base_hits, binfo = self.base.search(q, k=over, allowed=None)
            info["base_scanned"] = int(binfo.get("scanned", 0))
            for aid, score in base_hits:
                if aid in self._tombstones or aid in self._upserts:
                    info["filtered_out"] += 1
                    continue
                candidates[aid] = max(float(score), candidates.get(aid, 0.0))
        for aid, w in self._upserts.items():
            if aid in self._tombstones:
                continue
            sim = float(np.asarray(w, dtype=np.float32) @ q)
            if sim <= 0:
                continue
            candidates[aid] = max(sim, candidates.get(aid, 0.0))
        ordered = sorted(candidates.items(), key=lambda t: (-t[1], t[0]))
        hits: list[tuple[int, float]] = []
        for aid, score in ordered:
            if allowed is not None and aid not in allowed:
                info["filtered_out"] += 1
                continue
            hits.append((aid, score))
            if len(hits) >= k:
                break
        if allowed is not None and len(hits) < k:
            info["adequate"] = False
        info["served"] = True
        return hits, info

    def search_exact(self, query_text: str, k: int = 10,
                     allowed: set[int] | None = None) -> tuple[list[tuple[int, float]], dict]:
        """Brute-force oracle over the merged view (recall reference)."""
        ids, mat = self._merged()
        if not len(ids):
            return [], {"scanned": 0, "filtered_out": 0, "adequate": True}
        q = self._query_vec(query_text)
        nrm = float(np.linalg.norm(q)) or 1.0
        sims = mat @ (q / nrm)
        order = np.argsort(-sims, kind="stable")[: len(ids)]
        hits: list[tuple[int, float]] = []
        filtered = 0
        for row in order.tolist():
            aid = ids[int(row)]
            s = float(sims[int(row)])
            if aid in self._tombstones:
                filtered += 1
                continue
            if allowed is not None and aid not in allowed:
                filtered += 1
                continue
            if s <= 0:
                continue
            hits.append((aid, s))
            if len(hits) >= k:
                break
        adequate = not (allowed is not None and len(hits) < k)
        return hits, {"scanned": len(ids), "filtered_out": filtered,
                      "adequate": adequate}

    def recall_vs_exact(self, query_texts: list[str], k: int = 10,
                        allowed: set[int] | None = None) -> dict:
        """ANN recall@k of :meth:`search` vs :meth:`search_exact`."""
        hits = 0
        total = 0
        for text in query_texts:
            ref = {aid for aid, _ in self.search_exact(text, k, allowed)[0]}
            if not ref:
                continue
            got = {aid for aid, _ in self.search(text, k, allowed)[0]}
            total += 1
            hits += len(ref & got) / len(ref)
        return {"queries": total,
                "recall_at_k": round(hits / total, 4) if total else 1.0,
                "k": k}

    def materialize(self, nprobe: int = 8) -> VectorIndex:
        """Merge base + overlay into an in-memory index. No text encoding.

        Version-cached: the first call per content version merges + trains;
        every further call at the same version returns a fresh shell sharing
        the template's immutable array storage (no merge, no retrain, no
        aliasing risk — shells own only their generation label, nprobe, and
        tombstone set; every shared member is read-only outside build/open).
        Fast path: with nothing unmerged (empty overlay, no tombstones
        anywhere), the template shares the published base arrays directly —
        no merge, no KMeans retrain of identical content.
        """
        template = self._mat_template
        if template is None or self._mat_template_version != self._content_version:
            base = self.base
            try:
                base_clean = (base is not None and not base.tombstones
                              and base._mat is not None)
            except (AttributeError, TypeError):
                base_clean = False
            if base_clean and not self._upserts and not self._tombstones:
                assert base is not None
                template = VectorIndex(self.encoder, nprobe=nprobe)
                template.generation_id = self.generation_id or "unpublished"
                template.watermark = ""
                template.ids = base.ids
                template._mat = base._mat
                template._centroids = base._centroids
                template._assign = base._assign
                template._postings = base._postings
                template.tombstones = set()
            else:
                ids, mat = self._merged()
                template = VectorIndex(self.encoder, nprobe=nprobe)
                template.build(ids, mat,
                               generation_id=self.generation_id or "unpublished",
                               watermark="")
                template.tombstones = set(self._tombstones) & set(ids)
            self._mat_template = template
            self._mat_template_version = self._content_version
            self._last_materialize_cached = False
        else:
            self._last_materialize_cached = True
        shell = VectorIndex(self.encoder, nprobe=nprobe)
        shell.generation_id = template.generation_id
        shell.watermark = template.watermark
        shell.ids = template.ids
        shell._mat = template._mat
        shell._centroids = template._centroids
        shell._assign = template._assign
        shell._postings = template._postings
        shell.tombstones = set(template.tombstones)
        return shell

    def release(self) -> dict:
        """Drop the retained attach template (frees one index copy).

        Lifetime: previously returned shells stay valid — they share the
        template's arrays by reference, so Python keeps the storage alive
        exactly as long as any shell needs it. Next materialize() rebuilds.
        Never touches live serving state (base, overlay, epoch).
        """
        had = self._mat_template is not None
        self._mat_template = None
        return {"released": had}

    def describe(self) -> dict:
        """Honest snapshot introspection for generation/coverage accounting.

        Additive (no frozen shape changes): reports the vector-base
        generation, overlay size, compaction pressure, and content version
        so callers can distinguish "fresh base" from "stale base + overlay"
        without guessing from the pin label.
        """
        base_live = 0
        base_count = 0
        if self.base is not None:
            try:
                base_live = len(self.base.live_ids())
                base_count = self.base.count
            except (AttributeError, TypeError):
                pass
        return {
            "generation_id": self.generation_id,
            "servable": self.servable,
            "note": self._note,
            "base_count": base_count,
            "base_live": base_live,
            "delta_size": self.delta_size,
            "upserts": len(self._upserts),
            "tombstones": len(self._tombstones),
            "needs_compaction": self.needs_compaction(),
            "content_version": self._content_version,
            "config_hash": self.encoder.config_hash(),
        }

    # -- publication: controlled compaction ---------------------------
    def publish(self, conn: sqlite3.Connection | None, generation_id: str,
                watermark: str = "", nprobe: int = 8) -> dict:
        """Compact base + overlay into a new published generation.

        Same crash-safe order as the R03 protocol (segments -> manifest ->
        CURRENT -> generation row), then the new (empty-overlay) sidecar,
        then best-effort removal of the superseded sidecar. A crash before
        the CURRENT flip keeps serving the old generation; a crash after
        the flip heals via :meth:`recover` (the flipped files already merged
        the overlay, so the old sidecar's epoch applies to an empty overlay).
        Compaction merges under the SAME frozen IDF epoch — never a silent
        representation change — and performs zero text encodes (all vectors
        come from the merged matrix / cache).
        """
        self._require_epoch()
        generation_id = str(generation_id)
        previous = self.generation_id
        index = self.materialize(nprobe=nprobe)
        receipt = index.publish(self.index_dir, conn,
                                generation_id=generation_id, watermark=watermark)
        self.base = index
        self.generation_id = generation_id
        self._upserts = {}
        self._tombstones = set()
        self._write_sidecar(generation_id)
        self._note = ""
        self._invalidate()
        removed_sidecar = None
        if previous and previous != generation_id:
            stale = self.index_dir / _sidecar_name(previous)
            try:
                stale.unlink()
                removed_sidecar = stale.name
            except OSError:
                pass
        try:
            _fsync_dir(self.index_dir)
        except OSError:
            pass
        receipt.update({
            "compacted": True, "previous_generation": previous,
            "removed_sidecar": removed_sidecar,
            "cache": self.cache.stats(),
        })
        return receipt

    def recover(self, conn: sqlite3.Connection | None = None) -> dict:
        """Heal a crash at/after the CURRENT flip. Idempotent, no rebuild.

        - Ensures the CURRENT generation has a proper sidecar (rewrites one
          with the adopted epoch and an empty overlay when the flip
          outran the sidecar write).
        - With ``conn``: ensures the ``vector_index`` generation row matches
          CURRENT — reusing the existing row for the manifest hash when one
          exists (no row growth on repeat calls), else create + activate.
        """
        from ..store import generations as gen_store

        current = self.index_dir / "CURRENT"
        if not current.exists():
            return {"recovered": False, "reason": "no CURRENT pointer"}
        manifest_name = current.read_text(encoding="utf-8").strip()
        body = json.loads((self.index_dir / manifest_name).read_text(encoding="utf-8"))
        gen = str(body["generation_id"])
        sidecar = self.index_dir / _sidecar_name(gen)
        sidecar_healed = False
        if not sidecar.exists():
            if self._idf is None:
                self._adopt_newest_epoch()
            if self._idf is not None:
                self.generation_id = gen
                self._upserts = {}
                self._tombstones = set()
                self._write_sidecar(gen)
                sidecar_healed = True
        row_status = "no-conn"
        if conn is not None:
            rows = gen_store.list_generations(conn, GENERATION_KIND)
            match = next((r for r in rows
                          if r.get("manifest_hash") == body["manifest_hash"]), None)
            active = gen_store.get_active(conn, GENERATION_KIND)
            if match is not None:
                if active is None or active["generation_id"] != match["generation_id"]:
                    gen_store.activate_generation(conn, match["generation_id"])
                    row_status = "re-activated-existing"
                else:
                    row_status = "already-active"
            else:
                gid = gen_store.create_generation(
                    conn, kind=GENERATION_KIND,
                    config_hash=self.encoder.config_hash(),
                    watermark=str(body.get("watermark", "")),
                    manifest_hash=body["manifest_hash"])
                gen_store.activate_generation(conn, gid)
                row_status = "created-and-activated"
        fresh = ServedVectors.open(self.index_dir, encoder=self.encoder,
                                   cache=self.cache,
                                   delta_bound=self.delta_bound)
        self.base = fresh.base
        self.generation_id = fresh.generation_id
        self._idf = fresh._idf
        self._doc_count = fresh._doc_count
        self._upserts = fresh._upserts
        self._tombstones = fresh._tombstones
        self._note = fresh._note
        self._invalidate()
        return {"recovered": True, "generation_id": gen,
                "manifest": manifest_name,
                "manifest_hash": body["manifest_hash"],
                "sidecar_healed": sidecar_healed, "row": row_status}

    # -- retention: old/new generations + WAL disk check ---------------
    def stale_sidecars(self) -> list[str]:
        """Sidecars not belonging to CURRENT (crash leftovers, collectable)."""
        current = self.index_dir / "CURRENT"
        live = ""
        if current.exists():
            try:
                gen = ServedVectors.open(self.index_dir,
                                         encoder=self.encoder).generation_id
                live = _sidecar_name(gen)
            except (OSError, ValueError):
                live = ""
        return sorted(p.name for p in self.index_dir.glob("s4-delta-*.json")
                      if p.name != live)

    def prune_generations(self, keep: int = 1) -> dict:
        """Delete superseded generations, keeping CURRENT plus newest.

        ``keep=1`` retains exactly the CURRENT generation's files. Only
        files referenced by a pruned manifest (or its sidecar/tombstones)
        are removed; anything unparseable is left alone (fail-closed).
        """
        keep = max(1, int(keep))
        current = self.index_dir / "CURRENT"
        if not current.exists():
            return {"pruned": False, "reason": "no CURRENT pointer"}
        live_manifest = current.read_text(encoding="utf-8").strip()
        manifests: list[tuple[str, str, dict]] = []
        for path in self.index_dir.glob("manifest-*.json"):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
                manifests.append((path.name, str(body.get("created_at", "")),
                                  body))
            except (OSError, ValueError, KeyError):
                continue
        manifests.sort(key=lambda t: t[1])
        keep_names = {live_manifest}
        others = [m for m in manifests if m[0] != live_manifest]
        keep_names.update(m[0] for m in others[-(keep - 1):] if keep > 1)
        removed: list[str] = []
        reclaimed = 0
        for name, _, body in manifests:
            if name in keep_names:
                continue
            victims = [name]
            files = body.get("files", {})
            victims.extend(str(ref["path"]) for ref in files.values()
                           if isinstance(ref, dict) and "path" in ref)
            try:
                gen = str(body.get("generation_id", ""))
                if gen:
                    victims.append(_sidecar_name(gen))
            except (AttributeError, ValueError):
                pass
            for rel in victims:
                path = self.index_dir / rel
                try:
                    reclaimed += path.stat().st_size
                    path.unlink()
                    removed.append(rel)
                except OSError:
                    continue
        try:
            _fsync_dir(self.index_dir)
        except OSError:
            pass
        return {"pruned": True, "kept": sorted(keep_names),
                "removed": sorted(removed), "bytes_reclaimed": reclaimed}

    def disk_usage(self, db_path: str | Path | None = None) -> dict:
        """Bytes per live generation + totals + host headroom.

        Answers the S4 disk question directly: old and new generations plus
        the SQLite WAL must coexist; this reports every term and the host's
        free space in one record.
        """
        per_generation: dict[str, int] = {}
        for path in self.index_dir.glob("manifest-*.json"):
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            total = 0
            files = body.get("files", {})
            for ref in list(files.values()):
                try:
                    total += (self.index_dir / str(ref["path"])).stat().st_size
                except OSError:
                    continue
            try:
                total += (self.index_dir / path.name).stat().st_size
            except OSError:
                pass
            sidecar = self.index_dir / _sidecar_name(str(body.get("generation_id", "")))
            try:
                total += sidecar.stat().st_size
            except OSError:
                pass
            per_generation[str(body.get("generation_id", path.name))] = total
        index_bytes = sum(
            p.stat().st_size for p in self.index_dir.rglob("*") if p.is_file())
        db_files: dict[str, int] = {}
        if db_path is not None:
            for suffix in ("", "-wal", "-shm", "-journal"):
                p = Path(str(db_path) + suffix)
                try:
                    db_files[p.name] = p.stat().st_size
                except OSError:
                    continue
        try:
            free = shutil.disk_usage(str(self.index_dir)).free
        except OSError:
            free = -1
        footprint = index_bytes + sum(db_files.values())
        return {"per_generation": per_generation, "index_bytes": index_bytes,
                "db_files": db_files, "footprint_bytes": footprint,
                "host_free_bytes": free,
                "headroom_ratio": (free / footprint) if footprint > 0 and free >= 0 else None}


def ensure_served_persistent(router, served: ServedVectors,
                             generation_id: str) -> dict:
    """Pin the S4 served view on a router. Never builds, never encodes corpus.

    Drop-in replacement for the query-triggered full-corpus build: when a
    published base (or overlay) is servable the merged in-memory view is
    attached to the router's existing ANN branch (all INT-2 filtering
    semantics unchanged); otherwise the branch detaches with an honest note
    and the named lexical fallbacks serve. Corpus encode count is provably
    unchanged (only the per-query vector encodes, as with every branch).
    """
    if not served.servable:
        router.detach_vector(f"vector branch unavailable: {served.note}")
        return {"attached": False, "built": False, "reason": served.note}
    index = served.materialize()
    router.attach_vector(index, str(generation_id))
    # Additive honesty (frozen pin label untouched): the base generation the
    # vectors were actually built from, so pin-vs-base skew is observable.
    router._vector_base_generation = served.generation_id
    return {"attached": True, "built": False,
            "generation_id": str(generation_id), "atoms": index.count,
            "base_generation_id": served.generation_id,
            "delta_size": served.delta_size,
            "materialized_cached": bool(served._last_materialize_cached)}

