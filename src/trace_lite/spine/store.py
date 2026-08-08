"""SQLite WAL-backed immutable Spine event ledger and source record store."""

import json
import sqlite3
from pathlib import Path
from typing import Iterator

from trace_lite.spine.models import Atom, SourceArtifact, SpineEvent


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
        return conn

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

                CREATE INDEX IF NOT EXISTS idx_atoms_artifact ON atoms(source_artifact_id);
                CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
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
            conn.execute(
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

    def store_atoms_batch(self, atoms: list[Atom]) -> None:
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO atoms
                (atom_id, content, content_hash, source_artifact_id, sequence_index,
                 char_offset_start, char_offset_end, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        a.atom_id,
                        a.content,
                        a.content_hash,
                        a.source_artifact_id,
                        a.sequence_index,
                        a.char_offset_start,
                        a.char_offset_end,
                        a.created_at,
                        json.dumps(a.metadata, ensure_ascii=False),
                    )
                    for a in atoms
                ],
            )

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
