"""Forest Index: Tree and TreeNode representations backed by SQLite."""

import json
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


class _ManagedConnection:
    """Close SQLite connections after the existing transaction context exits."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self._connection.__enter__()
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return self._connection.__exit__(exc_type, exc_value, traceback)
        finally:
            self._connection.close()

    def __getattr__(self, name):
        return getattr(self._connection, name)


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
    # New derived nodes must be llm/retry. fallback and passthrough remain
    # readable only as legacy provenance so validation can mark those indexes
    # untrusted; source is reserved for leaves.
    summary_provenance: str = "source"

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
        return _ManagedConnection(conn)

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
                    summary_provenance TEXT NOT NULL DEFAULT 'source',
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

                CREATE TABLE IF NOT EXISTS pending_assignments (
                    tree_id     TEXT NOT NULL,
                    atom_id     TEXT NOT NULL,
                    queued_at   TEXT NOT NULL,
                    PRIMARY KEY (tree_id, atom_id)
                );

                CREATE INDEX IF NOT EXISTS idx_pending_assignments_tree
                    ON pending_assignments(tree_id);
                CREATE INDEX IF NOT EXISTS idx_pending_assignments_atom
                    ON pending_assignments(atom_id);

                -- Source-level queue: captures can be durable before any LLM
                -- routing/title generation is attempted.
                CREATE TABLE IF NOT EXISTS pending_source_atoms (
                    atom_id     TEXT PRIMARY KEY,
                    queued_at   TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pending_source_atoms_queued
                    ON pending_source_atoms(queued_at);

                CREATE TABLE IF NOT EXISTS index_builds (
                    build_id                  TEXT PRIMARY KEY,
                    state                     TEXT NOT NULL,
                    created_at                TEXT NOT NULL,
                    updated_at                TEXT NOT NULL,
                    source_atom_count         INTEGER NOT NULL DEFAULT 0,
                    tree_count                INTEGER NOT NULL DEFAULT 0,
                    node_count                INTEGER NOT NULL DEFAULT 0,
                    leaf_count                INTEGER NOT NULL DEFAULT 0,
                    vector_count              INTEGER NOT NULL DEFAULT 0,
                    embedding_model           TEXT NOT NULL DEFAULT '',
                    embedding_dimension       INTEGER NOT NULL DEFAULT 0,
                    config_fingerprint        TEXT NOT NULL DEFAULT '',
                    vector_collection         TEXT,
                    valid_summary_count       INTEGER NOT NULL DEFAULT 0,
                    fallback_summary_count    INTEGER NOT NULL DEFAULT 0,
                    retry_summary_count       INTEGER NOT NULL DEFAULT 0,
                    passthrough_summary_count INTEGER NOT NULL DEFAULT 0,
                    summary_quality           TEXT NOT NULL DEFAULT '{}',
                    warnings                  TEXT NOT NULL DEFAULT '[]',
                    error                     TEXT
                );

                CREATE TABLE IF NOT EXISTS index_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS graph_edges (
                    edge_id             TEXT PRIMARY KEY,
                    source_atom_id      TEXT NOT NULL,
                    target_atom_id      TEXT NOT NULL,
                    relation_type       TEXT NOT NULL,
                    weight              REAL NOT NULL DEFAULT 1.0,
                    confidence          REAL NOT NULL DEFAULT 1.0,
                    created_at          TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(source_atom_id);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON graph_edges(target_atom_id);
                CREATE INDEX IF NOT EXISTS idx_graph_edges_rel ON graph_edges(relation_type);
            """)
            # Existing Cortex databases predate summary provenance.  SQLite
            # migrations are intentionally additive; source data is untouched.
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(tree_nodes)").fetchall()
            }
            if "summary_provenance" not in columns:
                conn.execute(
                    "ALTER TABLE tree_nodes ADD COLUMN summary_provenance TEXT NOT NULL DEFAULT 'source'"
                )

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
                (node_id, tree_id, level, node_type, atom_ids, summary_text,
                 summary_provenance, parent_id, children_ids, created_at,
                 last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node.node_id,
                    node.tree_id,
                    node.level,
                    node.node_type,
                    json.dumps(node.atom_ids),
                    node.summary_text,
                    node.summary_provenance,
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
                (node_id, tree_id, level, node_type, atom_ids, summary_text,
                 summary_provenance, parent_id, children_ids, created_at,
                 last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        n.node_id,
                        n.tree_id,
                        n.level,
                        n.node_type,
                        json.dumps(n.atom_ids),
                        n.summary_text,
                        n.summary_provenance,
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
                summary_provenance=row["summary_provenance"] if "summary_provenance" in row.keys() else "source",
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
                    summary_provenance=r["summary_provenance"] if "summary_provenance" in r.keys() else "source",
                    parent_id=r["parent_id"],
                    children_ids=json.loads(r["children_ids"]),
                    created_at=r["created_at"],
                    last_accessed=r["last_accessed"],
                    access_count=r["access_count"],
                )
                for r in rows
            ]

    def get_indexed_atom_ids(self, tree_id: str | None = None) -> set[str]:
        """Return atom IDs represented by leaf nodes in the Cortex index."""
        with self._get_connection() as conn:
            if tree_id is None:
                rows = conn.execute(
                    "SELECT atom_ids FROM tree_nodes WHERE level = 0"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT atom_ids FROM tree_nodes WHERE tree_id = ? AND level = 0",
                    (tree_id,),
                ).fetchall()

            return {
                atom_id
                for row in rows
                for atom_id in json.loads(row["atom_ids"])
            }

    def get_leaf_nodes_for_atom(self, atom_id: str) -> list[TreeNode]:
        """Return derived leaves containing a source atom ID."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tree_nodes WHERE level = 0"
            ).fetchall()
        result: list[TreeNode] = []
        for row in rows:
            if atom_id not in json.loads(row["atom_ids"]):
                continue
            result.append(
                TreeNode(
                    node_id=row["node_id"], tree_id=row["tree_id"], level=row["level"],
                    node_type=row["node_type"], atom_ids=json.loads(row["atom_ids"]),
                    summary_text=row["summary_text"],
                    summary_provenance=row["summary_provenance"] if "summary_provenance" in row.keys() else "source",
                    parent_id=row["parent_id"], children_ids=json.loads(row["children_ids"]),
                    created_at=row["created_at"], last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                )
            )
        return result

    def queue_atom_assignments(self, tree_id: str, atom_ids: list[str]) -> None:
        """Durably queue atoms for a tree build, ignoring duplicate assignments."""
        unique_atom_ids = list(dict.fromkeys(atom_ids))
        if not unique_atom_ids:
            return

        queued_at = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO pending_assignments (tree_id, atom_id, queued_at)
                VALUES (?, ?, ?)
                """,
                [(tree_id, atom_id, queued_at) for atom_id in unique_atom_ids],
            )

    def queue_source_atoms(self, atom_ids: list[str]) -> None:
        """Queue raw source atoms without choosing a derived tree."""
        unique_atom_ids = list(dict.fromkeys(atom_ids))
        if not unique_atom_ids:
            return
        queued_at = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO pending_source_atoms (atom_id, queued_at)
                VALUES (?, ?)
                """,
                [(atom_id, queued_at) for atom_id in unique_atom_ids],
            )

    def get_pending_source_atom_ids(self) -> set[str]:
        """Return source captures waiting for staged routing/organization."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT atom_id FROM pending_source_atoms").fetchall()
        return {row["atom_id"] for row in rows}

    def get_pending_atom_ids(self, tree_id: str | None = None) -> set[str]:
        """Return queued atom IDs, optionally limited to one tree."""
        with self._get_connection() as conn:
            if tree_id is None:
                rows = conn.execute("SELECT atom_id FROM pending_assignments").fetchall()
            else:
                rows = conn.execute(
                    "SELECT atom_id FROM pending_assignments WHERE tree_id = ?", (tree_id,)
                ).fetchall()
            return {row["atom_id"] for row in rows}

    def get_pending_tree_ids(self) -> list[str]:
        """Return tree IDs with durable assignments waiting to be built."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT tree_id FROM pending_assignments ORDER BY tree_id"
            ).fetchall()
            return [row["tree_id"] for row in rows]

    def has_pending_assignments(self) -> bool:
        with self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM pending_assignments LIMIT 1").fetchone()
            return row is not None

    def has_pending_source_atoms(self) -> bool:
        with self._get_connection() as conn:
            row = conn.execute("SELECT 1 FROM pending_source_atoms LIMIT 1").fetchone()
            return row is not None

    def has_pending_work(self) -> bool:
        return self.has_pending_assignments() or self.has_pending_source_atoms()

    def clear_pending_assignments(
        self, tree_id: str | None = None, atom_ids: set[str] | None = None
    ) -> None:
        """Mark successfully built assignments consumed without losing newer work, or clear all."""
        with self._get_connection() as conn:
            if tree_id is None and atom_ids is None:
                conn.execute("DELETE FROM pending_assignments")
                conn.execute("DELETE FROM pending_source_atoms")
                return

            if tree_id is not None:
                if atom_ids is None:
                    conn.execute("DELETE FROM pending_assignments WHERE tree_id = ?", (tree_id,))
                    return

                if not atom_ids:
                    return
                placeholders = ", ".join("?" for _ in atom_ids)
                conn.execute(
                    f"DELETE FROM pending_assignments WHERE tree_id = ? AND atom_id IN ({placeholders})",
                    (tree_id, *sorted(atom_ids)),
                )

    def clear_pending_source_atoms(self, atom_ids: set[str] | None = None) -> None:
        """Consume only source queue rows included in a successful build."""
        with self._get_connection() as conn:
            if atom_ids is None:
                conn.execute("DELETE FROM pending_source_atoms")
                return
            if not atom_ids:
                return
            placeholders = ", ".join("?" for _ in atom_ids)
            conn.execute(
                f"DELETE FROM pending_source_atoms WHERE atom_id IN ({placeholders})",
                tuple(sorted(atom_ids)),
            )

    def replace_tree(self, tree: Tree, nodes: list[TreeNode]) -> None:
        """Atomically replace the derived SQLite records for one tree.

        Source atoms live in Spine and are never touched.  The caller should
        only invoke this after all summaries and embeddings have been generated.
        """
        if any(node.tree_id != tree.tree_id for node in nodes):
            raise ValueError("All replacement nodes must belong to the replacement tree.")

        with self._get_connection() as conn:
            conn.execute("DELETE FROM tree_nodes WHERE tree_id = ?", (tree.tree_id,))
            conn.execute(
                """
                INSERT INTO trees
                (tree_id, name, description, root_node_id, node_count, leaf_count, depth, created_at, last_consolidated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tree_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    root_node_id = excluded.root_node_id,
                    node_count = excluded.node_count,
                    leaf_count = excluded.leaf_count,
                    depth = excluded.depth,
                    created_at = excluded.created_at,
                    last_consolidated = excluded.last_consolidated
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
            conn.executemany(
                """
                INSERT INTO tree_nodes
                (node_id, tree_id, level, node_type, atom_ids, summary_text,
                 summary_provenance, parent_id, children_ids, created_at,
                 last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.node_id,
                        node.tree_id,
                        node.level,
                        node.node_type,
                        json.dumps(node.atom_ids),
                        node.summary_text,
                        node.summary_provenance,
                        node.parent_id,
                        json.dumps(node.children_ids),
                        node.created_at,
                        node.last_accessed,
                        node.access_count,
                    )
                    for node in nodes
                ],
            )

    def replace_all(self, trees: list[Tree], nodes: list[TreeNode]) -> None:
        """Atomically replace the complete Cortex projection.

        This method is called only after an independent candidate has passed
        structural validation.  The transaction boundary keeps a failed build
        from exposing a partially rebuilt forest.
        """
        tree_ids = {tree.tree_id for tree in trees}
        if any(node.tree_id not in tree_ids for node in nodes):
            raise ValueError("Replacement nodes reference an unknown tree.")
        node_ids = {node.node_id for node in nodes}
        if len(node_ids) != len(nodes):
            raise ValueError("Replacement contains duplicate node IDs.")

        with self._get_connection() as conn:
            conn.execute("DELETE FROM tree_nodes")
            conn.execute("DELETE FROM trees")
            conn.executemany(
                """
                INSERT INTO trees
                (tree_id, name, description, root_node_id, node_count, leaf_count,
                 depth, created_at, last_consolidated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
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
                    )
                    for tree in trees
                ],
            )
            conn.executemany(
                """
                INSERT INTO tree_nodes
                (node_id, tree_id, level, node_type, atom_ids, summary_text,
                 summary_provenance, parent_id, children_ids, created_at,
                 last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        node.node_id,
                        node.tree_id,
                        node.level,
                        node.node_type,
                        json.dumps(node.atom_ids),
                        node.summary_text,
                        node.summary_provenance,
                        node.parent_id,
                        json.dumps(node.children_ids),
                        node.created_at,
                        node.last_accessed,
                        node.access_count,
                    )
                    for node in nodes
                ],
            )

    def reset_derived(self) -> None:
        """Remove Cortex records while preserving the SQLite schema."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM tree_nodes")
            conn.execute("DELETE FROM trees")
            conn.execute("DELETE FROM pending_assignments")
            conn.execute("DELETE FROM index_builds")
            conn.execute("DELETE FROM index_state")
            conn.execute("DELETE FROM graph_edges")

    # ------------------------------------------------------------------
    # Versioned index-build manifest API
    # ------------------------------------------------------------------

    def create_index_build(self, manifest: dict) -> None:
        """Persist a new ``building`` manifest."""
        from trace_lite.cortex.manifest import IndexBuildManifest

        if hasattr(manifest, "to_dict"):
            manifest = manifest.to_dict()
        value = IndexBuildManifest.from_dict(manifest).to_dict()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO index_builds
                (build_id, state, created_at, updated_at, source_atom_count,
                 tree_count, node_count, leaf_count, vector_count,
                 embedding_model, embedding_dimension, config_fingerprint,
                 vector_collection, valid_summary_count, fallback_summary_count,
                 retry_summary_count, passthrough_summary_count, summary_quality,
                 warnings, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    value["build_id"], value["state"], value["created_at"],
                    value["updated_at"], value["source_atom_count"],
                    value["tree_count"], value["node_count"], value["leaf_count"],
                    value["vector_count"], value["embedding_model"],
                    value["embedding_dimension"], value["config_fingerprint"],
                    value["vector_collection"], value["valid_summary_count"],
                    value["fallback_summary_count"], value["retry_summary_count"],
                    value["passthrough_summary_count"],
                    json.dumps(value["summary_quality"], ensure_ascii=False),
                    json.dumps(value["warnings"], ensure_ascii=False), value["error"],
                ),
            )

    def update_index_build(self, build_id: str, **changes) -> dict:
        """Update a manifest and return its current public representation."""
        allowed = {
            "state", "source_atom_count", "tree_count", "node_count", "leaf_count",
            "vector_count", "embedding_model", "embedding_dimension",
            "config_fingerprint", "vector_collection", "valid_summary_count",
            "fallback_summary_count", "retry_summary_count",
            "passthrough_summary_count", "summary_quality", "warnings", "error",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        if not changes:
            return self.get_index_build(build_id) or {}
        changes["updated_at"] = datetime.now(timezone.utc).isoformat()
        assignments = []
        values = []
        for key, value in changes.items():
            if key in {"summary_quality", "warnings"}:
                value = json.dumps(value, ensure_ascii=False)
            assignments.append(f"{key} = ?")
            values.append(value)
        values.append(build_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE index_builds SET {', '.join(assignments)} WHERE build_id = ?",
                values,
            )
        return self.get_index_build(build_id) or {}

    def get_index_build(self, build_id: str | None = None) -> dict | None:
        with self._get_connection() as conn:
            if build_id is None:
                state = conn.execute(
                    "SELECT value FROM index_state WHERE key = 'active_build_id'"
                ).fetchone()
                if not state or not state["value"]:
                    return None
                build_id = state["value"]
            row = conn.execute(
                "SELECT * FROM index_builds WHERE build_id = ?", (build_id,)
            ).fetchone()
            if not row:
                return None
            value = dict(row)
            value["summary_quality"] = json.loads(value.get("summary_quality") or "{}")
            value["warnings"] = json.loads(value.get("warnings") or "[]")
            return value

    def get_active_build(self) -> dict | None:
        """Compatibility/readability alias for the active manifest."""
        return self.get_index_build()

    def get_index_manifest(self) -> dict | None:
        return self.get_index_build()

    def get_latest_index_build(self) -> dict | None:
        """Return the most recently updated build, active or otherwise.

        The active-build pointer intentionally remains empty after the first
        candidate fails, so callers need this separate lookup to explain why
        a legacy placeholder tree was retained.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT build_id FROM index_builds
                ORDER BY updated_at DESC, created_at DESC, build_id DESC
                LIMIT 1
                """
            ).fetchone()
        return self.get_index_build(row["build_id"]) if row else None

    def list_index_builds(self) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT build_id FROM index_builds ORDER BY created_at DESC"
            ).fetchall()
        return [self.get_index_build(row["build_id"]) for row in rows]

    def activate_index_build(
        self, build_id: str, trees: list[Tree], nodes: list[TreeNode]
    ) -> None:
        """Publish a validated Cortex candidate and its manifest atomically."""
        tree_ids = {tree.tree_id for tree in trees}
        if any(node.tree_id not in tree_ids for node in nodes):
            raise ValueError("Replacement nodes reference an unknown tree.")
        with self._get_connection() as conn:
            previous = conn.execute(
                "SELECT value FROM index_state WHERE key = 'active_build_id'"
            ).fetchone()
            previous_id = previous["value"] if previous else None
            conn.execute("DELETE FROM tree_nodes")
            conn.execute("DELETE FROM trees")
            conn.executemany(
                """
                INSERT INTO trees
                (tree_id, name, description, root_node_id, node_count, leaf_count,
                 depth, created_at, last_consolidated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (t.tree_id, t.name, t.description, t.root_node_id,
                     t.node_count, t.leaf_count, t.depth, t.created_at,
                     t.last_consolidated)
                    for t in trees
                ],
            )
            conn.executemany(
                """
                INSERT INTO tree_nodes
                (node_id, tree_id, level, node_type, atom_ids, summary_text,
                 summary_provenance, parent_id, children_ids, created_at,
                 last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (n.node_id, n.tree_id, n.level, n.node_type,
                     json.dumps(n.atom_ids), n.summary_text,
                     n.summary_provenance, n.parent_id, json.dumps(n.children_ids),
                     n.created_at, n.last_accessed, n.access_count)
                    for n in nodes
                ],
            )
            if previous_id and previous_id != build_id:
                conn.execute(
                    "UPDATE index_builds SET state = 'retained', updated_at = ? WHERE build_id = ?",
                    (datetime.now(timezone.utc).isoformat(), previous_id),
                )
            conn.execute(
                "UPDATE index_builds SET state = 'active', updated_at = ? WHERE build_id = ?",
                (datetime.now(timezone.utc).isoformat(), build_id),
            )
            conn.execute(
                "INSERT INTO index_state(key, value) VALUES('active_build_id', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (build_id,),
            )

    def garbage_collect_index_builds(self, keep_build_ids: set[str] | None = None) -> list[str]:
        """Delete non-active manifest rows explicitly requested by maintenance."""
        keep = set(keep_build_ids or set())
        active = self.get_index_build()
        if active:
            keep.add(active["build_id"])
        with self._get_connection() as conn:
            rows = conn.execute("SELECT build_id FROM index_builds").fetchall()
            removed = [row["build_id"] for row in rows if row["build_id"] not in keep]
            for build_id in removed:
                conn.execute("DELETE FROM index_builds WHERE build_id = ?", (build_id,))
        return removed

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
            conn.execute("DELETE FROM pending_assignments WHERE tree_id = ?", (tree_id,))

    def store_edges(self, edges: list[dict]) -> None:
        """Store graph edges into SQLite, ignoring duplicates or inserting/replacing."""
        if not edges:
            return
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO graph_edges
                (edge_id, source_atom_id, target_atom_id, relation_type, weight, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        e["edge_id"],
                        e["source_atom_id"],
                        e["target_atom_id"],
                        e["relation_type"],
                        float(e.get("weight", 1.0)),
                        float(e.get("confidence", 1.0)),
                        e.get("created_at", datetime.now(timezone.utc).isoformat()),
                    )
                    for e in edges
                ],
            )

    def get_neighbor_edges(self, node_ids: list[str], limit: int = 2000) -> list[dict]:
        """Retrieve outgoing and incoming edges for the given node IDs."""
        if not node_ids:
            return []
        placeholders = ", ".join("?" for _ in node_ids)
        with self._get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT edge_id, source_atom_id, target_atom_id, relation_type, weight, confidence, created_at
                FROM graph_edges
                WHERE source_atom_id IN ({placeholders}) OR target_atom_id IN ({placeholders})
                LIMIT ?
                """,
                (*node_ids, *node_ids, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_all_edges(self, limit: int = 10000) -> list[dict]:
        """Retrieve all edges up to limit."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT edge_id, source_atom_id, target_atom_id, relation_type, weight, confidence, created_at
                FROM graph_edges
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

