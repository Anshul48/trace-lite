"""Vector store adapter backed by LanceDB."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
import numpy as np


@dataclass
class SearchResult:
    node_id: str
    score: float
    metadata: dict


class VectorStore(Protocol):
    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        ...

    def upsert_batch(self, items: list[tuple[str, np.ndarray, dict]]) -> None:
        ...

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        ...

    def delete(self, node_id: str) -> None:
        ...


class LanceDBStore:
    """LanceDB implementation of VectorStore."""

    def __init__(self, db_dir: str | Path, table_name: str = "cortex_nodes"):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self._table = None
        self._init_table()

    def _init_table(self):
        import lancedb

        self.db = lancedb.connect(str(self.db_dir))
        if self.table_name in self.db.table_names():
            self._table = self.db.open_table(self.table_name)

    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        self.upsert_batch([(node_id, embedding, metadata)])

    def upsert_batch(self, items: list[tuple[str, np.ndarray, dict]]) -> None:
        if not items:
            return

        rows = []
        for node_id, embedding, meta in items:
            vec = embedding.tolist() if isinstance(embedding, np.ndarray) else list(embedding)
            rows.append(
                {
                    "node_id": node_id,
                    "vector": vec,
                    "metadata": json.dumps(meta, ensure_ascii=False),
                    "tree_id": meta.get("tree_id", ""),
                    "node_type": meta.get("node_type", "leaf"),
                }
            )

        import lancedb

        if self._table is None:
            self._table = self.db.create_table(self.table_name, data=rows, mode="overwrite")
        else:
            # Delete existing node_ids if present, then add new rows
            node_ids = [r["node_id"] for r in rows]
            id_list = ", ".join(f"'{nid}'" for nid in node_ids)
            try:
                self._table.delete(f"node_id IN ({id_list})")
            except Exception:
                pass
            self._table.add(rows)

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        if self._table is None:
            return []

        vec = query_embedding.tolist() if isinstance(query_embedding, np.ndarray) else list(query_embedding)
        q = self._table.search(vec).limit(top_k)
        if filter_expr:
            q = q.where(filter_expr)

        try:
            results = q.to_list()
        except Exception:
            results = q.to_pandas().to_dict(orient="records")

        search_results = []
        for row in results:
            raw_meta = row.get("metadata", {})
            meta = json.loads(raw_meta) if isinstance(raw_meta, str) else (raw_meta or {})
            distance = row.get("_distance", 0.0)
            similarity = 1.0 / (1.0 + distance)
            search_results.append(
                SearchResult(
                    node_id=row["node_id"],
                    score=similarity,
                    metadata=meta,
                )
            )

        return search_results

    def delete(self, node_id: str) -> None:
        if self._table is not None:
            try:
                self._table.delete(f"node_id = '{node_id}'")
            except Exception:
                pass


class MockVectorStore:
    """In-memory mock vector store for testing."""

    def __init__(self):
        self.nodes: dict[str, tuple[np.ndarray, dict]] = {}

    def upsert(self, node_id: str, embedding: np.ndarray, metadata: dict) -> None:
        self.nodes[node_id] = (np.array(embedding, dtype=np.float32), metadata)

    def upsert_batch(self, items: list[tuple[str, np.ndarray, dict]]) -> None:
        for node_id, embedding, meta in items:
            self.upsert(node_id, embedding, meta)

    def search(
        self, query_embedding: np.ndarray, top_k: int = 10, filter_expr: str | None = None
    ) -> list[SearchResult]:
        if not self.nodes:
            return []

        q_vec = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec) + 1e-9

        results = []
        for node_id, (vec, meta) in self.nodes.items():
            if filter_expr:
                if "tree_id" in filter_expr and meta.get("tree_id") not in filter_expr:
                    continue
                if "node_type" in filter_expr and meta.get("node_type") not in filter_expr:
                    continue

            v_norm = np.linalg.norm(vec) + 1e-9
            sim = float(np.dot(q_vec, vec) / (q_norm * v_norm))
            results.append(SearchResult(node_id=node_id, score=sim, metadata=meta))

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

    def delete(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
