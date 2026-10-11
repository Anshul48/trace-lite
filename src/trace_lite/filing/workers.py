"""S3 supervised maintenance workers (contract TL-QN-2026-09-12.1, steering S3).

Supervised worker lifecycle over the existing R01 outbox / R01 generation
machinery. Workers reuse the exact shared primitives the online
(``ingest_note``) and bulk (``sync_notes``) write paths use —
``source.project.drain_outbox`` (bounded claim -> project -> complete/fail
with the obsolete-revision skip) — so background maintenance can never
diverge from writer-driven correctness. ``source/project.py`` itself is
untouched: the worker is a supervisor around it, not a fork of it.

- Leases: claims carry the R01 lease (30 s floor) inside the shared
  drain. A dead worker's jobs become reclaimable on expiry and a
  restarted worker resumes them with no user replay request.
- Bounded batches: every pass drains at most ``batch_limit`` jobs
  (default: the shared ``DRAIN_LIMIT``; S4 owns tuning).
- Retry/backoff: failed batches back off exponentially via the shared
  ``outbox.retry_delay_s``. The delay is in-memory by design: a restart
  retries immediately, which is always safe because every stage is
  idempotent.
- Backpressure: depth >= high watermark reports pressure; the worker
  keeps draining bounded batches and lexical reads are never blocked.
  Producer-side throttling is S4 scope; S3 delivers the signal plus the
  bound.
- Generation guard: a pass publishes a new ``filing`` generation only
  when it completed real projections. Superseded revisions are
  completed without projecting (the drain's obsolete-worker rule) and
  the stale path never moves the served pointer (see
  ``generations.guarded_activate``).
- Observability: every pass records queue depth, queue lag, and writer
  progress into ``WorkerStats`` and the returned report.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from trace_lite.store import generations as gen_store
from trace_lite.store import outbox as outbox_store
from trace_lite.store.database import Database

#: WAL size (bytes) above which a pass-end checkpoint is attempted.
#: Override: ``TRACE_LITE_WAL_CHECKPOINT_MB`` (0 disables size-triggered
#: checkpoints; the count-based governor path still applies).
WAL_CHECKPOINT_BYTES = 64 * 1024 * 1024

#: Yield between finite-backfill batches so a tight drain loop never
#: starves concurrent writers (mirrors the in-pass drain pacing).
BACKFILL_PACE_S = 0.005


def _wal_checkpoint_bytes() -> int:
    try:
        mb = float(os.environ.get("TRACE_LITE_WAL_CHECKPOINT_MB",
                                  str(WAL_CHECKPOINT_BYTES / (1024 * 1024))))
    except ValueError:
        return WAL_CHECKPOINT_BYTES
    if mb <= 0:
        return 0
    return int(mb * 1024 * 1024)


def _wal_bytes(db_path: str | Path | None) -> int | None:
    if db_path is None:
        return None
    try:
        return Path(str(db_path) + "-wal").stat().st_size
    except OSError:
        return 0


def maybe_checkpoint(db: Database, *, threshold_bytes: int | None = None) -> dict:
    """Bounded WAL checkpoint + change-log prune at a pass boundary.

    G2 cycle 2 contention discipline (worker-owned: only the worker sees
    pass boundaries where no txn is open, and ``WalGovernor.checkpoint``
    MUST run outside open write txns). Uses the existing governor helper
    (``PRAGMA wal_checkpoint(PASSIVE)``) when the WAL exceeds the size
    threshold; always prunes the atom change log to its retained window
    (relocated off the query path). Never raises; every outcome — including
    ``busy`` (a writer held RESERVED) and ``checkpointed < log`` (readers
    pinning snapshots, the checkpoint-starvation signature) — is recorded
    in the returned receipt. A call never guarantees truncation: PASSIVE
    reclaims only frames no reader pins, and checkpointed frames are
    reused by later writes so the file stops growing once checkpoints
    keep up. TRUNCATE is deliberately never issued (it would stall the
    worker on live readers).
    """
    limit = _wal_checkpoint_bytes() if threshold_bytes is None else threshold_bytes
    wal_before = _wal_bytes(db.path)
    pruned = 0
    try:
        from trace_lite.router.hybrid import prune_atom_delta_log
        pruned = prune_atom_delta_log(db.conn)
    except Exception as exc:
        return {"checked": True, "checkpointed": False, "pruned": 0,
                "wal_before": wal_before, "wal_after": wal_before,
                "reason": f"prune failed: {exc}"}
    if not limit or wal_before is None or wal_before < limit:
        return {"checked": True, "checkpointed": False, "pruned": pruned,
                "wal_before": wal_before, "wal_after": wal_before,
                "reason": "below threshold"}
    if db.conn.in_transaction:
        return {"checked": True, "checkpointed": False, "pruned": pruned,
                "wal_before": wal_before, "wal_after": wal_before,
                "reason": "open txn; next boundary retries"}
    try:
        record = db.governor.checkpoint(db.conn)
    except Exception as exc:
        return {"checked": True, "checkpointed": False, "pruned": pruned,
                "wal_before": wal_before, "wal_after": _wal_bytes(db.path),
                "reason": f"checkpoint failed: {exc}"}
    return {"checked": True, "checkpointed": True, "pruned": pruned,
            "wal_before": wal_before, "wal_after": _wal_bytes(db.path),
            "busy": record.busy, "log_frames": record.log_frames,
            "checkpointed_frames": record.checkpointed_frames}


@dataclass
class WorkerConfig:
    """Supervised-worker settings. Defaults reuse shared constants (S4 owns tuning)."""

    owner: str = "s3-worker"
    batch_limit: int | None = None
    high_watermark: int | None = None
    base_backoff_s: float = outbox_store.RETRY_BASE_S
    cap_backoff_s: float = outbox_store.RETRY_CAP_S
    poll_interval_s: float = 1.0

    def resolved_batch_limit(self) -> int:
        if self.batch_limit is not None:
            return int(self.batch_limit)
        from trace_lite.source import project as _project

        return int(_project.DRAIN_LIMIT)

    def resolved_watermark(self) -> int:
        if self.high_watermark is not None:
            return int(self.high_watermark)
        return self.resolved_batch_limit()


@dataclass
class WorkerStats:
    """Cumulative supervised-lifecycle counters (in-memory, per worker)."""

    batches: int = 0
    claimed_total: int = 0
    completed_total: int = 0
    skipped_total: int = 0
    failed_total: int = 0
    publications: int = 0
    pressure_passes: int = 0
    consecutive_failures: int = 0
    backoff_s_total: float = 0.0
    last_error: str | None = None
    last_depth: int = 0
    last_queue_lag_s: float | None = None
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        return {
            "batches": self.batches,
            "claimed_total": self.claimed_total,
            "completed_total": self.completed_total,
            "skipped_total": self.skipped_total,
            "failed_total": self.failed_total,
            "publications": self.publications,
            "pressure_passes": self.pressure_passes,
            "consecutive_failures": self.consecutive_failures,
            "backoff_s_total": round(self.backoff_s_total, 3),
            "last_error": self.last_error,
            "last_depth": self.last_depth,
            "last_queue_lag_s": self.last_queue_lag_s,
        }


class MaintenanceWorker:
    """Supervised drain loop over one SQLite file.

    Either borrows a caller-owned ``Database`` (single-threaded use,
    never closed here) or owns a private handle opened from ``db_path``
    (background-thread use: one-instance-per-thread). Work always runs
    outside any writer transaction on its own connection.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        db: Database | None = None,
        config: WorkerConfig | None = None,
    ) -> None:
        if (db_path is None) == (db is None):
            raise ValueError("pass exactly one of db_path or db")
        self._owned = db is None
        # Owned handles serve the background thread, so they opt out of
        # the same-thread check (the worker is still the sole user of
        # its handle). Borrowed handles stay exactly as the caller made
        # them and are never closed here.
        self._db = (
            Database(db_path, check_same_thread=False) if db is None else db
        )
        assert db_path is not None or db is not None
        self.config = config if config is not None else WorkerConfig()
        self.stats = WorkerStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- single supervised pass ----------------------------------------

    def run_once(self, *, now: float | None = None) -> dict:
        """Drain one bounded batch, publish iff projections completed.

        Returns the pass report (also folded into ``self.stats``). Never
        raises on data errors: a failing batch is recorded with its
        backoff, never fatal to the supervisor.
        """
        from trace_lite.source import project as _project

        conn = self._db.conn
        queue_before = outbox_store.queue_stats(conn, now=now)
        pressure = outbox_store.pressure_status(
            conn, self.config.resolved_watermark(), now=now
        )
        batch_limit = self.config.resolved_batch_limit()
        try:
            drain = _project.drain_outbox(
                self._db, None, owner=self.config.owner, limit=batch_limit
            )
        except Exception as exc:
            self.stats.batches += 1
            self.stats.consecutive_failures += 1
            backoff = outbox_store.retry_delay_s(
                self.stats.consecutive_failures,
                base_s=self.config.base_backoff_s,
                cap_s=self.config.cap_backoff_s,
            )
            self.stats.backoff_s_total += backoff
            self.stats.last_error = str(exc)[:300]
            queue_after = outbox_store.queue_stats(conn)
            self.stats.last_depth = queue_after["depth"]
            self.stats.last_queue_lag_s = queue_after["queue_lag_s"]
            return {
                "owner": self.config.owner,
                "drained": 0,
                "completed": [],
                "skipped": [],
                "failed": [{"error": str(exc)[:200]}],
                "pressured": pressure["pressured"],
                "backoff_s": backoff,
                "published_generation": None,
                "publish_skipped_reason": "drain raised; nothing completed",
                "queue_before": queue_before,
                "queue_after": queue_after,
                "progress": outbox_store.job_progress(conn),
            }
        completed = list(drain["completed"])
        skipped = list(drain["skipped"])
        failed = list(drain["failed"])
        self.stats.batches += 1
        self.stats.claimed_total += int(drain["drained"])
        self.stats.completed_total += len(completed)
        self.stats.skipped_total += len(skipped)
        self.stats.failed_total += len(failed)
        if pressure["pressured"]:
            self.stats.pressure_passes += 1
        if failed:
            self.stats.consecutive_failures += 1
            backoff = outbox_store.retry_delay_s(
                self.stats.consecutive_failures,
                base_s=self.config.base_backoff_s,
                cap_s=self.config.cap_backoff_s,
            )
            self.stats.backoff_s_total += backoff
            self.stats.last_error = str(failed[0].get("error", failed[0]))[:300]
        else:
            self.stats.consecutive_failures = 0
            backoff = 0.0
            if completed:
                self.stats.last_error = None
        published, skip_reason = self._maybe_publish(completed)
        queue_after = outbox_store.queue_stats(conn)
        self.stats.last_depth = queue_after["depth"]
        self.stats.last_queue_lag_s = queue_after["queue_lag_s"]
        # Pass-boundary maintenance (worker-owned): bounded WAL checkpoint
        # + change-log prune. Never raises; recorded, never forced.
        checkpoint = maybe_checkpoint(self._db)
        return {
            "owner": self.config.owner,
            "drained": int(drain["drained"]),
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
            "deferred": list(drain.get("deferred", [])),
            "pressured": pressure["pressured"],
            "backoff_s": backoff,
            "published_generation": published,
            "publish_skipped_reason": skip_reason,
            "queue_before": queue_before,
            "queue_after": queue_after,
            "progress": outbox_store.job_progress(conn),
            "txn_hold_s": dict(drain.get("txn_hold_s", {})),
            "checkpoint": checkpoint,
        }

    def _maybe_publish(self, completed: list[str]) -> tuple[str | None, str]:
        """Publish one ``filing`` generation iff real projections completed.

        Skipped (superseded) completions alone never trigger publication:
        they changed nothing served. The watermark is read at publish
        time over committed state, so a worker can never publish
        obsolete results — currency was already enforced per revision
        inside the shared drain.
        """
        if not completed:
            return None, "no completed projections; nothing to publish"
        from trace_lite.api.app import (
            FILING_CONFIG_HASH,
            FILING_GENERATION_KIND,
            filing_watermark,
        )

        conn = self._db.conn
        gid = gen_store.create_generation(
            conn,
            kind=FILING_GENERATION_KIND,
            config_hash=FILING_CONFIG_HASH,
            watermark=filing_watermark(conn),
            commit=False,
        )
        gen_store.activate_generation(conn, gid, commit=False)
        conn.commit()
        self.stats.publications += 1
        return gid, ""

    # -- finite backfill ------------------------------------------------

    def run_until_drained(self, *, max_batches: int = 10_000) -> dict:
        """Run bounded passes until no unfinished work remains (finite backfill).

        Stops early with ``drained_all=False`` when a pass claims nothing
        while depth is nonzero (e.g. jobs held under live foreign leases),
        when passes stop making forward progress (permanently failing jobs
        need new arrivals or attention — the timed ``run_forever`` loop,
        not a tight spin, retries those), or when ``max_batches`` is
        exhausted — all reported honestly, never spun on.
        """
        batches = 0
        claimed = completed = skipped = failed = 0
        futile = 0
        while batches < max_batches:
            report = self.run_once()
            batches += 1
            # Yield between batches: a tight backfill loop must not starve
            # concurrent writers (ms-scale acquisition gaps).
            time.sleep(BACKFILL_PACE_S)
            claimed += int(report["drained"])
            done_now = len(report["completed"]) + len(report["skipped"])
            completed += len(report["completed"])
            skipped += len(report["skipped"])
            failed += len(report["failed"])
            futile = 0 if done_now else futile + 1
            if int(report["queue_after"]["depth"]) == 0:
                return {
                    "drained_all": True, "batches": batches, "claimed": claimed,
                    "completed": completed, "skipped": skipped, "failed": failed,
                }
            if int(report["drained"]) == 0:
                return {
                    "drained_all": False, "batches": batches, "claimed": claimed,
                    "completed": completed, "skipped": skipped, "failed": failed,
                    "reason": "no claimable jobs under live leases or backoff",
                }
            if futile >= 3:
                return {
                    "drained_all": False, "batches": batches, "claimed": claimed,
                    "completed": completed, "skipped": skipped, "failed": failed,
                    "reason": "no forward progress for 3 passes;"
                    " failing jobs await new arrivals or attention",
                }
        return {
            "drained_all": False, "batches": batches, "claimed": claimed,
            "completed": completed, "skipped": skipped, "failed": failed,
            "reason": "max_batches exhausted",
        }

    # -- supervised background loop -------------------------------------

    def run_forever(
        self,
        *,
        stop: threading.Event | None = None,
        poll_interval_s: float | None = None,
    ) -> WorkerStats:
        """Supervised loop: bounded passes until ``stop`` is set.

        One bad pass never kills the loop (exception isolation); failed
        passes sleep the computed backoff, clean passes sleep the poll
        interval. Returns the cumulative stats.
        """
        flag = stop if stop is not None else self._stop
        interval = (
            self.config.poll_interval_s if poll_interval_s is None else poll_interval_s
        )
        while not flag.is_set():
            try:
                report = self.run_once()
                delay = report["backoff_s"] if report["failed"] else interval
            except Exception as exc:  # supervisor never dies on one bad pass
                self.stats.last_error = str(exc)[:300]
                delay = interval
            flag.wait(float(delay) if delay > 0 else interval)
        return self.stats

    def start_background(self, *, poll_interval_s: float | None = None) -> threading.Thread:
        """Start the supervised loop on a daemon thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            kwargs={"stop": self._stop, "poll_interval_s": poll_interval_s},
            name=f"s3-worker:{self.config.owner}",
            daemon=True,
        )
        self._thread.start()
        return self._thread

    def stop(self, *, timeout_s: float = 10.0) -> WorkerStats:
        """Signal the background loop to stop and wait for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        return self.stats

    def close(self) -> WorkerStats:
        """Stop the loop and close the handle when it is owned here."""
        stats = self.stop()
        if self._owned:
            try:
                self._db.close()
            except Exception:
                pass
        return stats


def status_block(worker: MaintenanceWorker | None) -> dict:
    """Additive ``/api/status`` worker block (absent worker reads disabled)."""
    if worker is None:
        return {"enabled": False}
    out = {"enabled": True, "owner": worker.config.owner}
    out.update(worker.stats.snapshot())
    return out
