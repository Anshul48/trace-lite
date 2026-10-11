"""R03 embedded ANN: numpy IVF index + crash-recoverable publication.

Fallback substrate (no network — see ``encoder.py``): IVF over the pinned
``hashbow-128-idf`` space. Same vectors, same filters, same budgets as the
exact path; recall is measured against brute force, never against qrels.

Publication protocol (ARCH §8: immutable segments + manifests):
  1. write segment files, flush + fsync
  2. write ``manifest-<gen>.json`` via tmp + os.replace, fsync dir
  3. publish ``CURRENT`` pointer via tmp + os.replace, fsync dir
  4. record + activate the ``projection_generation`` row (kind
     ``vector_index``) — readers pin the generation CURRENT names
Deletes never rewrite segments: they append to an immutable-per-write
``tombstones-<gen>.json`` delta loaded alongside the manifest. Files no
manifest references are orphans: reported on open, collectable, never fatal.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover - venv always provides numpy
    np = None  # type: ignore[assignment]

from .encoder import HashBowEncoder

FORMAT_VERSION = 1
GENERATION_KIND = "vector_index"
TRAIN_SEED = 42
MAX_LISTS = 256


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as fh:
        os.fsync(fh.fileno())


def _fsync_dir(path: Path) -> None:
    """Fsync a directory entry (durability of renames). Best-effort: Windows
    cannot fsync a directory handle, so OSError is tolerated there — the
    tmp+os.replace atomicity (which Windows supports) still holds."""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _fsync_file(tmp)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _train_kmeans(mat: np.ndarray, n_lists: int, seed: int = TRAIN_SEED,
                  iters: int = 25) -> np.ndarray:
    """Bounded Lloyd k-means with kmeans++ init. Deterministic for fixed seed."""
    rng = np.random.default_rng(seed)
    n, dim = mat.shape
    first = int(rng.integers(0, n))
    centroids = [mat[first]]
    nearest = np.full(n, np.inf, dtype=np.float64)
    for _ in range(1, n_lists):
        d2 = 1.0 - mat @ centroids[-1]
        d2 = np.clip(d2, 0.0, None)
        nearest = np.minimum(nearest, d2)
        total = float(nearest.sum())
        if total <= 0:
            centroids.append(mat[int(rng.integers(0, n))])
            continue
        pick = float(rng.random()) * total
        centroids.append(mat[int(np.searchsorted(np.cumsum(nearest), pick))])
    centroids = np.stack(centroids).astype(np.float32)
    assign = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        sims = mat @ centroids.T
        new_assign = sims.argmax(axis=1).astype(np.int32)
        if bool((new_assign == assign).all()) and _ > 0:
            assign = new_assign
            break
        assign = new_assign
        for c in range(n_lists):
            members = mat[assign == c]
            if len(members):
                centroids[c] = members.mean(axis=0)
        norms = np.linalg.norm(centroids, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        centroids = (centroids / norms).astype(np.float32)
    return centroids


class VectorIndex:
    """In-memory IVF over one encoder space + file publication helpers."""

    def __init__(self, encoder: HashBowEncoder, nprobe: int = 8) -> None:
        if np is None:  # pragma: no cover
            raise RuntimeError("numpy is required for VectorIndex")
        self.encoder = encoder
        self.nprobe = nprobe
        self.generation_id = ""
        self.watermark = ""
        self.ids: list[int] = []
        self._mat: np.ndarray | None = None
        self._centroids: np.ndarray | None = None
        self._assign: np.ndarray | None = None
        self._postings: list[list[int]] = []
        self.tombstones: set[int] = set()

    # -- build ------------------------------------------------------
    @property
    def count(self) -> int:
        return len(self.ids)

    @property
    def n_lists(self) -> int:
        return len(self._postings)

    def build(self, ids: list[int], mat: np.ndarray, *,
              generation_id: str = "", watermark: str = "",
              n_lists: int | None = None) -> VectorIndex:
        if len(ids) != len(mat):
            raise ValueError("ids/matrix length mismatch")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate ids")
        self.ids = list(ids)
        self._mat = np.ascontiguousarray(mat, dtype=np.float32)
        norms = np.linalg.norm(self._mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._mat = (self._mat / norms).astype(np.float32)
        n = len(ids)
        if n_lists is None:
            n_lists = max(1, min(MAX_LISTS, int(round(n**0.5)))) if n else 1
        n_lists = max(1, min(n_lists, n if n else 1))
        if n <= n_lists:
            self._centroids = self._mat.copy()
            self._assign = np.arange(n, dtype=np.int32)
        else:
            self._centroids = _train_kmeans(self._mat, n_lists)
            self._assign = (self._mat @ self._centroids.T).argmax(axis=1).astype(np.int32)
        self._postings = [[] for _ in range(len(self._centroids))]
        for row, c in enumerate(self._assign.tolist()):
            self._postings[int(c)].append(row)
        self.tombstones = set()
        self.generation_id = generation_id
        self.watermark = watermark
        return self

    # -- mutation (tombstone deletes; segments stay immutable) -------
    def delete(self, atom_id: int) -> bool:
        if atom_id not in self.ids or atom_id in self.tombstones:
            return atom_id in self.tombstones
        self.tombstones.add(atom_id)
        return True

    def live_ids(self) -> set[int]:
        return set(self.ids) - self.tombstones

    # -- search ------------------------------------------------------
    def _row_scores(self, q: np.ndarray, rows: list[int]) -> list[tuple[int, float]]:
        sims = self._mat[rows] @ q
        return [(self.ids[r], float(s)) for r, s in zip(rows, sims.tolist())]

    def search_exact(self, query_vec: list[float] | np.ndarray, k: int = 10,
                     allowed: set[int] | None = None) -> tuple[list[tuple[int, float]], dict]:
        """Brute-force oracle in the same space (recall reference, not a product path)."""
        if self._mat is None or not self.ids:
            return [], {"scanned": 0, "filtered_out": 0, "adequate": True}
        q = np.asarray(query_vec, dtype=np.float32)
        nrm = float(np.linalg.norm(q)) or 1.0
        q = q / nrm
        # Oracle: untruncated under filters (k*5+50-style overretrieve is a
        # product-search bound and must not truncate the recall reference).
        depth = k if allowed is None else len(self.ids)
        sims = self._mat @ q
        cut = np.argpartition(-sims, kth=min(depth, len(sims)) - 1)[:depth]
        order = cut[np.argsort(-sims[cut], kind="stable")]
        pairs = [(int(r), float(sims[int(r)])) for r in order.tolist()]
        return self._finalize(pairs, k, allowed, scanned=len(self.ids))

    def search(self, query_vec: list[float] | np.ndarray, k: int = 10,
               nprobe: int | None = None,
               allowed: set[int] | None = None) -> tuple[list[tuple[int, float]], dict]:
        """IVF search. ``allowed`` = current-revision filter (overretrieve + flag)."""
        if self._mat is None or not self.ids or self._centroids is None:
            return [], {"scanned": 0, "filtered_out": 0, "adequate": True}
        q = np.asarray(query_vec, dtype=np.float32)
        nrm = float(np.linalg.norm(q)) or 1.0
        q = q / nrm
        if not bool(np.any(q)):
            return [], {"scanned": 0, "filtered_out": 0, "adequate": True}
        probe = nprobe if nprobe is not None else self.nprobe
        probe = max(1, min(probe, len(self._centroids)))
        csims = self._centroids @ q
        top = np.argpartition(-csims, kth=probe - 1)[:probe]
        rows: list[int] = []
        for c in top.tolist():
            rows.extend(self._postings[int(c)])
        if not rows:
            return [], {"scanned": 0, "filtered_out": 0, "adequate": True}
        sims = self._mat[rows] @ q
        depth = k if allowed is None else len(rows)
        cut = np.argpartition(-sims, kth=min(depth, len(sims)) - 1)[:depth]
        order = cut[np.argsort(-sims[cut], kind="stable")]
        pairs = [(int(rows[i]), float(sims[int(i)])) for i in order.tolist()]
        return self._finalize(pairs, k, allowed, scanned=len(rows))

    def _finalize(self, pairs: list[tuple[int, float]], k: int,
                  allowed: set[int] | None, scanned: int) -> tuple[list[tuple[int, float]], dict]:
        # Deterministic (-score, atom-id) order: identical tie-breaks in the
        # exact and IVF paths, so recall measures retrieval misses, not sort
        # input order.
        pairs = sorted(pairs, key=lambda t: (-t[1], self.ids[t[0]]))
        hits: list[tuple[int, float]] = []
        filtered = 0
        for row, s in pairs:
            aid = self.ids[row]
            if aid in self.tombstones:
                filtered += 1
                continue
            if allowed is not None and aid not in allowed:
                filtered += 1
                continue
            if float(s) <= 0:
                continue
            hits.append((aid, float(s)))
            if len(hits) >= k:
                break
        adequate = True
        if allowed is not None and len(hits) < k:
            adequate = False  # filtered-out top-k is NOT "no evidence"
        return hits, {"scanned": scanned, "filtered_out": filtered, "adequate": adequate}

    def recall_vs_exact(self, query_vecs: list, k: int = 10,
                        allowed: set[int] | None = None) -> dict:
        """ANN recall@k vs exact in the same space/filters (EVAL §5 ANN-vs-task rule)."""
        if not query_vecs:
            return {"queries": 0, "recall_at_k": 1.0, "k": k}
        hits = 0
        total = 0
        for qv in query_vecs:
            ref = {aid for aid, _ in self.search_exact(qv, k, allowed)[0]}
            got = {aid for aid, _ in self.search(qv, k, allowed=allowed)[0]}
            if not ref:
                continue
            total += 1
            hits += len(ref & got) / len(ref)
        return {"queries": total, "recall_at_k": round(hits / total, 4) if total else 1.0, "k": k}

    # -- publication --------------------------------------------------
    def _segment_paths(self, index_dir: Path) -> dict[str, Path]:
        seg = index_dir / "segments"
        gen = self.generation_id or "unpublished"
        return {
            "vec": seg / f"seg-{gen}.vec.npy",
            "ids": seg / f"seg-{gen}.ids.npy",
            "centroids": seg / f"seg-{gen}.centroids.npy",
            "assign": seg / f"seg-{gen}.assign.npy",
        }

    def save(self, index_dir: str | Path, generation_id: str = "",
             watermark: str = "") -> dict:
        """Write immutable segment files (flushed + fsynced). No pointer touched."""
        if self._mat is None:
            raise ValueError("nothing built to save")
        if generation_id:
            self.generation_id = generation_id
        if watermark:
            self.watermark = watermark
        if not self.generation_id:
            raise ValueError("generation_id required before save")
        index_dir = Path(index_dir)
        segdir = index_dir / "segments"
        segdir.mkdir(parents=True, exist_ok=True)
        paths = self._segment_paths(index_dir)
        assert np is not None
        np.save(paths["vec"], self._mat)
        np.save(paths["ids"], np.asarray(self.ids, dtype=np.int64))
        np.save(paths["centroids"], self._centroids)
        np.save(paths["assign"], self._assign)
        for p in paths.values():
            _fsync_file(p)
        _fsync_dir(segdir)
        tomb_path = index_dir / f"tombstones-{self.generation_id}.json"
        _atomic_write_json(tomb_path, {"generation_id": self.generation_id,
                                       "tombstones": sorted(self.tombstones)})
        files = {k: {"path": str(p.relative_to(index_dir)), "sha256": _sha_file(p)}
                 for k, p in paths.items()}
        files["tombstones"] = {"path": str(tomb_path.relative_to(index_dir)),
                               "sha256": _sha_file(tomb_path)}
        return files

    def manifest(self, files: dict) -> dict:
        body = {
            "format_version": FORMAT_VERSION,
            "generation_id": self.generation_id,
            "kind": GENERATION_KIND,
            "watermark": self.watermark,
            "encoder": self.encoder.spec.to_dict(),
            "dim": self.encoder.dim,
            "count": self.count,
            "live_count": len(self.live_ids()),
            "n_lists": self.n_lists,
            "nprobe_default": self.nprobe,
            "train_seed": TRAIN_SEED,
            "created_at": _utcnow(),
            "files": files,
        }
        body["manifest_hash"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        return body

    def publish(self, index_dir: str | Path, conn: sqlite3.Connection | None = None,
                generation_id: str = "", watermark: str = "") -> dict:
        """save → manifest → CURRENT → generation row. Crash-safe order."""
        from ..store import generations as gen_store

        index_dir = Path(index_dir)
        files = self.save(index_dir, generation_id, watermark)
        body = self.manifest(files)
        manifest_name = f"manifest-{self.generation_id}.json"
        _atomic_write_json(index_dir / manifest_name, body)
        current = index_dir / "CURRENT"
        tmp = current.with_suffix(".tmp")
        tmp.write_text(manifest_name, encoding="utf-8")
        _fsync_file(tmp)
        os.replace(tmp, current)
        _fsync_dir(index_dir)
        if conn is not None:
            gid = gen_store.create_generation(
                conn, kind=GENERATION_KIND,
                config_hash=self.encoder.config_hash(),
                watermark=self.watermark,
                manifest_hash=body["manifest_hash"])
            gen_store.activate_generation(conn, gid)
        return {"manifest": manifest_name, "manifest_hash": body["manifest_hash"],
                "generation_id": self.generation_id, "count": self.count}

    @classmethod
    def open(cls, index_dir: str | Path,
             expected_config_hash: str | None = None) -> tuple[VectorIndex, dict]:
        """Restart recovery: CURRENT → manifest → segments, hash-verified."""
        assert np is not None
        index_dir = Path(index_dir)
        current = index_dir / "CURRENT"
        if not current.exists():
            raise FileNotFoundError(f"no CURRENT pointer in {index_dir}")
        manifest_name = current.read_text(encoding="utf-8").strip()
        manifest_path = index_dir / manifest_name
        body = json.loads(manifest_path.read_text(encoding="utf-8"))
        expect = dict(body)
        digest = expect.pop("manifest_hash", "")
        actual = hashlib.sha256(json.dumps(expect, sort_keys=True).encode("utf-8")).hexdigest()
        if digest != actual:
            raise ValueError(f"manifest hash mismatch in {manifest_name}")
        if expected_config_hash and body["encoder"]["config_hash"] != expected_config_hash:
            raise ValueError("encoder config hash mismatch: index generation is stale")
        for key, ref in body["files"].items():
            if key == "tombstones":
                continue
            p = index_dir / ref["path"]
            if _sha_file(p) != ref["sha256"]:
                raise ValueError(f"segment hash mismatch: {ref['path']}")
        files = body["files"]
        mat = np.load(index_dir / files["vec"]["path"])
        ids = np.load(index_dir / files["ids"]["path"]).tolist()
        centroids = np.load(index_dir / files["centroids"]["path"])
        assign = np.load(index_dir / files["assign"]["path"])
        tomb = json.loads((index_dir / files["tombstones"]["path"]).read_text(encoding="utf-8"))
        from .encoder import EncoderSpec as _Spec
        spec = _Spec()
        for k, v in body["encoder"].items():
            if k in ("config_hash", "label"):
                continue
            setattr(spec, k, v)
        idx = cls(HashBowEncoder(spec), nprobe=body.get("nprobe_default", 8))
        idx.generation_id = body["generation_id"]
        idx.watermark = body.get("watermark", "")
        idx.ids = [int(i) for i in ids]
        idx._mat = np.ascontiguousarray(mat, dtype=np.float32)
        idx._centroids = np.ascontiguousarray(centroids, dtype=np.float32)
        idx._assign = np.ascontiguousarray(assign, dtype=np.int32)
        idx._postings = [[] for _ in range(len(idx._centroids))]
        for row, c in enumerate(idx._assign.tolist()):
            idx._postings[int(c)].append(row)
        idx.tombstones = set(tomb.get("tombstones", []))
        referenced = {manifest_name, "CURRENT",
                      *(r["path"] for r in files.values())}
        orphans = sorted(str(p.relative_to(index_dir))
                         for p in list((index_dir / "segments").glob("*"))
                         + list(index_dir.glob("manifest-*.json"))
                         + list(index_dir.glob("tombstones-*.json"))
                         + list(index_dir.glob("*.tmp"))
                         if str(p.relative_to(index_dir)) not in referenced)
        info = {"manifest": manifest_name, "manifest_hash": body["manifest_hash"],
                "generation_id": body["generation_id"], "count": body["count"],
                "live_count": len(idx.live_ids()), "orphans": orphans,
                "encoder_label": body["encoder"]["label"]}
        return idx, info

    @staticmethod
    def collect_orphans(index_dir: str | Path) -> list[str]:
        """Remove files no live manifest references (*.tmp always eligible)."""
        index_dir = Path(index_dir)
        current = index_dir / "CURRENT"
        referenced: set[str] = set()
        if current.exists():
            manifest_name = current.read_text(encoding="utf-8").strip()
            try:
                body = json.loads((index_dir / manifest_name).read_text(encoding="utf-8"))
                referenced = {manifest_name,
                              *(r["path"] for r in body["files"].values())}
            except (OSError, ValueError, KeyError):
                referenced = set()
        removed = []
        for p in (list(index_dir.glob("*.tmp")) + list(index_dir.glob("manifest-*.json"))
                  + list(index_dir.glob("tombstones-*.json"))
                  + list((index_dir / "segments").glob("*")) if (index_dir / "segments").exists() else []):
            rel = str(p.relative_to(index_dir))
            if rel not in referenced and (p.suffix == ".tmp" or rel not in ("CURRENT",)):
                if rel == "CURRENT":
                    continue
                p.unlink()
                removed.append(rel)
        _fsync_dir(index_dir)
        return sorted(removed)
