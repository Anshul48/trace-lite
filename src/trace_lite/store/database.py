"""Single-node SQLite storage engine: canonical atoms, event ledger, FTS5 index."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .governor import CheckpointRecord, WalGovernor

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def sha256_bytes(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Owns one SQLite file in WAL mode. Not thread-safe; one instance per thread."""

    def __init__(
        self,
        path: str | Path,
        governor: WalGovernor | None = None,
        checkpoint_every: int = 5000,
    ) -> None:
        self.path = Path(path)
        self.governor = governor if governor is not None else WalGovernor(threshold=checkpoint_every)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            self.conn.executescript(fh.read())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- atoms -----------------------------------------------------------
    def insert_atom(
        self,
        doc_id: str,
        text: str,
        start_byte: int = 0,
        end_byte: int | None = None,
        content_hash: bytes | None = None,
        commit: bool = True,
    ) -> int:
        digest = content_hash if content_hash is not None else sha256_bytes(text)
        end = len(text.encode("utf-8")) if end_byte is None else end_byte
        cur = self.conn.execute(
            "INSERT INTO atom (doc_id, content_hash, start_byte, end_byte, text)"
            " VALUES (?, ?, ?, ?, ?)",
            (doc_id, digest, start_byte, end, text),
        )
        atom_id = int(cur.lastrowid)
        self.conn.execute("INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)", (atom_id, text))
        if commit:
            self.conn.commit()
            self.governor.note_commit(self.conn, 1)
        return atom_id

    def get_atom(self, atom_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM atom WHERE id = ?", (atom_id,)).fetchone()
        return dict(row) if row is not None else None

    def find_atoms_by_hash(self, digest: bytes | str) -> list[dict]:
        if isinstance(digest, str):
            digest = bytes.fromhex(digest)
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM atom WHERE content_hash = ?", (digest,)
            ).fetchall()
        ]

    def count_atoms(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM atom").fetchone()[0])

    def delete_atom(self, atom_id: int) -> None:
        self.conn.execute("INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, '')", (atom_id,))
        self.conn.execute("DELETE FROM atom WHERE id = ?", (atom_id,))
        self.conn.commit()

    def rebuild_fts(self) -> int:
        self.conn.execute("INSERT INTO fts_atoms(fts_atoms) VALUES('rebuild')")
        self.conn.commit()
        return self.count_atoms()

    # -- bulk ingest ------------------------------------------------------
    def bulk_ingest(self, docs: list[tuple[str, str]]) -> list[int]:
        """Insert many (doc_id, text) pairs in one transaction; one post-commit governor check."""
        if not docs:
            return []
        rows = [(doc_id, sha256_bytes(text), 0, len(text.encode("utf-8")), text) for doc_id, text in docs]
        self.conn.executemany(
            "INSERT INTO atom (doc_id, content_hash, start_byte, end_byte, text)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        first_id = int(self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]) - len(rows) + 1
        ids = list(range(first_id, first_id + len(rows)))
        self.conn.executemany(
            "INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)",
            [(atom_id, text) for atom_id, (_, text) in zip(ids, docs)],
        )
        self.conn.commit()
        self.governor.note_commit(self.conn, len(docs))
        return ids

    # -- events ------------------------------------------------------------
    def insert_event(
        self,
        stream_id: str,
        event_type: str,
        payload: dict | None = None,
        stream_seq: int | None = None,
        commit: bool = True,
    ) -> str:
        if stream_seq is None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(stream_seq), -1) FROM events WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            stream_seq = int(row[0]) + 1
        event_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO events (event_id, stream_id, stream_seq, event_type, occurred_at, payload_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, stream_id, stream_seq, event_type, _utcnow(), json.dumps(payload or {})),
        )
        if commit:
            self.conn.commit()
        return event_id

    def list_events(self, stream_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM events WHERE stream_id = ? ORDER BY stream_seq",
                (stream_id,),
            ).fetchall()
        ]

    # -- search --------------------------------------------------------------
    def search_fts(self, query: str, limit: int = 20) -> list[dict]:
        """BM25-ranked search over the external-content FTS5 index (rank ascending = best)."""
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT atom.*, fts_atoms.rank AS bm25_rank FROM fts_atoms"
                " JOIN atom ON atom.id = fts_atoms.rowid"
                " WHERE fts_atoms MATCH ? ORDER BY rank LIMIT ?",
                (query, limit),
            ).fetchall()
        ]

    def fts_table_sql(self) -> str:
        row = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'fts_atoms'"
        ).fetchone()
        return str(row[0]) if row else ""

    def checkpoint_now(self) -> CheckpointRecord:
        return self.governor.checkpoint(self.conn)
