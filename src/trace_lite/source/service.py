"""Source service: atomic replacement with retained revisions (R01).

One writer transaction per replacement: insert revision + update the
current-source pointer + lexical delta (``atom``/``fts_atoms`` rows) + compact
event + outbox job. Expensive projection work never runs here; workers consume
the outbox outside the writer transaction.

Concurrency: pass ``expected_revision`` for conflicting edits; a stale value is
rejected with :class:`ObsoleteRevisionError` (EVALUATION gate 5). Idempotency:
``(key, payload_hash) -> revision_id``; same key + different payload raises
:class:`IdempotencyConflict` naming the key (gate 2). Crash seam: the private
``_pre_commit_fault`` closed-over hook runs inside the writer transaction just
before commit (gate 3 test seam; not a global).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from trace_lite.store.database import sha256_bytes
from trace_lite.store.outbox import enqueue as _enqueue_job

REFRESH_JOB_TYPE = "projection.refresh"
RETRACT_JOB_TYPE = "projection.retract"


class IdempotencyConflict(ValueError):
    """Same idempotency key submitted with a different payload (or source)."""

    def __init__(self, key: str, detail: str = "") -> None:
        self.key = key
        super().__init__(f"idempotency conflict for key {key!r}" + (f": {detail}" if detail else ""))


class ObsoleteRevisionError(RuntimeError):
    """Writer's expected revision no longer matches the current revision."""

    def __init__(self, source_id: str, expected: str, current: str | None) -> None:
        self.source_id = source_id
        self.expected = expected
        self.current = current
        super().__init__(
            f"source {source_id!r}: expected revision {expected!r} is obsolete"
            f" (current is {current!r})"
        )


@dataclass
class PublishResult:
    revision_id: str
    created: bool
    job_id: str | None
    supersedes: str | None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_for_revision(conn: sqlite3.Connection, revision_id: str) -> str | None:
    row = conn.execute(
        "SELECT job_id FROM projection_job WHERE input_revision_id = ?"
        " ORDER BY created_at LIMIT 1",
        (revision_id,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def get_current(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        "SELECT sr.* FROM source s JOIN source_revision sr"
        " ON sr.revision_id = s.current_revision_id WHERE s.source_id = ?",
        (source_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def get_revision(conn: sqlite3.Connection, revision_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM source_revision WHERE revision_id = ?", (revision_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def list_revisions(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM source_revision WHERE source_id = ?"
            " ORDER BY observed_at, revision_id",
            (source_id,),
        ).fetchall()
    ]


def excerpt(conn: sqlite3.Connection, revision_id: str, start_byte: int, end_byte: int) -> bytes:
    """Resolve a half-open byte range against the retained revision payload."""
    rev = get_revision(conn, revision_id)
    if rev is None:
        raise KeyError(f"unknown revision {revision_id!r}")
    raw = str(rev["text"]).encode("utf-8")
    if not (0 <= start_byte <= end_byte <= len(raw)):
        raise ValueError(
            f"byte range [{start_byte}, {end_byte}) outside payload of {len(raw)} bytes"
        )
    return raw[start_byte:end_byte]


def _remove_stale_atoms(conn: sqlite3.Connection, source_id: str, keep_atom_id: int) -> None:
    """Lexical delta: drop superseded FTS/atom rows for this source (same txn)."""
    stale = conn.execute(
        "SELECT id, text FROM atom WHERE doc_id = ? AND id != ?",
        (source_id, keep_atom_id),
    ).fetchall()
    if stale:
        conn.execute(
            "UPDATE source_revision SET atom_id = NULL WHERE atom_id IN (%s)"
            % ",".join("?" * len(stale)),
            [a for a, _t in stale],
        )
    for atom_id, text in stale:
        facet_ids = [
            r[0]
            for r in conn.execute(
                "SELECT facet_id FROM memberships WHERE atom_id = ?", (atom_id,)
            ).fetchall()
        ]
        conn.execute(
            "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
            (atom_id, text),
        )
        conn.execute("DELETE FROM atom WHERE id = ?", (atom_id,))
        for fid in facet_ids:
            n = conn.execute(
                "SELECT COUNT(*) FROM memberships WHERE facet_id = ?", (fid,)
            ).fetchone()[0]
            if n == 0:
                conn.execute(
                    "UPDATE facets SET centroid_blob = NULL WHERE facet_id = ?", (fid,)
                )


def publish(
    db,
    source_id: str,
    text: str,
    *,
    namespace: str = "default",
    kind: str = "doc",
    locator: str = "",
    access_scope: str = "default",
    expected_revision: str | None = None,
    idempotency_key: str | None = None,
    media: str = "text/plain; charset=utf-8",
    encoding: str = "utf-8",
    authored_at: str | None = None,
    valid_from: str | None = None,
    _pre_commit_fault: Callable[[], None] | None = None,
) -> PublishResult:
    """Atomically replace a source; retains the superseded revision."""
    conn = db.conn
    payload = text.encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    now = _utcnow()
    revision_id = uuid.uuid4().hex
    job_key = f"refresh:{revision_id}"

    conn.execute("BEGIN IMMEDIATE")
    try:
        if idempotency_key is not None:
            hit = conn.execute(
                "SELECT payload_hash, revision_id FROM idempotency_keys WHERE key = ?",
                (idempotency_key,),
            ).fetchone()
            if hit is not None:
                if bytes(hit[0]) != digest:
                    raise IdempotencyConflict(
                        idempotency_key, "payload differs from the original submission"
                    )
                existing = get_revision(conn, str(hit[1]))
                if existing is not None and str(existing["source_id"]) != source_id:
                    raise IdempotencyConflict(
                        idempotency_key,
                        f"key was first used for source {existing['source_id']!r}",
                    )
                conn.rollback()
                return PublishResult(
                    revision_id=str(hit[1]),
                    created=False,
                    job_id=_job_for_revision(conn, str(hit[1])),
                    supersedes=None,
                )

        src = conn.execute(
            "SELECT current_revision_id, namespace FROM source WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        current = str(src[0]) if src is not None and src[0] is not None else None
        if src is not None and str(src[1]) != namespace:
            raise IdempotencyConflict(
                idempotency_key or "",
                f"source {source_id!r} lives in namespace {src[1]!r},"
                f" not {namespace!r}",
            ) if idempotency_key else ValueError(
                f"source {source_id!r} lives in namespace {src[1]!r}, not {namespace!r}"
            )
        if expected_revision is not None and current != expected_revision:
            raise ObsoleteRevisionError(source_id, expected_revision, current)

        if src is None:
            conn.execute(
                "INSERT INTO source (source_id, namespace, kind, locator,"
                " current_revision_id, access_scope, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, NULL, ?, ?, ?)",
                (source_id, namespace, kind, locator or source_id,
                 access_scope, now, now),
            )

        cur = conn.execute(
            "INSERT INTO atom (doc_id, content_hash, start_byte, end_byte, text)"
            " VALUES (?, ?, 0, ?, ?)",
            (source_id, digest, len(payload), text),
        )
        atom_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO fts_atoms(rowid, text) VALUES (?, ?)", (atom_id, text)
        )
        conn.execute(
            "INSERT INTO source_revision (revision_id, source_id, content_hash, media,"
            " encoding, coordinate_space, observed_at, authored_at, valid_from,"
            " supersedes, status, text, byte_len, atom_id, namespace)"
            " VALUES (?, ?, ?, ?, ?, 'raw_utf8_bytes', ?, ?, ?, ?,"
            " 'current', ?, ?, ?, ?)",
            (
                revision_id, source_id, digest, media, encoding, now,
                authored_at, valid_from, current, text, len(payload),
                atom_id, namespace,
            ),
        )
        if current is not None:
            conn.execute(
                "UPDATE source_revision SET status = 'superseded'"
                " WHERE revision_id = ?",
                (current,),
            )
        _remove_stale_atoms(conn, source_id, atom_id)
        conn.execute(
            "UPDATE source SET current_revision_id = ?, updated_at = ?"
            " WHERE source_id = ?",
            (revision_id, now, source_id),
        )
        conn.execute(
            "INSERT INTO events (event_id, stream_id, stream_seq, event_type,"
            " occurred_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                f"source:{source_id}",
                _next_seq(conn, f"source:{source_id}"),
                "source.revision_committed",
                now,
                json.dumps(
                    {
                        "revision_id": revision_id,
                        "supersedes": current,
                        "content_hash": digest.hex(),
                        "namespace": namespace,
                    }
                ),
            ),
        )
        job_id = _enqueue_job(
            conn,
            job_type=REFRESH_JOB_TYPE,
            input_revision_id=revision_id,
            idempotency_key=job_key,
            commit=False,
        )
        if idempotency_key is not None:
            conn.execute(
                "INSERT INTO idempotency_keys (key, payload_hash, revision_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (idempotency_key, digest, revision_id, now),
            )
        if _pre_commit_fault is not None:
            _pre_commit_fault()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    db.governor.note_commit(conn, 1)
    return PublishResult(
        revision_id=revision_id, created=True, job_id=job_id, supersedes=current
    )


def idempotency_key_for(source_id: str, text: str) -> str:
    """INT-1 derived idempotency key: ``(source, payload-hash) -> key``.

    Same source + same payload replays to the prior revision (``publish``
    returns ``created=False`` with no new outbox job); any payload change
    mints a fresh key and therefore a new revision. The same-key/different-
    payload conflict path stays available to explicit-key library callers.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"int-1:{source_id}:{digest}"


def retract_key_for(source_id: str, revision_id: str | None) -> str:
    """INT-1 derived idempotency key for a retract of one source."""
    return f"int-1-retract:{source_id}:{revision_id or 'none'}"


def atom_id_for_revision(conn: sqlite3.Connection, revision_id: str) -> int | None:
    """Lexical atom linked to a revision (None after replacement/retract)."""
    row = conn.execute(
        "SELECT atom_id FROM source_revision WHERE revision_id = ?",
        (revision_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def job_for_revision(conn: sqlite3.Connection, revision_id: str) -> dict | None:
    """Newest outbox job row for a revision (None for pre-outbox revisions)."""
    from trace_lite.store.outbox import get_job

    job_id = _job_for_revision(conn, revision_id)
    if job_id is None:
        return None
    return get_job(conn, job_id)


def facets_of_atom(conn: sqlite3.Connection, atom_id: int) -> list[str]:
    """Facet ids currently holding an atom (pre-write snapshot helper)."""
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT facet_id FROM memberships WHERE atom_id = ?", (atom_id,)
        ).fetchall()
    ]


def _next_seq(conn: sqlite3.Connection, stream_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(stream_seq), -1) FROM events WHERE stream_id = ?",
        (stream_id,),
    ).fetchone()
    return int(row[0]) + 1


def retract(
    db,
    source_id: str,
    *,
    expected_revision: str | None = None,
    idempotency_key: str | None = None,
    _pre_commit_fault: Callable[[], None] | None = None,
) -> PublishResult:
    """Retract a source: no revision stays current; history is retained."""
    conn = db.conn
    now = _utcnow()
    conn.execute("BEGIN IMMEDIATE")
    try:
        src = conn.execute(
            "SELECT current_revision_id FROM source WHERE source_id = ?", (source_id,)
        ).fetchone()
        current = str(src[0]) if src is not None and src[0] is not None else None
        if expected_revision is not None and current != expected_revision:
            raise ObsoleteRevisionError(source_id, expected_revision, current)
        if idempotency_key is not None:
            hit = conn.execute(
                "SELECT payload_hash, revision_id FROM idempotency_keys WHERE key = ?",
                (idempotency_key,),
            ).fetchone()
            if hit is not None:
                conn.rollback()
                return PublishResult(
                    revision_id=str(hit[1]),
                    created=False,
                    job_id=_job_for_revision(conn, str(hit[1])),
                    supersedes=None,
                )
        if current is not None:
            conn.execute(
                "UPDATE source_revision SET status = 'superseded' WHERE revision_id = ?",
                (current,),
            )
        stale = conn.execute(
            "SELECT id, text FROM atom WHERE doc_id = ?", (source_id,)
        ).fetchall()
        if stale:
            conn.execute(
                "UPDATE source_revision SET atom_id = NULL WHERE atom_id IN (%s)"
                % ",".join("?" * len(stale)),
                [a for a, _t in stale],
            )
        for atom_id, text in stale:
            conn.execute(
                "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
                (atom_id, text),
            )
            conn.execute("DELETE FROM atom WHERE id = ?", (atom_id,))
        conn.execute(
            "UPDATE source SET current_revision_id = NULL, updated_at = ?"
            " WHERE source_id = ?",
            (now, source_id),
        )
        conn.execute(
            "INSERT INTO events (event_id, stream_id, stream_seq, event_type,"
            " occurred_at, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                f"source:{source_id}",
                _next_seq(conn, f"source:{source_id}"),
                "source.retracted",
                now,
                json.dumps({"supersedes": current}),
            ),
        )
        tombstone = "retract:" + (current or source_id)
        rev_row = conn.execute(
            "SELECT revision_id FROM source_revision WHERE revision_id = ?",
            (tombstone,),
        ).fetchone()
        anchor = current or tombstone
        if current is None and rev_row is None:
            conn.execute(
                "INSERT INTO source_revision (revision_id, source_id, content_hash,"
                " observed_at, status, text, byte_len, namespace)"
                " VALUES (?, ?, ?, ?, 'superseded', '', 0, 'default')",
                (tombstone, source_id, sha256_bytes("")),
            )
        job_id = _enqueue_job(
            conn,
            job_type=RETRACT_JOB_TYPE,
            input_revision_id=anchor,
            idempotency_key=f"retract:{anchor}",
            commit=False,
        )
        if idempotency_key is not None:
            conn.execute(
                "INSERT INTO idempotency_keys (key, payload_hash, revision_id, created_at)"
                " VALUES (?, ?, ?, ?)",
                (idempotency_key, sha256_bytes("retract:" + source_id), anchor, now),
            )
        if _pre_commit_fault is not None:
            _pre_commit_fault()
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    db.governor.note_commit(conn, 1)
    return PublishResult(
        revision_id=anchor, created=True, job_id=job_id, supersedes=current
    )


def search_current(
    conn: sqlite3.Connection, query: str, namespace: str, limit: int = 20
) -> list[dict]:
    """BM25 search restricted to current revisions of one namespace (scope gate)."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT atom.*, fts_atoms.rank AS bm25_rank,"
            " sr.revision_id AS revision_id, s.namespace AS namespace"
            " FROM fts_atoms"
            " JOIN atom ON atom.id = fts_atoms.rowid"
            " JOIN source_revision sr ON sr.atom_id = atom.id"
            " JOIN source s ON s.source_id = sr.source_id"
            " WHERE fts_atoms MATCH ? AND sr.status = 'current'"
            " AND s.namespace = ? AND sr.namespace = ?"
            " ORDER BY rank LIMIT ?",
            (query, namespace, namespace, limit),
        ).fetchall()
    ]


def verify_store(conn: sqlite3.Connection, *, probe_limit: int = 20) -> dict:
    """Restart verification (gate 10): hashes, FTS postings, generation manifests."""
    revisions = conn.execute(
        "SELECT revision_id, text, content_hash, status, atom_id,"
        " coordinate_space FROM source_revision"
    ).fetchall()
    hash_ok = 0
    hash_bad: list[str] = []
    for rev_id, text, content_hash, _status, _atom_id, _space in revisions:
        if hashlib.sha256(str(text).encode("utf-8")).digest() == bytes(content_hash):
            hash_ok += 1
        else:
            hash_bad.append(str(rev_id))
    try:
        conn.execute("INSERT INTO fts_atoms(fts_atoms) VALUES('integrity-check')")
        fts_integrity = "ok"
    except sqlite3.DatabaseError as exc:
        fts_integrity = f"FAILED: {exc}"
    sources = conn.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    current = conn.execute(
        "SELECT COUNT(*) FROM source_revision WHERE status = 'current'"
    ).fetchone()[0]
    dangling = conn.execute(
        "SELECT COUNT(*) FROM source s LEFT JOIN source_revision sr"
        " ON sr.revision_id = s.current_revision_id"
        " WHERE s.current_revision_id IS NOT NULL AND sr.revision_id IS NULL"
    ).fetchone()[0]
    linked = conn.execute(
        "SELECT COUNT(*) FROM source_revision sr JOIN atom a ON a.id = sr.atom_id"
        " WHERE sr.status = 'current' AND sr.atom_id IS NOT NULL"
    ).fetchone()[0]
    probe_ok = 0
    probe_checked = 0
    probe_failed: list[str] = []
    for rev in conn.execute(
        "SELECT sr.revision_id, sr.text, a.id AS aid FROM source_revision sr"
        " JOIN atom a ON a.id = sr.atom_id"
        " WHERE sr.status = 'current' AND sr.atom_id IS NOT NULL"
        " ORDER BY sr.revision_id LIMIT ?",
        (probe_limit,),
    ).fetchall():
        token = _probe_token(str(rev["text"]))
        if token is None:
            continue
        probe_checked += 1
        hit = conn.execute(
            "SELECT COUNT(*) FROM fts_atoms JOIN atom ON atom.id = fts_atoms.rowid"
            " WHERE fts_atoms MATCH ? AND atom.id = ?",
            (token, rev["aid"]),
        ).fetchone()[0]
        if hit:
            probe_ok += 1
        else:
            probe_failed.append(str(rev["revision_id"]))
    generations = conn.execute(
        "SELECT kind, COUNT(*), SUM(active) FROM projection_generation GROUP BY kind"
    ).fetchall()
    ok = not hash_bad and fts_integrity == "ok" and dangling == 0 and not probe_failed
    return {
        "revisions": len(revisions),
        "hash_ok": hash_ok,
        "hash_bad": hash_bad,
        "sources": int(sources),
        "current_revisions": int(current),
        "dangling_pointers": int(dangling),
        "current_with_lexical_row": int(linked),
        "fts_integrity": fts_integrity,
        "match_probes_checked": probe_checked,
        "match_probes_ok": probe_ok,
        "match_probes_failed": probe_failed,
        "generations": [
            {"kind": k, "count": c, "active": a} for k, c, a in generations
        ],
        "ok": bool(ok),
    }


def _probe_token(text: str) -> str | None:
    for raw in text.lower().split():
        token = "".join(ch for ch in raw if ch.isalpha())
        if len(token) >= 4:
            return token
    return None
