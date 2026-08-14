"""SQLite WAL-backed immutable Spine event ledger and source record store."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from trace_lite.spine.models import Atom, SourceArtifact, SpineEvent


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


class SpineStore:
    """
    Append-only SQLite event ledger and ground-truth store.
    Uses WAL mode for concurrent performance and data integrity.
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
                CREATE TABLE IF NOT EXISTS events (
                    event_id     TEXT PRIMARY KEY,
                    event_type   TEXT NOT NULL,
                    timestamp    TEXT NOT NULL,
                    payload      TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_artifacts (
                    artifact_id     TEXT PRIMARY KEY,
                    document_name   TEXT,
                    source_uri      TEXT,
                    content_hash    TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    metadata        TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS atoms (
                    atom_id             TEXT PRIMARY KEY,
                    content             TEXT NOT NULL,
                    content_hash        TEXT NOT NULL,
                    source_artifact_id  TEXT NOT NULL,
                    sequence_index      INTEGER NOT NULL,
                    char_offset_start   INTEGER NOT NULL,
                    char_offset_end     INTEGER NOT NULL,
                    created_at          TEXT NOT NULL,
                    metadata            TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(source_artifact_id) REFERENCES source_artifacts(artifact_id)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS atom_fts USING fts5(
                    atom_id UNINDEXED,
                    source_artifact_id UNINDEXED,
                    content,
                    tokenize='porter unicode61'
                );

                CREATE INDEX IF NOT EXISTS idx_atoms_artifact ON atoms(source_artifact_id);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

                -- Folder connections and file fingerprints are source-sync
                -- metadata.  They live beside the immutable artifacts so a
                -- watcher can resume after a process restart without changing
                -- or deleting any prior source record.
                CREATE TABLE IF NOT EXISTS source_connections (
                    connection_id  TEXT PRIMARY KEY,
                    root_path      TEXT NOT NULL UNIQUE,
                    kind           TEXT NOT NULL DEFAULT 'folder',
                    metadata       TEXT NOT NULL DEFAULT '{}',
                    created_at     TEXT NOT NULL,
                    last_synced_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_file_fingerprints (
                    connection_id TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    fingerprint   TEXT NOT NULL,
                    artifact_id   TEXT,
                    file_size     INTEGER,
                    modified_at   REAL,
                    last_seen_at  TEXT NOT NULL,
                    PRIMARY KEY (connection_id, relative_path),
                    FOREIGN KEY(connection_id) REFERENCES source_connections(connection_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_source_files_connection
                    ON source_file_fingerprints(connection_id);
            """)

    def append_event(self, event: SpineEvent) -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO events (event_id, event_type, timestamp, payload) VALUES (?, ?, ?, ?)",
                (
                    event.event_id,
                    event.event_type,
                    event.timestamp,
                    json.dumps(event.payload, ensure_ascii=False),
                ),
            )

    def store_artifact(self, artifact: SourceArtifact) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO source_artifacts
                (artifact_id, document_name, source_uri, content_hash, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.document_name,
                    artifact.source_uri,
                    artifact.content_hash,
                    artifact.created_at,
                    json.dumps(artifact.metadata, ensure_ascii=False),
                ),
            )

    def store_atom(self, atom: Atom) -> None:
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO atoms
                (atom_id, content, content_hash, source_artifact_id, sequence_index,
                 char_offset_start, char_offset_end, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    atom.atom_id,
                    atom.content,
                    atom.content_hash,
                    atom.source_artifact_id,
                    atom.sequence_index,
                    atom.char_offset_start,
                    atom.char_offset_end,
                    atom.created_at,
                    json.dumps(atom.metadata, ensure_ascii=False),
                ),
            )
            if cursor.rowcount > 0:
                conn.execute(
                    """
                    INSERT INTO atom_fts (atom_id, source_artifact_id, content)
                    VALUES (?, ?, ?)
                    """,
                    (
                        atom.atom_id,
                        atom.source_artifact_id,
                        atom.content,
                    ),
                )

    def store_atoms_batch(self, atoms: list[Atom]) -> None:
        if not atoms:
            return
        with self._get_connection() as conn:
            for atom in atoms:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO atoms
                    (atom_id, content, content_hash, source_artifact_id, sequence_index,
                     char_offset_start, char_offset_end, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        atom.atom_id,
                        atom.content,
                        atom.content_hash,
                        atom.source_artifact_id,
                        atom.sequence_index,
                        atom.char_offset_start,
                        atom.char_offset_end,
                        atom.created_at,
                        json.dumps(atom.metadata, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount > 0:
                    conn.execute(
                        """
                        INSERT INTO atom_fts (atom_id, source_artifact_id, content)
                        VALUES (?, ?, ?)
                        """,
                        (
                            atom.atom_id,
                            atom.source_artifact_id,
                            atom.content,
                        ),
                    )

    def search_lexical(self, query_text: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Search atoms lexically using SQLite FTS5 with BM25 ranking.

        Query text is sanitized to avoid FTS5 syntax errors with special characters.
        Returns a list of (atom_id, normalized_score) tuples, ordered by relevance descending.
        """
        if not query_text or top_k <= 0:
            return []

        tokens = re.findall(r"\w+", query_text)
        if not tokens:
            return []

        fts_query = " OR ".join(f'"{t}"' for t in tokens)

        with self._get_connection() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT atom_id, bm25(atom_fts) as score
                    FROM atom_fts
                    WHERE atom_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (fts_query, top_k),
                ).fetchall()
            except sqlite3.OperationalError:
                return []

            results: list[tuple[str, float]] = []
            for row in rows:
                atom_id = row["atom_id"]
                raw_score = float(row["score"])
                val = abs(raw_score)
                normalized_score = float(val / (1.0 + val))
                results.append((atom_id, normalized_score))

            return results

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM atoms WHERE atom_id = ?", (atom_id,)).fetchone()
            if not row:
                return None
            return Atom(
                atom_id=row["atom_id"],
                content=row["content"],
                content_hash=row["content_hash"],
                source_artifact_id=row["source_artifact_id"],
                sequence_index=row["sequence_index"],
                char_offset_start=row["char_offset_start"],
                char_offset_end=row["char_offset_end"],
                created_at=row["created_at"],
                metadata=json.loads(row["metadata"]),
            )

    def get_atoms_by_artifact(self, artifact_id: str) -> list[Atom]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM atoms WHERE source_artifact_id = ? ORDER BY sequence_index ASC",
                (artifact_id,),
            ).fetchall()
            return [
                Atom(
                    atom_id=r["atom_id"],
                    content=r["content"],
                    content_hash=r["content_hash"],
                    source_artifact_id=r["source_artifact_id"],
                    sequence_index=r["sequence_index"],
                    char_offset_start=r["char_offset_start"],
                    char_offset_end=r["char_offset_end"],
                    created_at=r["created_at"],
                    metadata=json.loads(r["metadata"]),
                )
                for r in rows
            ]

    def get_artifact(self, artifact_id: str) -> SourceArtifact | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM source_artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if not row:
                return None
            return SourceArtifact(
                artifact_id=row["artifact_id"],
                document_name=row["document_name"],
                source_uri=row["source_uri"],
                content_hash=row["content_hash"],
                created_at=row["created_at"],
                metadata=json.loads(row["metadata"]),
            )

    def list_artifacts(self) -> list[SourceArtifact]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM source_artifacts ORDER BY created_at DESC").fetchall()
            return [
                SourceArtifact(
                    artifact_id=row["artifact_id"],
                    document_name=row["document_name"],
                    source_uri=row["source_uri"],
                    content_hash=row["content_hash"],
                    created_at=row["created_at"],
                    metadata=json.loads(row["metadata"]),
                )
                for row in rows
            ]

    def count_atoms(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as count FROM atoms").fetchone()
            return row["count"] if row else 0

    def list_atoms(self) -> list[Atom]:
        """Return every atom in stable source order.

        This is primarily used to recover Cortex indexes after an older
        installation created Spine atoms without durable tree assignments.
        """
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM atoms
                ORDER BY source_artifact_id ASC, sequence_index ASC, atom_id ASC
                """
            ).fetchall()
            return [
                Atom(
                    atom_id=row["atom_id"],
                    content=row["content"],
                    content_hash=row["content_hash"],
                    source_artifact_id=row["source_artifact_id"],
                    sequence_index=row["sequence_index"],
                    char_offset_start=row["char_offset_start"],
                    char_offset_end=row["char_offset_end"],
                    created_at=row["created_at"],
                    metadata=json.loads(row["metadata"]),
                )
                for row in rows
            ]

    def get_events(self, limit: int = 50) -> list[SpineEvent]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
            return [
                SpineEvent(
                    event_id=r["event_id"],
                    event_type=r["event_type"],
                    timestamp=r["timestamp"],
                    payload=json.loads(r["payload"]),
                )
                for r in rows
            ]

    def upsert_source_connection(
        self,
        connection_id: str,
        root_path: str,
        *,
        kind: str = "folder",
        metadata: dict | None = None,
    ) -> dict:
        """Persist a source connection and return its public metadata."""
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT created_at FROM source_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
            created_at = row["created_at"] if row else now
            conn.execute(
                """
                INSERT INTO source_connections
                    (connection_id, root_path, kind, metadata, created_at, last_synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    root_path = excluded.root_path,
                    kind = excluded.kind,
                    metadata = excluded.metadata,
                    last_synced_at = excluded.last_synced_at
                """,
                (connection_id, str(root_path), kind, metadata_json, created_at, now),
            )
        return {
            "connection_id": connection_id,
            "root_path": str(root_path),
            "kind": kind,
            "metadata": metadata or {},
            "created_at": created_at,
            "last_synced_at": now,
        }

    def get_source_file_fingerprint(
        self, connection_id: str, relative_path: str
    ) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT connection_id, relative_path, fingerprint, artifact_id,
                       file_size, modified_at, last_seen_at
                FROM source_file_fingerprints
                WHERE connection_id = ? AND relative_path = ?
                """,
                (connection_id, relative_path),
            ).fetchone()
            return dict(row) if row else None

    def record_source_file_fingerprint(
        self,
        connection_id: str,
        relative_path: str,
        fingerprint: str,
        *,
        artifact_id: str | None = None,
        file_size: int | None = None,
        modified_at: float | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO source_file_fingerprints
                    (connection_id, relative_path, fingerprint, artifact_id,
                     file_size, modified_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id, relative_path) DO UPDATE SET
                    fingerprint = excluded.fingerprint,
                    artifact_id = excluded.artifact_id,
                    file_size = excluded.file_size,
                    modified_at = excluded.modified_at,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    connection_id,
                    relative_path,
                    fingerprint,
                    artifact_id,
                    file_size,
                    modified_at,
                    now,
                ),
            )

    def list_source_connections(self) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT connection_id, root_path, kind, metadata,
                       created_at, last_synced_at
                FROM source_connections
                ORDER BY last_synced_at DESC
                """
            ).fetchall()
            return [
                {
                    "connection_id": row["connection_id"],
                    "root_path": row["root_path"],
                    "kind": row["kind"],
                    "metadata": json.loads(row["metadata"]),
                    "created_at": row["created_at"],
                    "last_synced_at": row["last_synced_at"],
                }
                for row in rows
            ]

    def list_source_file_fingerprints(self, connection_id: str) -> list[dict]:
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT connection_id, relative_path, fingerprint, artifact_id,
                       file_size, modified_at, last_seen_at
                FROM source_file_fingerprints
                WHERE connection_id = ?
                ORDER BY relative_path
                """,
                (connection_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def replay_events(self, since: str | None = None) -> Iterator[SpineEvent]:
        with self._get_connection() as conn:
            if since:
                rows = conn.execute(
                    "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp ASC", (since,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM events ORDER BY timestamp ASC").fetchall()

            for r in rows:
                yield SpineEvent(
                    event_id=r["event_id"],
                    event_type=r["event_type"],
                    timestamp=r["timestamp"],
                    payload=json.loads(r["payload"]),
                )
