"""Forest Index: Tree and TreeNode representations backed by SQLite."""

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class TreeNode:
    node_id: str
    tree_id: str
    level: int  # 0 = leaf, 1+ = summary
    node_type: str  # "leaf" | "cluster_summary" | "root_summary"
    atom_ids: list[str]  # Atoms covered by this node
    summary_text: str | None  # Summary text (None for leaves)
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_accessed: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    access_count: int = 1

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Tree:
    tree_id: str
    name: str
    description: str
    root_node_id: str | None = None
    node_count: int = 0
    leaf_count: int = 0
    depth: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_consolidated: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ForestIndex:
    """
    SQLite-backed store for Forest trees and tree nodes.
    Lives in cortex.sqlite3 (rebuildable from Spine).
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trees (
                    tree_id             TEXT PRIMARY KEY,
                    name                TEXT NOT NULL,
                    description         TEXT NOT NULL,
                    root_node_id        TEXT,
                    node_count          INTEGER NOT NULL DEFAULT 0,
                    leaf_count          INTEGER NOT NULL DEFAULT 0,
                    depth               INTEGER NOT NULL DEFAULT 0,
                    created_at          TEXT NOT NULL,
                    last_consolidated   TEXT
                );

                CREATE TABLE IF NOT EXISTS tree_nodes (
                    node_id         TEXT PRIMARY KEY,
                    tree_id         TEXT NOT NULL,
                    level           INTEGER NOT NULL,
                    node_type       TEXT NOT NULL,
                    atom_ids        TEXT NOT NULL, -- JSON list
                    summary_text    TEXT,
                    parent_id       TEXT,
                    children_ids    TEXT NOT NULL, -- JSON list
                    created_at      TEXT NOT NULL,
                    last_accessed   TEXT NOT NULL,
                    access_count    INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY(tree_id) REFERENCES trees(tree_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_tree ON tree_nodes(tree_id);
                CREATE INDEX IF NOT EXISTS idx_nodes_parent ON tree_nodes(parent_id);
                CREATE INDEX IF NOT EXISTS idx_nodes_level ON tree_nodes(tree_id, level);
            """)

    def store_tree(self, tree: Tree) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trees
                (tree_id, name, description, root_node_id, node_count, leaf_count, depth, created_at, last_consolidated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tree.tree_id,
                    tree.name,
                    tree.description,
                    tree.root_node_id,
                    tree.node_count,
                    tree.leaf_count,
                    tree.depth,
                    tree.created_at,
                    tree.last_consolidated,
                ),
            )

    def get_tree(self, tree_id: str) -> Tree | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM trees WHERE tree_id = ?", (tree_id,)).fetchone()
            if not row:
                return None
            return Tree(
                tree_id=row["tree_id"],
                name=row["name"],
                description=row["description"],
                root_node_id=row["root_node_id"],
                node_count=row["node_count"],
                leaf_count=row["leaf_count"],
                depth=row["depth"],
                created_at=row["created_at"],
                last_consolidated=row["last_consolidated"],
            )

    def list_trees(self) -> list[Tree]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM trees ORDER BY created_at DESC").fetchall()
            return [
                Tree(
                    tree_id=r["tree_id"],
                    name=r["name"],
                    description=r["description"],
                    root_node_id=r["root_node_id"],
                    node_count=r["node_count"],
                    leaf_count=r["leaf_count"],
                    depth=r["depth"],
                    created_at=r["created_at"],
                    last_consolidated=r["last_consolidated"],
                )
                for r in rows
            ]

    def store_node(self, node: TreeNode) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tree_nodes
                (node_id, tree_id, level, node_type, atom_ids, summary_text, parent_id,
                 children_ids, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.tree_id,
                    node.level,
                    node.node_type,
                    json.dumps(node.atom_ids),
                    node.summary_text,
                    node.parent_id,
                    json.dumps(node.children_ids),
                    node.created_at,
                    node.last_accessed,
                    node.access_count,
                ),
            )

    def store_nodes_batch(self, nodes: list[TreeNode]) -> None:
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO tree_nodes
                (node_id, tree_id, level, node_type, atom_ids, summary_text, parent_id,
                 children_ids, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        n.node_id,
                        n.tree_id,
                        n.level,
                        n.node_type,
                        json.dumps(n.atom_ids),
                        n.summary_text,
                        n.parent_id,
                        json.dumps(n.children_ids),
                        n.created_at,
                        n.last_accessed,
                        n.access_count,
                    )
                    for n in nodes
                ],
            )

    def get_node(self, node_id: str) -> TreeNode | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tree_nodes WHERE node_id = ?", (node_id,)).fetchone()
            if not row:
                return None
            return TreeNode(
                node_id=row["node_id"],
                tree_id=row["tree_id"],
                level=row["level"],
                node_type=row["node_type"],
                atom_ids=json.loads(row["atom_ids"]),
                summary_text=row["summary_text"],
                parent_id=row["parent_id"],
                children_ids=json.loads(row["children_ids"]),
                created_at=row["created_at"],
                last_accessed=row["last_accessed"],
                access_count=row["access_count"],
            )

    def get_children(self, node_id: str) -> list[TreeNode]:
        node = self.get_node(node_id)
        if not node or not node.children_ids:
            return []
        return [self.get_node(cid) for cid in node.children_ids if self.get_node(cid) is not None]

    def get_tree_nodes(self, tree_id: str, level: int | None = None) -> list[TreeNode]:
        with self._get_connection() as conn:
            if level is not None:
                rows = conn.execute(
                    "SELECT * FROM tree_nodes WHERE tree_id = ? AND level = ?", (tree_id, level)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tree_nodes WHERE tree_id = ?", (tree_id,)).fetchall()

            return [
                TreeNode(
                    node_id=r["node_id"],
                    tree_id=r["tree_id"],
                    level=r["level"],
                    node_type=r["node_type"],
                    atom_ids=json.loads(r["atom_ids"]),
                    summary_text=r["summary_text"],
                    parent_id=r["parent_id"],
                    children_ids=json.loads(r["children_ids"]),
                    created_at=r["created_at"],
                    last_accessed=r["last_accessed"],
                    access_count=r["access_count"],
                )
                for r in rows
            ]

    def record_access(self, node_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE tree_nodes
                SET last_accessed = ?, access_count = access_count + 1
                WHERE node_id = ?
                """,
                (now, node_id),
            )

    def delete_tree(self, tree_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM tree_nodes WHERE tree_id = ?", (tree_id,))
            conn.execute("DELETE FROM trees WHERE tree_id = ?", (tree_id,))
