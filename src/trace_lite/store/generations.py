"""Versioned projection generations (R01 migration 001).

R01 only freezes the record shape (ARCH §2: kind, config hash, watermark,
manifest, active status) and the activation primitive used when a worker
publishes output. Real generation kinds are owned by R02+; the ``refresh``
kind used here marks lexical-readiness acknowledgment.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_generation(
    conn: sqlite3.Connection,
    *,
    kind: str,
    config_hash: str,
    watermark: str = "",
    manifest_hash: str | None = None,
    commit: bool = True,
) -> str:
    generation_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO projection_generation (generation_id, kind, config_hash,"
        " watermark, manifest_hash, active, created_at)"
        " VALUES (?, ?, ?, ?, ?, 0, ?)",
        (generation_id, kind, config_hash, watermark, manifest_hash, _utcnow()),
    )
    if commit:
        conn.commit()
    return generation_id


def activate_generation(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    commit: bool = True,
    expected_active_id: str | None = None,
) -> bool:
    """Activate a generation as the sole active row of its kind.

    The swap is one atomic single-statement ``UPDATE`` (CASE over the kind),
    so concurrent activations can never leave zero or two active rows.

    When ``expected_active_id`` is given, the UPDATE carries a
    compare-and-swap guard: the swap happens only if that generation is
    still the kind's active one, decided by the statement's rowcount.
    A mismatch returns False with the DB unchanged -- a lost race is a
    defined outcome, never an exception. Unknown ``generation_id`` still
    raises KeyError (a programming error, not a lost race).

    ``commit=False`` joins the caller's transaction (no BEGIN/COMMIT/
    rollback issued); the exactly-one-winner race guarantee across
    connections then needs the caller to hold the write lock
    (BEGIN IMMEDIATE). With ``commit=True`` this takes BEGIN IMMEDIATE
    itself when it owns the txn boundary; a mismatch then rolls back to
    release the lock and never commits.
    """
    row = conn.execute(
        "SELECT kind FROM projection_generation WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown generation {generation_id!r}")
    kind = row[0]
    # Serialize writers up front when we own the txn boundary, so the CAS
    # guard below reads the latest committed state; the guarded
    # single-statement UPDATE is what makes the swap atomic.
    own_txn = commit and not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        if expected_active_id is None:
            conn.execute(
                "UPDATE projection_generation SET active = CASE"
                " WHEN generation_id = ? THEN 1 ELSE 0 END WHERE kind = ?",
                (generation_id, kind),
            )
        else:
            cursor = conn.execute(
                "UPDATE projection_generation SET active = CASE"
                " WHEN generation_id = ? THEN 1 ELSE 0 END"
                " WHERE kind = ? AND EXISTS (SELECT 1 FROM projection_generation"
                " WHERE kind = ? AND generation_id = ? AND active = 1)",
                (generation_id, kind, kind, expected_active_id),
            )
            if cursor.rowcount == 0:
                # CAS mismatch: the expected generation is not (any longer)
                # the kind's active one. The guarded UPDATE touched zero
                # rows, so the DB is unchanged; release our lock (if any)
                # and report the lost race -- never commit here.
                if own_txn:
                    conn.rollback()
                return False
    except Exception:
        if own_txn:
            conn.rollback()
        raise
    if commit:
        conn.commit()
    return True


def get_active(
    conn: sqlite3.Connection, kind: str
) -> dict | None:
    row = conn.execute(
        "SELECT * FROM projection_generation WHERE kind = ? AND active = 1",
        (kind,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_generations(conn: sqlite3.Connection, kind: str | None = None) -> list[dict]:
    if kind is None:
        rows = conn.execute(
            "SELECT * FROM projection_generation ORDER BY created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM projection_generation WHERE kind = ? ORDER BY created_at",
            (kind,),
        ).fetchall()
    return [dict(r) for r in rows]


def guarded_activate(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    allow: bool,
    reason: str = "",
    commit: bool = True,
    expected_active_id: str | None = None,
) -> dict:
    """Activate a generation only when the worker output is still current.

    S3 generation guard: a worker that computed output from a superseded
    revision must NEVER move the served pointer. Callers compute
    ``allow`` from a currency check (e.g. the revision is still the
    source's current revision at publish time) and call this to publish.
    When ``allow`` is False the active generation is left untouched and
    the call reports ``{"activated": False, ...}`` — stale output is
    skipped, never served.

    The ``allow=True`` path publishes through the atomic CAS activation:
    pass ``expected_active_id`` (the generation the caller saw as active)
    to make the currency check itself atomic — a concurrent publisher
    winning first turns this call into ``{"activated": False, ...}``
    with the DB unchanged instead of silently overriding the winner.
    """
    row = conn.execute(
        "SELECT kind FROM projection_generation WHERE generation_id = ?",
        (generation_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown generation {generation_id!r}")
    if not allow:
        # Skip path performs no writes, so there is nothing to commit;
        # the caller's transaction (if any) is left exactly as found.
        return {"activated": False, "generation_id": generation_id, "reason": reason}
    ok = activate_generation(
        conn, generation_id, commit=commit, expected_active_id=expected_active_id
    )
    return {"activated": ok, "generation_id": generation_id, "reason": reason}
