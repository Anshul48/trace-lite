"""Durable projection outbox (R01 migration 001).

Jobs are enqueued inside the source-replacement writer transaction and consumed
by workers OUTSIDE any writer transaction: claim -> work -> complete/fail.

Lease rules: ``lease_seconds`` minimum is 30 s (packet C1-3 limit). A claim picks
the oldest row that is ``pending`` or whose ``claimed`` lease has expired, so a
dead worker's job is reclaimable and a restarted process resumes from persisted
rows. Completion is idempotent: completing an already-``done`` job is a no-op
success. All methods take an explicit connection and never commit; the caller
owns the transaction boundary (single-statement calls commit immediately so a
crash between worker steps cannot lose state).
"""

from __future__ import annotations

import math
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

MIN_LEASE_SECONDS = 30.0

#: S3 retry/backoff defaults (S4 tuning explicitly out of scope: these are
#: conservative starting values, not tuned constants).
RETRY_BASE_S = 1.0
RETRY_CAP_S = 60.0

_VALID_STATES = ("pending", "claimed", "done", "failed")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JobClaim:
    job_id: str
    job_type: str
    input_revision_id: str
    attempt: int
    lease_owner: str
    lease_expires_at: float


class LeaseError(RuntimeError):
    """Raised when a job is owned by a live lease held by another worker."""


class JobStateError(RuntimeError):
    """Raised when an operation does not apply to the job's current state."""


def enqueue(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    input_revision_id: str,
    idempotency_key: str,
    commit: bool = True,
) -> str:
    """Enqueue a projection job; same idempotency key returns the existing job id."""
    row = conn.execute(
        "SELECT job_id FROM projection_job WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if row is not None:
        return str(row[0])
    job_id = uuid.uuid4().hex
    now = _utcnow()
    own_txn = commit and not conn.in_transaction
    try:
        conn.execute(
            "INSERT INTO projection_job (job_id, idempotency_key, input_revision_id,"
            " job_type, state, attempt, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (job_id, idempotency_key, input_revision_id, job_type, now, now),
        )
    except sqlite3.IntegrityError:
        # Lost a same-key enqueue race: the winner committed first. Drop our
        # partial txn (only when we own it; a commit=False caller owns theirs)
        # and return the winner's id. A missing row means a different
        # constraint failed, so re-raise.
        if own_txn:
            conn.rollback()
        winner = conn.execute(
            "SELECT job_id FROM projection_job WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if winner is None:
            raise
        return str(winner[0])
    if commit:
        conn.commit()
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM projection_job WHERE job_id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM projection_job WHERE state IN ('pending', 'claimed')"
    ).fetchone()
    return int(row[0])


def claim(
    conn: sqlite3.Connection,
    owner: str,
    lease_seconds: float = 30.0,
    *,
    now: float | None = None,
    commit: bool = True,
) -> JobClaim | None:
    """Claim the oldest available job. Returns None when no job is claimable."""
    if lease_seconds < MIN_LEASE_SECONDS:
        raise ValueError(
            f"lease_seconds must be >= {MIN_LEASE_SECONDS:g}s (got {lease_seconds:g})"
        )
    ts = time.time() if now is None else now
    # Serialize writers up front when we own the txn boundary, so the
    # oldest-first subselect sees the latest committed state; the guarded
    # single-statement UPDATE below is what makes the claim atomic.
    own_txn = commit and not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        cursor = conn.execute(
            "UPDATE projection_job SET state = 'claimed', attempt = attempt + 1,"
            " lease_owner = ?, lease_expires_at = ?, error = NULL, updated_at = ?"
            " WHERE job_id = (SELECT job_id FROM projection_job"
            " WHERE state = 'pending' OR (state = 'claimed' AND lease_expires_at <= ?)"
            " ORDER BY created_at, rowid LIMIT 1)"
            " AND (state = 'pending' OR (state = 'claimed' AND lease_expires_at <= ?))"
            " RETURNING job_id, job_type, input_revision_id, attempt, lease_expires_at",
            (owner, ts + lease_seconds, _utcnow(), ts, ts),
        )
        row = cursor.fetchone()
    except Exception:
        if own_txn:
            conn.rollback()
        raise
    if row is None:
        if own_txn:
            conn.rollback()  # nothing claimable; release the write lock
        return None
    if commit:
        conn.commit()
    return JobClaim(
        job_id=str(row[0]),
        job_type=str(row[1]),
        input_revision_id=str(row[2]),
        attempt=int(row[3]),
        lease_owner=owner,
        lease_expires_at=float(row[4] or 0.0),
    )


def complete(
    conn: sqlite3.Connection,
    job_id: str,
    owner: str,
    output_generation: str | None = None,
    *,
    now: float | None = None,
    commit: bool = True,
) -> bool:
    """Mark a claimed job done. Idempotent: an already-done job returns True."""
    job = get_job(conn, job_id)
    if job is None:
        raise JobStateError(f"unknown job {job_id!r}")
    if job["state"] == "done":
        return True
    if job["state"] != "claimed":
        raise JobStateError(f"job {job_id!r} is {job['state']!r}, not claimable work")
    ts = time.time() if now is None else now
    live_lease = (
        job["lease_owner"] != owner
        and job["lease_expires_at"] is not None
        and float(job["lease_expires_at"]) > ts
    )
    if live_lease:
        raise LeaseError(
            f"job {job_id!r} is leased to {job['lease_owner']!r} until"
            f" {job['lease_expires_at']!r}"
        )
    conn.execute(
        "UPDATE projection_job SET state = 'done', lease_owner = ?,"
        " output_generation = COALESCE(?, output_generation),"
        " error = NULL, updated_at = ? WHERE job_id = ?",
        (owner, output_generation, _utcnow(), job_id),
    )
    if commit:
        conn.commit()
    return True


def fail(
    conn: sqlite3.Connection,
    job_id: str,
    owner: str,
    error: str,
    *,
    commit: bool = True,
) -> None:
    """Return a claimed job to pending with its error recorded for retry."""
    job = get_job(conn, job_id)
    if job is None:
        raise JobStateError(f"unknown job {job_id!r}")
    if job["state"] == "done":
        return
    if job["state"] == "claimed" and job["lease_owner"] != owner:
        raise LeaseError(f"job {job_id!r} is leased to {job['lease_owner']!r}")
    conn.execute(
        "UPDATE projection_job SET state = 'pending', lease_owner = NULL,"
        " lease_expires_at = NULL, error = ?, updated_at = ? WHERE job_id = ?",
        (error, _utcnow(), job_id),
    )
    if commit:
        conn.commit()


# -- S3 supervised-worker measurements --------------------------------------

def _parse_created(value: object) -> float | None:
    """Parse an ISO-8601 ``created_at`` value to epoch seconds (None if bad)."""
    try:
        text = str(value)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def queue_stats(
    conn: sqlite3.Connection, *, now: float | None = None
) -> dict:
    """Bounded queue measurement: depth, oldest-job lag, attempts, errors.

    ``depth`` is pending + claimed (all unfinished work). ``queue_lag_s``
    is the age of the oldest unfinished job (None when the queue is
    empty) — the backlog-lag metric S3/B1 require. All reads are small
    aggregates over the outbox table; no projection or source rows are
    touched.
    """
    ts = time.time() if now is None else now
    row = conn.execute(
        "SELECT SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN state = 'claimed' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN state IN ('pending', 'claimed') THEN 1 ELSE 0 END),"
        " COALESCE(MAX(attempt), 0),"
        " SUM(CASE WHEN state IN ('pending', 'claimed')"
        " AND error IS NOT NULL THEN 1 ELSE 0 END),"
        " MIN(CASE WHEN state IN ('pending', 'claimed')"
        " THEN created_at ELSE NULL END)"
        " FROM projection_job"
    ).fetchone()
    pending, claimed, depth = (int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))
    oldest = _parse_created(row[5]) if row[5] is not None else None
    return {
        "pending": pending,
        "claimed": claimed,
        "depth": depth,
        "max_attempt": int(row[3] or 0),
        "with_error": int(row[4] or 0),
        "oldest_created_at": str(row[5]) if row[5] is not None else None,
        "queue_lag_s": (ts - oldest) if oldest is not None else None,
    }


def job_progress(conn: sqlite3.Connection) -> dict:
    """Writer-progress counts by job state (done/total included)."""
    row = conn.execute(
        "SELECT SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN state = 'claimed' THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END),"
        " COUNT(*) FROM projection_job"
    ).fetchone()
    pending, claimed, done, total = (
        int(row[0] or 0), int(row[1] or 0), int(row[2] or 0), int(row[3]))
    return {
        "pending": pending,
        "claimed": claimed,
        "done": done,
        "total": total,
        "done_ratio": (done / total) if total else 1.0,
    }


def retry_delay_s(
    attempt: int, *, base_s: float = RETRY_BASE_S, cap_s: float = RETRY_CAP_S
) -> float:
    """Exponential backoff for a job (or batch) at 1-based ``attempt``.

    Pure function: ``base * 2**(attempt-1)`` capped at ``cap_s``. The
    worker sleeps this between retries; durability of the delay itself
    is intentionally in-memory (a restart retries immediately, which is
    always safe because every stage is idempotent).
    """
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1 (got {attempt})")
    if base_s <= 0 or cap_s <= 0:
        raise ValueError("base_s and cap_s must be positive")
    # Cap test first: 2.0 ** (attempt - 1) overflows float for large
    # attempts, so decide saturation via logarithms (no float pow).
    if attempt - 1 >= math.log2(cap_s / base_s):
        return float(cap_s)
    return min(float(cap_s), float(base_s) * (2.0 ** (attempt - 1)))


def pressure_status(
    conn: sqlite3.Connection, high_watermark: int, *, now: float | None = None
) -> dict:
    """Backpressure signal: pressured when unfinished depth >= watermark.

    A pressured worker keeps draining bounded batches (reads are never
    blocked) but reports pressure so supervisors can slow intake. The
    watermark is caller-chosen; S4 owns tuning it.
    """
    if high_watermark < 1:
        raise ValueError(f"high_watermark must be >= 1 (got {high_watermark})")
    stats = queue_stats(conn, now=now)
    pressured = stats["depth"] >= high_watermark
    return {
        "pressured": pressured,
        "depth": stats["depth"],
        "high_watermark": int(high_watermark),
        "queue_lag_s": stats["queue_lag_s"],
    }
