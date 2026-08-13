"""Public vector-store interfaces and the LanceDB implementation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np


class VectorStoreError(RuntimeError):
    """Raised when vector data exists but cannot be extracted or queried."""


@dataclass
class SearchResult:
    node_id: str
    score: float
    metadata: dict


VectorRecord = tuple[str, np.ndarray, dict]


class VectorStore(Protocol):
    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        ...

    def upsert_batch(self, items: list[VectorRecord]) -> None:
        ...

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        ...

    def delete(self, node_id: str) -> None:
        ...

    def iter_vectors(self) -> Iterator[VectorRecord]:
        """Yield ``(node_id, vector, metadata)`` without backend internals."""
        ...


def _metadata(value: object) -> dict:
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _row_vector(row: dict) -> np.ndarray:
    value = row.get("vector")
    if value is None:
        raise VectorStoreError("Vector row is missing its vector column.")
    return np.asarray(value, dtype=np.float32)


class LanceDBStore:
    """LanceDB implementation with versioned collections.

    The active collection is selected by a tiny atomically replaced marker
    file.  Candidate collections are created beside it and remain invisible to
    readers until activation succeeds.
    """

    _ACTIVE_MARKER = "ACTIVE_COLLECTION.json"

    def __init__(
        self,
        db_dir: str | Path,
        table_name: str = "cortex_nodes",
        *,
        _follow_active: bool = True,
    ):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._base_table_name = table_name
        self._follow_active = _follow_active
        self.table_name = table_name
        self._table = None
        self._init_table()

    def _init_table(self) -> None:
        try:
            import lancedb

            self.db = lancedb.connect(str(self.db_dir))
        except Exception as exc:  # pragma: no cover - depends on optional backend
            raise VectorStoreError(f"Unable to open LanceDB: {exc}") from exc

        if self._follow_active:
            marker = self.db_dir / self._ACTIVE_MARKER
            if marker.exists():
                try:
                    value = json.loads(marker.read_text(encoding="utf-8"))
                    selected = value.get("collection")
                    if selected:
                        self.table_name = str(selected)
                except (OSError, json.JSONDecodeError):
                    # A stale/corrupt marker is reported by availability and
                    # does not make the source Spine data inaccessible.
                    self.table_name = self._base_table_name
        try:
            if self.table_name in self._list_table_names():
                self._table = self.db.open_table(self.table_name)
        except Exception as exc:  # pragma: no cover - backend/version specific
            raise VectorStoreError(f"Unable to open vector collection: {exc}") from exc

    def _list_table_names(self) -> list[str]:
        """Read collection names across supported LanceDB client versions."""
        list_tables = getattr(self.db, "list_tables", None)
        if callable(list_tables):
            response = list_tables()
            names = getattr(response, "tables", response)
            return [str(name) for name in names]
        return [str(name) for name in self.db.table_names()]

    @property
    def collection_name(self) -> str:
        return self.table_name

    @property
    def available(self) -> bool:
        return self._table is not None

    def diagnostic(self) -> dict:
        return {
            "available": self.available,
            "collection": self.table_name,
            "backend": "lancedb",
            "error": None,
        }

    def begin_build(
        self, build_id: str, *, dimension: int | None = None
    ) -> "LanceDBStore":
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", build_id)
        staged = LanceDBStore(
            self.db_dir,
            table_name=f"cortex_nodes_{safe_id}",
            _follow_active=False,
        )
        if dimension is not None:
            staged._create_empty_table(dimension)
        return staged

    def _create_empty_table(self, dimension: int) -> None:
        """Create a typed empty candidate so an empty rebuild can activate."""
        if dimension < 1:
            raise ValueError("Vector dimension must be positive.")
        try:
            import pyarrow as pa

            schema = pa.schema(
                [
                    pa.field("node_id", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), dimension)),
                    pa.field("metadata", pa.string()),
                    pa.field("tree_id", pa.string()),
                    pa.field("node_type", pa.string()),
                ]
            )
            self._table = self.db.create_table(self.table_name, schema=schema)
        except Exception as exc:
            raise VectorStoreError(
                f"Unable to create empty vector candidate: {exc}"
            ) from exc

    def activate_collection(self, collection_name: str) -> str:
        """Atomically switch readers to a validated collection."""
        old = self.table_name
        # Resolve the candidate before changing the marker.  A missing or
        # unreadable table must leave the old active collection untouched.
        new_table = self.db.open_table(collection_name)
        marker = self.db_dir / self._ACTIVE_MARKER
        temp = self.db_dir / f".{self._ACTIVE_MARKER}.{os.getpid()}.tmp"
        temp.write_text(
            json.dumps({"collection": collection_name}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temp, marker)
        self.table_name = collection_name
        self._table = new_table
        return old

    def restore_collection(self, collection_name: str | None) -> None:
        if collection_name:
            self.activate_collection(collection_name)

    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        self.upsert_batch([(node_id, embedding, metadata)])

    def upsert_batch(self, items: list[VectorRecord]) -> None:
        if not items:
            return
        rows = []
        for node_id, embedding, meta in items:
            vector = np.asarray(embedding, dtype=np.float32)
            if vector.ndim != 1 or vector.size == 0:
                raise ValueError(f"Invalid embedding for node {node_id}.")
            rows.append(
                {
                    "node_id": str(node_id),
                    "vector": vector.tolist(),
                    "metadata": json.dumps(meta or {}, ensure_ascii=False),
                    "tree_id": (meta or {}).get("tree_id", ""),
                    "node_type": (meta or {}).get("node_type", "leaf"),
                }
            )
        try:
            if self._table is None:
                self._table = self.db.create_table(
                    self.table_name, data=rows, mode="overwrite"
                )
            else:
                node_ids = [row["node_id"] for row in rows]
                escaped = ", ".join("'" + value.replace("'", "''") + "'" for value in node_ids)
                try:
                    self._table.delete(f"node_id IN ({escaped})")
                except Exception:
                    # A fresh candidate may not have a deletion index yet.
                    pass
                self._table.add(rows)
        except Exception as exc:
            raise VectorStoreError(f"Unable to write vector collection: {exc}") from exc

    def _read_rows(self) -> list[dict]:
        if self._table is None:
            return []
        try:
            if hasattr(self._table, "to_arrow"):
                return self._table.to_arrow().to_pylist()
            if hasattr(self._table, "to_list"):
                return self._table.to_list()
        except Exception as exc:
            raise VectorStoreError(f"Unable to read vector collection: {exc}") from exc
        raise VectorStoreError("LanceDB table exposes neither Arrow nor list row APIs.")

    def iter_vectors(self) -> Iterator[VectorRecord]:
        for row in self._read_rows():
            node_id = row.get("node_id")
            if not node_id:
                raise VectorStoreError("Vector row is missing node_id.")
            yield str(node_id), _row_vector(row), _metadata(row.get("metadata"))

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        if self._table is None:
            return []
        vector = np.asarray(query_embedding, dtype=np.float32).tolist()
        try:
            query = self._table.search(vector)
            if filter_expr:
                query = query.where(filter_expr)
            query = query.limit(top_k)
            if hasattr(query, "to_list"):
                rows = query.to_list()
            elif hasattr(query, "to_arrow"):
                rows = query.to_arrow().to_pylist()
            else:
                raise VectorStoreError("LanceDB search exposes no list/Arrow result API.")
        except VectorStoreError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Unable to search vector collection: {exc}") from exc

        results: list[SearchResult] = []
        for row in rows:
            distance = float(row.get("_distance", 0.0) or 0.0)
            results.append(
                SearchResult(
                    node_id=str(row["node_id"]),
                    score=1.0 / (1.0 + distance),
                    metadata=_metadata(row.get("metadata")),
                )
            )
        return results

    def delete(self, node_id: str) -> None:
        if self._table is None:
            return
        escaped = str(node_id).replace("'", "''")
        try:
            self._table.delete(f"node_id = '{escaped}'")
        except Exception as exc:
            raise VectorStoreError(f"Unable to delete vector {node_id}: {exc}") from exc

    def count(self) -> int:
        return sum(1 for _ in self.iter_vectors())

    def dimension(self) -> int | None:
        for _, vector, _ in self.iter_vectors():
            return int(vector.size)
        return None

    def list_collections(self) -> list[str]:
        try:
            return self._list_table_names()
        except Exception as exc:
            raise VectorStoreError(f"Unable to list vector collections: {exc}") from exc

    def garbage_collect(self, keep_collections: set[str] | None = None) -> list[str]:
        keep = set(keep_collections or set())
        keep.add(self.table_name)
        removed: list[str] = []
        for name in self.list_collections():
            if name in keep:
                continue
            if not name.startswith("cortex_nodes"):
                continue
            try:
                self.db.drop_table(name)
                removed.append(name)
            except Exception as exc:
                raise VectorStoreError(f"Unable to garbage-collect {name}: {exc}") from exc
        return removed

    def reset_derived(self) -> None:
        for name in self.list_collections():
            if name.startswith("cortex_nodes"):
                try:
                    self.db.drop_table(name)
                except Exception as exc:
                    raise VectorStoreError(f"Unable to reset vector collection {name}: {exc}") from exc
        marker = self.db_dir / self._ACTIVE_MARKER
        if marker.exists():
            marker.unlink()
        self.table_name = self._base_table_name
        self._table = None


class MockVectorStore:
    """In-memory vector store with the same public diagnostics as LanceDB."""

    def __init__(self, dim: int | None = None):
        self.nodes: dict[str, tuple[np.ndarray, dict]] = {}
        self._dim = dim
        self.collection_name = "mock-active"

    @property
    def available(self) -> bool:
        return True

    def diagnostic(self) -> dict:
        return {"available": True, "collection": self.collection_name, "backend": "mock", "error": None}

    def begin_build(self, build_id: str) -> "MockVectorStore":
        return MockVectorStore(dim=self._dim)

    def activate_collection(self, staged: "MockVectorStore") -> str:
        old = self.collection_name
        self.nodes = {
            node_id: (vector.copy(), dict(metadata))
            for node_id, (vector, metadata) in staged.nodes.items()
        }
        self._dim = staged._dim
        self.collection_name = f"mock-{id(staged)}"
        return old

    def restore_snapshot(self, snapshot: list[VectorRecord], collection_name: str | None = None) -> None:
        self.nodes = {
            node_id: (np.asarray(vector, dtype=np.float32).copy(), dict(metadata))
            for node_id, vector, metadata in snapshot
        }
        if collection_name:
            self.collection_name = collection_name

    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        vector = np.asarray(embedding, dtype=np.float32)
        self._dim = self._dim or int(vector.size)
        self.nodes[node_id] = (vector.copy(), dict(metadata or {}))

    def upsert_batch(self, items: list[VectorRecord]) -> None:
        for node_id, embedding, meta in items:
            self.upsert(node_id, embedding, meta)

    def replace_all(self, items: list[VectorRecord]) -> None:
        self.nodes = {}
        self.upsert_batch(items)

    def iter_vectors(self) -> Iterator[VectorRecord]:
        for node_id in sorted(self.nodes):
            vector, metadata = self.nodes[node_id]
            yield node_id, vector.copy(), dict(metadata)

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        if not self.nodes:
            return []
        query = np.asarray(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query) + 1e-9
        results = []
        for node_id, (vector, metadata) in self.nodes.items():
            if filter_expr and not self._matches_filter(metadata, filter_expr):
                continue
            score = float(np.dot(query, vector) / (query_norm * (np.linalg.norm(vector) + 1e-9)))
            results.append(SearchResult(node_id=node_id, score=score, metadata=dict(metadata)))
        results.sort(key=lambda result: (-result.score, result.node_id))
        return results[:top_k]

    @staticmethod
    def _matches_filter(metadata: dict, expression: str) -> bool:
        # Test-only compatibility for the simple expressions used by the
        # routing engine; real filter parsing belongs to LanceDB.
        for key in ("tree_id", "node_type"):
            if key in expression:
                values = re.findall(r"'([^']*)'", expression)
                if values and metadata.get(key) not in values:
                    return False
        return True

    def delete(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)

    def count(self) -> int:
        return len(self.nodes)

    def dimension(self) -> int | None:
        return self._dim or (len(next(iter(self.nodes.values()))[0]) if self.nodes else None)

    def reset_derived(self) -> None:
        self.nodes.clear()
        self._dim = None
