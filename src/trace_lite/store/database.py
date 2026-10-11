"""Single-node SQLite storage engine: canonical atoms, event ledger, FTS5 index."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .governor import CheckpointRecord, WalGovernor

from . import mint, restart

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


#: G1 incremental delta log: every ``atom`` insert/delete/text-update appends
#: one row in the SAME writer transaction (crash-atomic with the change).
#: Readers consume ``seq > watermark`` instead of re-scanning the corpus.
ATOM_DELTA_DDL = """
CREATE TABLE IF NOT EXISTS atom_delta (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    atom_id    INTEGER NOT NULL,
    op         TEXT NOT NULL,
    text       TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_atom_delta_seq ON atom_delta(seq);
CREATE TRIGGER IF NOT EXISTS trg_delta_atom_ins
    AFTER INSERT ON atom BEGIN
        INSERT INTO atom_delta(atom_id, op, text, created_at)
        VALUES (NEW.id, 'upsert', NEW.text, datetime('now'));
    END;
CREATE TRIGGER IF NOT EXISTS trg_delta_atom_del
    AFTER DELETE ON atom BEGIN
        INSERT INTO atom_delta(atom_id, op, text, created_at)
        VALUES (OLD.id, 'delete', NULL, datetime('now'));
    END;
CREATE TRIGGER IF NOT EXISTS trg_delta_atom_upd
    AFTER UPDATE OF text ON atom
    WHEN NEW.text IS NOT OLD.text BEGIN
        INSERT INTO atom_delta(atom_id, op, text, created_at)
        VALUES (NEW.id, 'upsert', NEW.text, datetime('now'));
    END;
"""


def ensure_atom_delta_log(conn) -> None:
    """Idempotent change-log DDL (additive; safe on every open).

    Rows changed before the first ensure are covered by the reader's next
    full-warm baseline, which watermarks at the then-current max ``seq``.
    """
    conn.executescript(ATOM_DELTA_DDL)


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
        check_same_thread: bool = True,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.governor = governor if governor is not None else WalGovernor(threshold=checkpoint_every)
        self.on_empty_facet: Callable[[str], None] | None = None
        self.conn = sqlite3.connect(
            str(self.path), timeout=30.0, check_same_thread=check_same_thread
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            self.conn.executescript(fh.read())
        # G1: atom change log for incremental delta warming (additive DDL,
        # same open path as the static schema; triggers capture every write
        # from here on).
        ensure_atom_delta_log(self.conn)

    def recover_mint(self, expected_birth: str | None = None) -> dict:
        """N06-c open-path restart recovery (N06b RST/U/C, N06a DES/DUR, D4).

        Call IMMEDIATELY after open, BEFORE any mint or serving (U-2/U-6):
        a reopened unclean DB still presents the old fenced epoch until this
        bumps it, so serving first would reuse the old namespace. Returns
        ``{"birth_uuid", "epoch", "clean", "first_init", "prev_epoch"}``
        (``prev_epoch`` is the pre-bump epoch on the unclean path, else None).

        Paths: no ``mint_epoch`` row => first init (``init_mint`` with
        ``expected_birth`` if given, else a generated uuid4hex; birth is then
        KEPT across restarts — no rotation, AMB-N06-8 stays OPEN) + commit.
        Row present + valid marker matching the fenced (birth, epoch) =>
        clean (C-2/C-3: no bump, k continuity). Row present + marker
        absent/torn/mismatched => unclean (RST-3/U-2: ``advance_epoch`` CAS +
        commit in ONE recovery txn before admission; old rows RETIRED+retained
        per D4). A supplied ``expected_birth`` mismatching the fenced birth
        raises ``StaleEpoch`` with NO effects (FAIL-5; marker untouched so a
        correct retry still sees the clean path). Exhaustion propagates
        fail-closed after rollback. Commits on success; never leaves an open
        txn. ``:memory:`` DBs skip the marker (no restart across them).
        """
        is_memory = str(self.path) == ":memory:"
        if self.conn.in_transaction:
            self.conn.rollback()
        row = self.conn.execute(
            "SELECT birth_uuid, epoch FROM mint_epoch LIMIT 1"
        ).fetchone()
        if row is None:
            if expected_birth is not None:
                if not isinstance(expected_birth, str) or not expected_birth:
                    raise ValueError("expected_birth must be a non-empty string")
                birth = expected_birth
            else:
                birth = uuid.uuid4().hex
            try:
                epoch = mint.init_mint(self.conn, birth)
            except Exception:
                if self.conn.in_transaction:
                    self.conn.rollback()
                raise
            self.conn.commit()
            if not is_memory:
                restart.invalidate_marker(self.path)
            return {
                "birth_uuid": birth,
                "epoch": epoch,
                "clean": True,
                "first_init": True,
                "prev_epoch": None,
            }
        birth = row[0]
        epoch = int(row[1])
        if expected_birth is not None and expected_birth != birth:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise mint.StaleEpoch(
                f"birth mismatch: fenced {birth!r} != expected {expected_birth!r}"
            )
        if is_memory:
            cur = mint.init_mint(self.conn, birth)
            if self.conn.in_transaction:
                self.conn.commit()
            return {
                "birth_uuid": birth,
                "epoch": cur,
                "clean": True,
                "first_init": False,
                "prev_epoch": None,
            }
        is_clean, _reason = restart.check_clean_marker(self.path, birth, epoch)
        if is_clean:
            cur = mint.init_mint(self.conn, birth)
            if self.conn.in_transaction:
                self.conn.commit()
            # Fail-closed invalidation: a crash after open but before the next
            # clean close must leave absent => unclean (C-1).
            restart.invalidate_marker(self.path)
            return {
                "birth_uuid": birth,
                "epoch": cur,
                "clean": True,
                "first_init": False,
                "prev_epoch": None,
            }
        try:
            new_epoch = mint.advance_epoch(self.conn, epoch, expected_birth=birth)
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        self.conn.commit()
        restart.invalidate_marker(self.path)
        return {
            "birth_uuid": birth,
            "epoch": new_epoch,
            "clean": False,
            "first_init": False,
            "prev_epoch": epoch,
        }

    def close(self, *, clean: bool = True) -> None:
        """Close the DB, writing the clean-shutdown marker on the clean path.

        ``clean=True`` (default; existing ``close()`` callers unchanged): if a
        ``mint_epoch`` row exists, reflect the COMMITTED (birth, epoch) in a
        fresh marker (any open txn is rolled back first — close discards it
        anyway — so uncommitted state is never marked clean); if no row, remove
        any stale marker. ``clean=False`` simulates a crash (no marker; any
        present marker is removed). Marker I/O failures are swallowed
        fail-closed (absent marker => next ``recover_mint`` takes the unclean
        path with an extra bump, never reuse). Idempotent.
        """
        try:
            is_memory = str(self.path) == ":memory:"
            if not is_memory:
                if not clean:
                    try:
                        restart.invalidate_marker(self.path)
                    except Exception:
                        pass
                else:
                    try:
                        if self.conn.in_transaction:
                            try:
                                self.conn.rollback()
                            except Exception:
                                pass
                        try:
                            row = self.conn.execute(
                                "SELECT birth_uuid, epoch FROM mint_epoch LIMIT 1"
                            ).fetchone()
                        except Exception:
                            row = None
                        if row is not None:
                            try:
                                restart.write_clean_marker(
                                    self.path, row[0], int(row[1])
                                )
                            except Exception:
                                pass
                        else:
                            try:
                                restart.invalidate_marker(self.path)
                            except Exception:
                                pass
                    except Exception:
                        pass
        finally:
            try:
                self.conn.close()
            except Exception:
                pass

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

    def get_tombstone(self, atom_id: int) -> dict | None:
        """Return the tombstone row for a retired atom, else None (A1 §3).

        Mirrors :meth:`get_atom`: live rows answer retired-vs-unknown only
        through this table (``get_atom`` stays None for both).
        """
        row = self.conn.execute(
            "SELECT * FROM atom_tombstone WHERE atom_id = ?", (atom_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def find_tombstones_by_hash(self, digest: bytes | str) -> list[dict]:
        """Tombstones matching a content hash (bytes or hex); mirrors live lookup."""
        if isinstance(digest, str):
            digest = bytes.fromhex(digest)
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM atom_tombstone WHERE content_hash = ?", (digest,)
            ).fetchall()
        ]

    def count_tombstones(self) -> int:
        """Live tombstone count (retention accounting; interim policy retains all)."""
        return int(self.conn.execute("SELECT COUNT(*) FROM atom_tombstone").fetchone()[0])

    def delete_doc(self, doc_id: str) -> int:
        """Remove every atom of a document (memberships cascade). Returns atoms removed."""
        ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM atom WHERE doc_id = ?", (doc_id,)
            ).fetchall()
        ]
        removed = 0
        for atom_id in ids:
            if self.delete_atom(atom_id) == "deleted":
                removed += 1
        return removed

    def delete_atom(self, atom_id: int) -> str:
        """Retire an atom, leaving a tombstone (A1 §2).

        Returns ``deleted`` | ``already-retired`` | ``never-existed`` (D6
        return-codes; never raises for unknown ids). A retirement writes the
        tombstone, drops the atom row, deletes the FTS postings, and clears
        newly-empty facet centroids in ONE commit. ``retire_gen`` is an
        interim opaque ``gen1:<id>`` label (D5) — never parsed as identity —
        and ``reason`` is always ``retract`` until the edit path lands.
        """
        # FTS5 external-content delete must carry the ORIGINAL text; '' removes
        # zero tokens and leaves dangling postings that corrupt the index.
        row = self.conn.execute(
            "SELECT text, content_hash, doc_id FROM atom WHERE id = ?", (atom_id,)
        ).fetchone()
        if row is None:
            tomb = self.conn.execute(
                "SELECT 1 FROM atom_tombstone WHERE atom_id = ?", (atom_id,)
            ).fetchone()
            if tomb is not None:
                return "already-retired"
            return "never-existed"
        text, content_hash, doc_id = row[0], bytes(row[1]), row[2]
        # Find which facets this atom belonged to before cascade deletes memberships
        facet_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT facet_id FROM memberships WHERE atom_id = ?", (atom_id,)
            ).fetchall()
        ]
        self.conn.execute(
            "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
            (atom_id, text),
        )
        try:
            self.conn.execute(
                "INSERT INTO atom_tombstone"
                " (atom_id, content_hash, doc_id, retire_gen, reason, created_at)"
                " VALUES (?, ?, ?, ?, 'retract', ?)",
                (atom_id, content_hash, doc_id, f"gen1:{uuid.uuid4().hex}", _utcnow()),
            )
        except sqlite3.IntegrityError:
            # Lost a racing delete of the same id: the winner committed the
            # tombstone first. Roll back our partial txn (the FTS delete above
            # is undone; the winner's stands) and report idempotent replay.
            self.conn.rollback()
            tomb = self.conn.execute(
                "SELECT 1 FROM atom_tombstone WHERE atom_id = ?", (atom_id,)
            ).fetchone()
            if tomb is not None:
                return "already-retired"
            raise
        self.conn.execute("DELETE FROM atom WHERE id = ?", (atom_id,))
        # If any facet this atom belonged to now has zero members, clear its centroid blob
        for fid in facet_ids:
            count = self.conn.execute(
                "SELECT COUNT(*) FROM memberships WHERE facet_id = ?", (fid,)
            ).fetchone()[0]
            if count == 0:
                self.conn.execute(
                    "UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (fid,)
                )
                if self.on_empty_facet is not None:
                    try:
                        self.on_empty_facet(fid)
                    except Exception:
                        pass
        self.conn.commit()
        return "deleted"

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
