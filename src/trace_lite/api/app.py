"""Loopback REST daemon (default :8420) consumed by the Obsidian companion plugin.

R06 concurrency model (ARCH §3, §8): the global ``guard`` mutex is gone.
One writer connection is serialized by ``write_lock`` (SQLite WAL admits a
single writer); every reader thread owns a private WAL connection plus a
private warmed router (R01 "one-instance-per-thread" rule), so queries
never block on syncs. Readers pin the active ``filing`` projection
generation and rebuild their warmed views when the writer publishes a new
one (generation-swapped reads); freshness is reported honestly instead of
the hardcoded R00 trusted/organized state.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..filing.engine import FilingEngine, unpack_vector
from ..filing.holon import NEEDS_ATTENTION
from ..filing.taxonomy import Taxonomy
from ..filing.workers import MaintenanceWorker, WorkerConfig, status_block
from ..obsidian.parser import facet_hints, parse_note
from ..router.cascade import CascadeRouter
from ..source import coverage_state
from ..source import project as projection
from ..source import service as source_service
from ..store import generations as gen_store
from ..store import outbox as outbox_store
from ..store.database import Database

DEFAULT_PORT = int(os.environ.get("TRACE_LITE_PORT", "8420"))

#: Projection-generation kind published by every R06 writer batch. New rows
#: only (existing R01/R03 kinds untouched); pre-R06 code never queries this
#: kind, so the rows are inert under rollback.
FILING_GENERATION_KIND = "filing"
FILING_CONFIG_HASH = hashlib.sha256(b"r06:filing:centroid_dim=128").hexdigest()[:16]


class QueryRequest(BaseModel):
    query: str | None = None
    query_text: str | None = None
    limit: int | None = None
    top_k: int | None = None
    mode: str = "hybrid"


class SyncRequest(BaseModel):
    path: str | None = None


class IngestRequest(BaseModel):
    text: str
    document_name: str


class DossierRequest(BaseModel):
    """R07 additive: compile a query-generated evidence dossier.

    Read-only over the generation-pinned reader stack; touches none of the
    R06 writer/generation/freshness logic bodies.
    """

    query: str
    objective: str | None = None
    goal_id: str = ""
    scope_namespaces: list[str] | None = None
    access_scopes: list[str] | None = None
    current_only: bool = True
    limit: int = 10
    mode: str = "hybrid"
    mandatory_invariants: list[dict[str, Any]] | None = None
    max_total_tokens: int = 3500
    salient_evidence_budget: int = 1800
    mandatory_invariant_tokens: int = 600


@dataclass
class ReaderView:
    """Thread-private read stack: own WAL connection + warmed router."""

    db: Database
    taxonomy: Taxonomy
    engine: FilingEngine
    router: CascadeRouter
    generation_id: str | None


#: S3 supervised maintenance worker: opt-in via ``TRACE_LITE_WORKER=1``
#: (default off so the S1/S2 qualification bars run the exact
#: synchronous-drain behavior they pinned; enable for autonomous
#: backfill without user replay requests). Poll interval override:
#: ``TRACE_LITE_WORKER_POLL_S`` (seconds, default 1.0).
WORKER_ENV_VAR = "TRACE_LITE_WORKER"
WORKER_POLL_ENV_VAR = "TRACE_LITE_WORKER_POLL_S"


def worker_enabled_from_env() -> bool:
    return os.environ.get(WORKER_ENV_VAR, "") == "1"


def worker_poll_interval_from_env(default: float = 1.0) -> float:
    try:
        return max(0.01, float(os.environ.get(WORKER_POLL_ENV_VAR, str(default))))
    except ValueError:
        return default


def start_maintenance_worker(
    state: State,
    db_path: str | Path,
    *,
    config: WorkerConfig | None = None,
    poll_interval_s: float | None = None,
) -> MaintenanceWorker:
    """Start the supervised background worker (idempotent; S3 wiring + G1).

    The worker owns a private WAL handle on the same file and drains the
    shared outbox outside every writer transaction, so stalled semantic
    work never blocks lexical retrieval. G1: write paths never drain, so
    this worker is the sole projection driver (auto-started under
    persistent serving).
    """
    existing = state.worker
    if existing is not None:
        return existing
    cfg = config if config is not None else WorkerConfig(
        poll_interval_s=worker_poll_interval_from_env()
    )
    worker = MaintenanceWorker(db_path, config=cfg)
    worker.start_background(poll_interval_s=poll_interval_s)
    state.worker = worker
    return worker


def stop_maintenance_worker(state: State) -> None:
    """Stop the background worker and close its private handle (S3 wiring only)."""
    worker = state.worker
    state.worker = None
    if worker is not None:
        try:
            worker.close()
        except Exception:
            pass


#: S6 persistent vector serving: opt-in via ``TRACE_LITE_SERVED_PERSISTENT=1``
#: (default off so the S1/S2/S5 qualification bars run the exact legacy
#: query-triggered build behavior they pinned; enable to cut the query path
#: over to the S4 published base + bounded delta with background seeding).
#: Index-dir override: ``TRACE_LITE_SERVED_INDEX_DIR`` (default: a
#: ``<db-stem>.served`` sibling of the database file). Disk-budget override
#: (bytes): ``TRACE_LITE_SERVED_DISK_BUDGET_BYTES`` (default: no byte cap,
#: headroom-ratio check only). Queue-lag bound override (seconds):
#: ``TRACE_LITE_QUEUE_LAG_BOUND_S`` (default 60).
SERVED_PERSISTENT_ENV_VAR = "TRACE_LITE_SERVED_PERSISTENT"
SERVED_INDEX_DIR_ENV_VAR = "TRACE_LITE_SERVED_INDEX_DIR"
SERVED_DISK_BUDGET_ENV_VAR = "TRACE_LITE_SERVED_DISK_BUDGET_BYTES"
QUEUE_LAG_BOUND_ENV_VAR = "TRACE_LITE_QUEUE_LAG_BOUND_S"

#: Frozen S6 release-envelope budgets (admission thresholds; S4 owns tuning).
S6_QUEUE_LAG_BOUND_S = 60.0
S6_DISK_HEADROOM_MIN_RATIO = 10.0
S6_FRESHNESS_BUDGET_S = 1.0  # S2 query-path freshness budget, re-enforced here

#: Bound on a clean shutdown's wait for in-flight served maintenance.
SERVED_SHUTDOWN_JOIN_S = 30.0

#: HTTP write-path contention discipline (G2 repair cycle 2): bounded busy
#: retries with backoff; after exhaustion HTTP 503 + Retry-After (never 500
#: for contention). Each attempt also waits up to the connection's 5 s
#: SQLite busy_timeout, so 3 attempts ≈ 15 s server-side: an op landing in
#: a throttled full-recompute hold (~8 s at 10 k) succeeds on the second
#: attempt, inside the harness 10 s op budget.
HTTP_BUSY_ATTEMPTS = 3
HTTP_BUSY_BASE_S = 0.1
HTTP_RETRY_AFTER_S = 5
#: Reader-build attempts (reader DDL races worker writes; the warm itself
#: is read-only, so retries only replay a cheap open).
READER_BUSY_ATTEMPTS = 5
READER_BUSY_BASE_S = 0.2
#: S4 seed attempts (each attempt re-reads + re-encodes; other errors fail
#: fast with the existing honest label).
SEED_BUSY_ATTEMPTS = 3
SEED_BUSY_BASE_S = 0.5

#: Minimum seconds between served-cache disk saves (G2 cycle 2 RSS
#: discipline). Serializing the 13+ MB cache JSON on every catchup pass
#: (~1/s under mutations) pins a ~35 MB transient continuously for a
#: warm-start optimization. The in-memory cache stays authoritative, so a
#: restart re-encodes at most one throttle window of novel content
#: (bounded, correct, fast). Saves under ``SERVED_CACHE_SAVE_SMALL_N``
#: entries always go through (test-scale files serialize in milliseconds).
SERVED_CACHE_SAVE_MIN_S = 120.0
SERVED_CACHE_SAVE_SMALL_N = 1000

#: Bound on concurrent request threads (G2 cycle 2 RSS discipline). Every
#: anyio worker thread permanently retains a thread-local ReaderView plus
#: its pinned vector shell (~5-11 MB at 5 k); the default pool (40) lets a
#: long run accumulate 150 MB+ of idle-thread pins (measured: worker+S4
#: alone sit at ~148 MB, clients add the rest). 12 covers the G2 workload
#: (4 query + 1 mut + sampler/status ≈ 8 concurrent) with headroom; excess
#: requests queue inside anyio (never fail). Worker/S4 threads are plain
#: threading.Thread and are unaffected. Best-effort: unknown runtimes keep
#: the default pool.
ANYIO_THREAD_LIMIT = 12


def _cap_request_threads(n: int = ANYIO_THREAD_LIMIT) -> None:
    """Cap the anyio worker-thread pool (bounds reader retention)."""
    try:
        from anyio.to_thread import current_default_thread_limiter
        limiter = current_default_thread_limiter()
        if limiter.total_tokens > n:
            limiter.total_tokens = n
    except Exception:
        pass


def _is_busy_error(exc: BaseException) -> bool:
    """True for transient SQLite lock contention (SQLITE_BUSY / locked)."""
    return (isinstance(exc, sqlite3.OperationalError)
            and ("locked" in str(exc).lower() or "busy" in str(exc).lower()))


def _busy_503(op: str) -> HTTPException:
    """Contention exhaustion: retryable 503, never a 500."""
    return HTTPException(
        status_code=503, detail=f"{op}: database busy; retry",
        headers={"Retry-After": str(HTTP_RETRY_AFTER_S)})


def _retry_write(db: Database, fn, *, op: str,
                 attempts: int = HTTP_BUSY_ATTEMPTS,
                 base_s: float = HTTP_BUSY_BASE_S):
    """Run idempotent write ``fn`` with bounded busy retry.

    Rolls back before each retry (a busy commit can leave the txn open);
    re-raises the last busy error when attempts exhaust (callers convert
    to 503). Non-busy errors propagate immediately. Only idempotent
    sections may use this (publish/retract/facet-ensure/generation
    publish); the non-idempotent facet fold-out relies on the worker's
    sub-second holds fitting inside the 5 s busy_timeout instead.
    """
    err: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:
            if not _is_busy_error(exc):
                raise
            err = exc
            try:
                db.conn.rollback()
            except Exception:
                pass
            if i + 1 < attempts:
                time.sleep(base_s * (2 ** i))
    assert err is not None
    raise err

#: Name of the persisted embedding cache inside the served index dir.
SERVED_CACHE_NAME = "s4-cache.json"


def served_persistent_enabled_from_env() -> bool:
    return os.environ.get(SERVED_PERSISTENT_ENV_VAR, "") == "1"


def served_index_dir_from_env(db_path: str | Path) -> Path:
    override = os.environ.get(SERVED_INDEX_DIR_ENV_VAR, "")
    if override:
        return Path(override)
    p = Path(db_path)
    return p.parent / (p.stem + ".served")


def served_disk_budget_from_env() -> int | None:
    raw = os.environ.get(SERVED_DISK_BUDGET_ENV_VAR, "")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def queue_lag_bound_from_env(
    default: float = S6_QUEUE_LAG_BOUND_S,
) -> float:
    try:
        return max(0.0, float(os.environ.get(QUEUE_LAG_BOUND_ENV_VAR,
                                             str(default))))
    except ValueError:
        return default


class ServedRuntime:
    """Process-wide S4 serving state for one app (S6 cutover).

    Holds the shared :class:`ServedVectors` snapshot plus a SINGLE
    background maintenance thread (seed / catch-up jobs, coalesced).
    G1 immutable serving: maintenance NEVER mutates the published
    snapshot — it builds a thread-private clone and publishes with one
    atomic pointer swap under ``lock``. Request paths read the pointer
    under the same momentary lock and retain that immutable snapshot for
    the whole request, so queries serve continuously during maintenance
    and NEVER detach for it. The worker thread is a daemon: a stuck build
    can delay, never hang, shutdown (the join is bounded).
    """

    def __init__(self, db_path: str | Path, index_dir: str | Path) -> None:
        self.db_path = Path(db_path)
        self.index_dir = Path(index_dir)
        self.lock = threading.Lock()
        self.served: Any = None  # ServedVectors snapshot (open-only)
        self.open_note = "not opened"
        # Single background thread (jobs coalesced; never duplicated).
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._jobs: Any = None  # queue.Queue[str], created lazily
        self._job_pending = False
        self._repeat_wanted = False
        self._busy = False
        self._closing = False
        self._seed_state = "idle"  # idle | running | done | failed
        self._seed_receipt: dict | None = None
        self._seed_error: str | None = None
        self._catchup_state = "idle"  # idle | running | done | failed
        self._catchup_receipt: dict | None = None
        self._catchup_error: str | None = None
        # F2 incremental catch-up: change-log watermark + served-id cache.
        # Both None until one full-diff pass syncs them; the watermark then
        # advances per pass (O(delta)). Sound under the single-writer rule
        # (all atom writes via write_lock): log seq order matches commit
        # order, so no committed change can hide below the watermark.
        self._catchup_seq: int | None = None
        self._served_id_cache: set[int] | None = None
        self._last_cache_save: float = 0.0

    # -- snapshot access (request paths; never blocks on maintenance) --

    def snapshot(self) -> Any:
        with self.lock:
            return self.served

    def served_atom_count(self) -> int | None:
        """Live served atom count (base + overlay − tombstones), or None.

        Brief pointer access: computed under the lock from the published
        snapshot without starting maintenance or waiting on it. Converged
        serving covers every live atom; a one-shot seed receipt does not.
        """
        with self.lock:
            snap = self.served
            if snap is None or not snap.servable:
                return None
            return len(self._served_ids_locked(snap))

    def maintenance_running(self) -> bool:
        with self.lock:
            return self._busy

    def seed_status(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self._seed_state, "receipt": self._seed_receipt,
                    "error": self._seed_error}

    def catchup_status(self) -> dict[str, Any]:
        with self.lock:
            return {"state": self._catchup_state,
                    "receipt": self._catchup_receipt,
                    "error": self._catchup_error}

    def thread_alive(self) -> bool:
        with self.lock:
            return self._thread is not None and self._thread.is_alive()

    def _load_cache(self) -> Any:
        from ..router.vector_store import EmbeddingCache

        path = self.index_dir / SERVED_CACHE_NAME
        try:
            return EmbeddingCache.load(path)
        except (OSError, ValueError):
            return EmbeddingCache()

    def _save_cache_throttled(self, cache: Any) -> None:
        """Persist the embedding cache (large saves throttled off the hot path).

        Caches under ``SERVED_CACHE_SAVE_SMALL_N`` entries save immediately
        (milliseconds); larger ones at most every ``SERVED_CACHE_SAVE_MIN_S``
        so a 13+ MB serialization never overlaps the drain/seed peak.
        Never raises (a failed save keeps the previous file; the in-memory
        cache stays authoritative either way).
        """
        try:
            entries = int(cache.stats().get("entries", 0))
        except Exception:
            entries = 0
        now = time.monotonic()
        if (entries >= SERVED_CACHE_SAVE_SMALL_N
                and now - self._last_cache_save < SERVED_CACHE_SAVE_MIN_S):
            return
        try:
            cache.save(self.index_dir / SERVED_CACHE_NAME)
        except (OSError, ValueError):
            pass
        else:
            self._last_cache_save = now

    def _open_locked(self) -> Any:
        """Open the CURRENT published base. Caller holds ``lock``.

        Open-only: never encodes, never writes (a missing base yields an
        honest unservable snapshot, never a build).
        """
        from ..router.vector_store import ServedVectors

        try:
            self.served = ServedVectors.open(
                self.index_dir, cache=self._load_cache())
            self.open_note = self.served.note
        except (OSError, ValueError) as exc:
            self.served = None
            self.open_note = f"open failed: {exc}"
        return self.served

    def _current_gen_locked(self) -> str | None:
        """Generation id CURRENT points at (None when absent/unreadable)."""
        import json as _json

        try:
            manifest_name = (self.index_dir / "CURRENT").read_text(
                encoding="utf-8").strip()
            body = _json.loads((self.index_dir / manifest_name).read_text(
                encoding="utf-8"))
            return str(body.get("generation_id", ""))
        except (OSError, ValueError, KeyError):
            return None

    def _reopen_if_current_moved_locked(self, snap: Any) -> Any:
        current = self._current_gen_locked()
        if current is not None and current != snap.generation_id:
            return self._open_locked()
        return snap

    # -- background scheduling (non-blocking, idempotent, coalesced) --

    def request_maintenance(self) -> bool:
        """Enqueue one catch-up job (coalesced). Never blocks, never raises.

        Returns True when a job is (now) pending or running. The worker
        escalates to a full background seed when no base is servable.
        """
        import queue as _queue

        with self.lock:
            if self._closing:
                return False
            if self._jobs is None:
                self._jobs = _queue.Queue()
            if self._job_pending or self._busy:
                # Coalesced but not lost: the worker chains one more pass
                # after the in-flight job so post-seed edits converge.
                self._repeat_wanted = True
                return True
            self._job_pending = True
            self._jobs.put("catchup")
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._main, name="s6-served-maintenance",
                    daemon=True)
                self._thread.start()
            return True

    def _main(self) -> None:
        assert self._jobs is not None
        import queue as _queue

        while not self._stop.is_set():
            try:
                job = self._jobs.get(timeout=0.2)
            except _queue.Empty:
                continue
            try:
                with self.lock:
                    self._job_pending = False
                    self._busy = True
                if job == "catchup":
                    self._run_catchup()
            except Exception as exc:  # supervisor never dies on one bad job
                with self.lock:
                    self._catchup_state = "failed"
                    self._catchup_error = str(exc)[:300]
            finally:
                with self.lock:
                    self._busy = False
                    repeat = self._repeat_wanted and not self._stop.is_set()
                    if repeat:
                        self._repeat_wanted = False
                        self._job_pending = True
                if repeat:
                    assert self._jobs is not None
                    self._jobs.put("catchup")
                try:
                    self._jobs.task_done()
                except ValueError:
                    pass

    def _run_catchup(self) -> None:
        """Diff live atoms vs the served view; overlay only changed content.

        G1 immutable serving: the published snapshot is NEVER mutated here.
        The pass clones it, applies the overlay batch + optional compaction
        to the thread-private clone (all encodes through the shared content
        cache, so unchanged content never re-encodes), then publishes with
        one atomic pointer swap. Readers retain the previous snapshot for
        their whole request; Python reclaims it once released.
        F2: change detection is incremental (change-log watermark, O(delta))
        once one full-diff pass has synced the served-id cache; prune gaps
        or a cold cache fall back to the exact full id-diff. Escalates to
        one background seed when no base is servable (or the drift exceeds
        the bounded overlay); compacts when the overlay breaches its bound.
        """
        with self.lock:
            self._catchup_state = "running"
            self._catchup_error = None
        db = Database(self.db_path, check_same_thread=False)
        try:
            with self.lock:
                snap = self.served
                if snap is None or not snap.servable:
                    snap = self._open_locked()
            if snap is None or not snap.servable:
                live = db.conn.execute("SELECT COUNT(*) FROM atom").fetchone()
                if not live or int(live[0]) == 0:
                    with self.lock:
                        self._catchup_state = "done"
                        self._catchup_receipt = {"empty": True,
                                                 "escalated": None}
                    return
                self._run_seed(db)
                with self.lock:
                    seed_receipt = dict(self._seed_receipt or {})
                    seed_receipt["escalated"] = "seed"
                    self._catchup_state = "done"
                    self._catchup_receipt = seed_receipt
                return
            if self._served_id_cache is not None and self._catchup_seq is not None:
                if self._catchup_incremental(db, snap):
                    return
                # Gap or log failure: fall through to the exact full diff.
            self._catchup_full_diff(db, snap)
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _catchup_incremental(self, db: Database, snap: Any) -> bool:
        """One watermark-driven pass. True when consumed (or clean no-op).

        Returns False when the log window was pruned past the watermark or
        the log is unreadable, in which case the caller runs the exact
        full-diff pass instead. Never raises on data errors (fail-closed
        to full diff); apply/compact errors fail the pass like full diff.
        """
        from ..router.vector_store import S4_DELTA_BOUND

        assert self._served_id_cache is not None and self._catchup_seq is not None
        try:
            floor = db.conn.execute(
                "SELECT COALESCE(MIN(seq), 0) FROM atom_delta").fetchone()
            floor_seq = int(floor[0]) if floor else 0
            if floor_seq and self._catchup_seq < floor_seq - 1:
                return False
            ops: dict[int, tuple[str, str | None]] = {}
            high = self._catchup_seq
            cur = db.conn.execute(
                "SELECT seq, atom_id, op, text FROM atom_delta WHERE seq > ?"
                " ORDER BY seq", (self._catchup_seq,))
            while True:
                batch = cur.fetchmany(20000)
                if not batch:
                    break
                for seq, atom_id, op, text in batch:
                    high = max(high, int(seq))
                    ops[int(atom_id)] = (str(op),
                                         str(text) if text is not None else None)
        except Exception:
            return False
        if not ops:
            with self.lock:
                self._catchup_state = "done"
                self._catchup_receipt = {"new": 0, "dead": 0, "encoded": 0,
                                         "delta_size": snap.delta_size,
                                         "compacted": False, "warmed": True,
                                         "via": "incremental"}
            return True
        cache = self._served_id_cache
        dead = sorted(aid for aid, (op, _t) in ops.items()
                      if op == "delete" and aid in cache)
        upserts: list[tuple[int, str]] = []
        for aid, (op, text) in ops.items():
            if op == "delete":
                continue
            if text is None:
                row = db.conn.execute(
                    "SELECT text FROM atom WHERE id = ?", (aid,)).fetchone()
                if row is None:
                    if aid in cache and aid not in dead:
                        dead.append(aid)
                    continue
                text = str(row[0])
            upserts.append((aid, text))
        if len(upserts) + len(dead) > S4_DELTA_BOUND:
            self._run_seed(db)
            with self.lock:
                self._catchup_state = "done"
                self._catchup_receipt = {
                    "escalated": "seed",
                    "reason": "drift exceeds bounded overlay",
                    "new": len(upserts), "dead": len(dead),
                    "bound": S4_DELTA_BOUND, "via": "incremental"}
            return True
        return self._catchup_apply(db, snap, upserts, sorted(dead),
                                   via="incremental", watermark=high)

    def _catchup_full_diff(self, db: Database, snap: Any) -> None:
        """Exact full id-diff pass; (re)syncs the incremental cache."""
        from ..router.vector_store import S4_DELTA_BOUND

        try:
            mark = db.conn.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM atom_delta").fetchone()
            watermark: int | None = int(mark[0]) if mark else 0
        except Exception:
            watermark = None
        live_rows = db.conn.execute(
            "SELECT id FROM atom ORDER BY id").fetchall()
        live_ids = {int(r[0]) for r in live_rows}
        served_ids = self._served_ids_locked(snap)
        dead = sorted(served_ids - live_ids)
        new = sorted(live_ids - served_ids)
        if len(new) + len(dead) > S4_DELTA_BOUND:
            self._run_seed(db)
            with self.lock:
                self._catchup_state = "done"
                self._catchup_receipt = {
                    "escalated": "seed",
                    "reason": "drift exceeds bounded overlay",
                    "new": len(new), "dead": len(dead),
                    "bound": S4_DELTA_BOUND, "via": "full-diff"}
            return
        upserts: list[tuple[int, str]] = []
        for chunk in _chunked(new, 500):
            if not chunk:
                continue
            marks = ",".join("?" for _ in chunk)
            try:
                rows = db.conn.execute(
                    f"SELECT id, text FROM atom WHERE id IN ({marks})",
                    chunk).fetchall()
            except Exception:
                continue
            upserts.extend((int(r[0]), str(r[1])) for r in rows)
        if self._catchup_apply(db, snap, upserts, dead, via="full-diff",
                               watermark=watermark):
            self._served_id_cache = set(live_ids)
            self._catchup_seq = watermark

    def _catchup_apply(self, db: Database, snap: Any,
                       upserts: list[tuple[int, str]], dead: list[int],
                       *, via: str, watermark: int | None) -> bool:
        """Clone-apply-compact-warm-swap one batch. False only on failure."""
        # Thread-private working copy; the published snap stays immutable.
        work = snap.clone()
        try:
            applied = work.apply_batch(upserts, dead)
        except (OSError, ValueError, RuntimeError) as exc:
            with self.lock:
                self._catchup_state = "failed"
                self._catchup_error = str(exc)[:300]
            return False
        filing_gen = _filing_active_id(db.conn)
        receipt = None
        if work.needs_compaction() and filing_gen is not None:
            try:
                receipt = work.publish(
                    db.conn, filing_gen,
                    watermark=filing_watermark(db.conn))
            except (OSError, ValueError, RuntimeError) as exc:
                with self.lock:
                    self._catchup_state = "failed"
                    self._catchup_error = str(exc)[:300]
                return False
        if applied["upserts"] == 0 and applied["deletes"] == 0 \
                and receipt is None:
            # Effective no-op: keep the live warm template (no cold swap),
            # still advance the incremental state on success below.
            if via == "incremental" and watermark is not None:
                self._catchup_seq = watermark
            with self.lock:
                self._catchup_state = "done"
                self._catchup_receipt = {"new": len(upserts),
                                         "dead": len(dead),
                                         "encoded": applied["encoded"],
                                         "delta_size": snap.delta_size,
                                         "compacted": False, "warmed": True,
                                         "via": via}
            return True
        self._save_cache_throttled(work.cache)
        try:
            # F1: warm the attach template on the background thread so
            # the first query after the swap gets a cache hit instead
            # of training KMeans on the HTTP path. Best-effort: a warm
            # failure still swaps (queries materialize lazily).
            work.materialize()
            warmed = True
        except Exception:
            warmed = False
        with self.lock:  # atomic pointer swap; old snap reclaimed on release
            self.served = work
            self.open_note = work.note
            self._catchup_state = "done"
            self._catchup_receipt = {"new": len(upserts), "dead": len(dead),
                                     "encoded": applied["encoded"],
                                     "delta_size": work.delta_size,
                                     "compacted": receipt is not None,
                                     "warmed": warmed, "via": via}
        if via == "incremental" and watermark is not None:
            assert self._served_id_cache is not None
            self._served_id_cache.difference_update(dead)
            self._served_id_cache.update(aid for aid, _t in upserts)
            self._catchup_seq = watermark
        return True

    def _served_ids_locked(self, snap: Any) -> set[int]:
        try:
            return set(snap.served_ids())
        except (AttributeError, TypeError, ValueError):
            return set()

    def _run_seed(self, db: Database) -> None:
        """One explicit background build (transitional repair). Own handle."""
        from ..router.vector_store import ServedVectors

        with self.lock:
            self._seed_state = "running"
            self._seed_error = None
        try:
            filing_gen = _filing_active_id(db.conn)
            if filing_gen is None:
                raise RuntimeError("no active filing generation to seed at")
            fresh = ServedVectors(self.index_dir, cache=self._load_cache())
            # Bounded busy retry around the one-shot build (G2 cycle 2):
            # the seed's closing generation-row write can race worker
            # holds; other errors fail fast with the honest label below.
            receipt = None
            for attempt in range(SEED_BUSY_ATTEMPTS):
                try:
                    receipt = fresh.seed_from_db(
                        db.conn, filing_gen,
                        watermark=filing_watermark(db.conn))
                    break
                except Exception as exc:
                    if (not _is_busy_error(exc)
                            or attempt + 1 >= SEED_BUSY_ATTEMPTS):
                        raise
                    try:
                        db.conn.rollback()
                    except Exception:
                        pass
                    time.sleep(SEED_BUSY_BASE_S * (2 ** attempt))
            assert receipt is not None
            self._save_cache_throttled(fresh.cache)
            try:
                # F1: warm the attach template before publishing (best
                # effort; queries materialize lazily on warm failure).
                fresh.materialize()
                warmed = True
            except Exception:
                warmed = False
            with self.lock:
                self.served = fresh
                self.open_note = fresh.note
                self._seed_state = "done"
                self._seed_receipt = {"atoms": receipt.get("atoms"),
                                      "generation_id": filing_gen,
                                      "cache": receipt.get("cache"),
                                      "warmed": warmed}
                # F2: a fresh base invalidates the incremental served-set;
                # the next pass re-syncs via one exact full diff.
                self._catchup_seq = None
                self._served_id_cache = None
        except Exception as exc:
            with self.lock:
                self._seed_state = "failed"
                self._seed_error = str(exc)[:300]
            raise

    # -- shutdown (bounded; a stuck build delays, never hangs) --

    def begin_close(self) -> None:
        with self.lock:
            self._closing = True

    def stop(self, *, timeout_s: float = SERVED_SHUTDOWN_JOIN_S) -> dict:
        """Signal the maintenance thread and wait (bounded) for it."""
        self.begin_close()
        with self.lock:
            thread = self._thread
        self._stop.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_s)
            alive = thread.is_alive()
        else:
            alive = False
        with self.lock:
            self._thread = None if not alive else self._thread
            self._busy = False
        return {"thread_alive": alive, "timeout_s": timeout_s}


def _chunked(items: list[int], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _filing_active_id(conn) -> str | None:
    try:
        active = gen_store.get_active(conn, FILING_GENERATION_KIND)
    except Exception:
        return None
    return str(active["generation_id"]) if active else None


def served_status_block(rt: ServedRuntime, conn) -> dict[str, Any]:
    """Additive ``/api/status`` served-persistent block (S6, gated).

    Reports the open-only snapshot state, background seed/catch-up
    receipts, the disk-budget verdict (old/new generations + WAL), and
    the measured queue lag against its bound. Read-only: never starts
    maintenance, never waits on it.
    """
    snap = rt.snapshot()
    servable = bool(snap is not None and snap.servable)
    disk = check_disk_budget(
        rt.index_dir, rt.db_path, served=snap if servable else None,
        budget_bytes=served_disk_budget_from_env())
    lag_bound = queue_lag_bound_from_env()
    try:
        lag_s = outbox_store.queue_stats(conn).get("queue_lag_s")
    except Exception:
        lag_s = None
    seed = rt.seed_status()
    catchup = rt.catchup_status()
    try:
        content_version = (snap.describe().get("content_version")
                           if servable else None)
    except (AttributeError, TypeError):
        content_version = None
    return {
        "enabled": True,
        "index_dir": str(rt.index_dir),
        "servable": servable,
        "note": (snap.note if snap is not None else rt.open_note),
        "generation_id": (snap.generation_id if snap is not None else None),
        "delta_size": (snap.delta_size if servable else None),
        "served_atoms": rt.served_atom_count(),
        "content_version": content_version,
        "maintenance_running": rt.maintenance_running(),
        "thread_alive": rt.thread_alive(),
        "seed": seed,
        "catchup": catchup,
        "disk": disk,
        "disk_within_budget": disk["within_budget"],
        "lag": {"queue_lag_s": lag_s, "bound_s": lag_bound,
                "within_bound": lag_s is None or lag_s <= lag_bound},
    }


def served_after_write(state: State) -> None:
    """Post-commit hook: schedule background served catch-up (S6, gated).

    Non-blocking and exception-free: writers only enqueue a coalesced job;
    all encoding/publication happens on the single maintenance thread, so
    write latency never includes vector work and freshness never awaits it.
    No-op unless persistent serving was enabled at startup.
    """
    try:
        rt = state.served_rt
    except AttributeError:
        return
    if rt is None:
        return
    try:
        rt.request_maintenance()
    except Exception:
        pass


def check_disk_budget(
    index_dir: str | Path,
    db_path: str | Path | None,
    served: Any = None,
    *,
    budget_bytes: int | None = None,
    min_headroom_ratio: float = S6_DISK_HEADROOM_MIN_RATIO,
) -> dict[str, Any]:
    """Old/new generations + WAL against the disk budget (S6 envelope check).

    ``served`` (a :class:`ServedVectors`, when a base is open) contributes
    the per-generation accounting; DB/WAL bytes are always measured from
    ``db_path``. Passes iff the footprint fits ``budget_bytes`` (when set)
    AND host headroom covers ``min_headroom_ratio`` × footprint.
    """
    import shutil as _shutil

    per_generation: dict[str, int] = {}
    index_bytes = 0
    if served is not None:
        try:
            usage = served.disk_usage(db_path)
            per_generation = dict(usage.get("per_generation", {}))
            index_bytes = int(usage.get("index_bytes", 0))
        except (OSError, ValueError):
            pass
    else:
        try:
            index_bytes = sum(
                p.stat().st_size for p in Path(index_dir).rglob("*")
                if p.is_file())
        except OSError:
            index_bytes = 0
    db_files: dict[str, int] = {}
    if db_path is not None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(db_path) + suffix)
            try:
                db_files[p.name] = p.stat().st_size
            except OSError:
                continue
    try:
        free = _shutil.disk_usage(str(index_dir)).free
    except OSError:
        try:
            free = _shutil.disk_usage(
                str(Path(db_path).parent if db_path else ".")).free
        except OSError:
            free = -1
    footprint = index_bytes + sum(db_files.values())
    ratio = (free / footprint) if footprint > 0 and free >= 0 else None
    fits_budget = budget_bytes is None or footprint <= budget_bytes
    fits_headroom = ratio is None or ratio >= min_headroom_ratio
    return {"per_generation": per_generation, "index_bytes": index_bytes,
            "db_files": db_files, "footprint_bytes": footprint,
            "host_free_bytes": free, "headroom_ratio": ratio,
            "budget_bytes": budget_bytes,
            "min_headroom_ratio": min_headroom_ratio,
            "within_budget": bool(fits_budget and fits_headroom)}


#: R3 shared warmed view: opt-out via ``TRACE_LITE_SHARED_WARM=0``
#: (default on: one pooled immutable warmed matrix per generation, read
#: by every thread, built by a single rebuilder on generation swap).
#: Disable only as a diagnostic control — per-thread private warms are
#: the pre-R3 behavior (G2 warm storm) and stay available for A/B.
SHARED_WARM_ENV_VAR = "TRACE_LITE_SHARED_WARM"

#: Bound on pooled-rebuild attempts per warm when the serving generation
#: keeps moving mid-build (each attempt converges via the O(delta) path;
#: past the bound the reader serves a correct private view instead).
SHARED_WARM_BUILD_ATTEMPTS = 3


def shared_warm_enabled_from_env() -> bool:
    return os.environ.get(SHARED_WARM_ENV_VAR, "1") == "1"


class State:
    db: Database | None = None
    taxonomy: Taxonomy | None = None
    engine: FilingEngine | None = None
    vault: Path | None = None
    watcher: Any = None
    worker: Any = None
    #: S6 persistent-serving runtime (None unless TRACE_LITE_SERVED_PERSISTENT=1
    #: at startup; when None the legacy query-triggered build path runs).
    served_rt: Any = None
    started_at: float = 0.0
    db_path: Path | None = None
    # Serializes ALL writer-connection use (ingest/sync/watcher/teardown).
    # Readers never take it.
    write_lock: threading.Lock | None = None
    readers: threading.local | None = None
    reader_registry: list[Database] | None = None
    registry_lock: threading.Lock | None = None
    #: R3 pooled warmed view: at most one immutable
    #: ``{"generation_id", "matrix", "ids", "pos", "idf", "digests",
    #: "delta_seq", "centroids"}`` snapshot, published under
    #: ``shared_lock`` by a single rebuilder per generation. Readers
    #: attach it by reference (zero encodes); pooled objects are never
    #: mutated in place (copy-on-write in ``FlatHybrid``), so a stale
    #: reader mid-request keeps serving its pinned snapshot safely while
    #: the pool advances — the same immutable-serving contract as G1.
    shared_lock: threading.Lock | None = None
    shared_hybrid: dict | None = None


def filing_watermark(conn) -> str:
    """Lexical watermark: live atom count + max rowid (monotonic-ish)."""
    count, max_id = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM atom"
    ).fetchone()
    return f"atoms={int(count)}:maxid={int(max_id)}"


def publish_filing_generation(conn, *, commit: bool = True) -> str:
    """Publish a new ``filing`` generation row covering the writer's commit.

    Writers call this with ``commit=False`` INSIDE their data transaction so
    the generation row commits atomically with the data it describes (ARCH
    §3): no snapshot ever contains data without its generation, so the
    generation-swap retry in :func:`route_pinned` is sound. Uses the R01
    generation helpers unchanged (no R01 semantic change): create-then-
    activate is atomic per helper; readers compare the pinned id against the
    active row to decide when to swap warmed views.

    Standalone calls (``commit=True``) retry transient busy with rollback
    (G2 cycle 2); a rolled-back generation id never escapes, so retries
    are side-effect free. ``commit=False`` never retries (the caller owns
    the txn and any half-written row).
    """
    err: Exception | None = None
    attempts = HTTP_BUSY_ATTEMPTS if commit else 1
    for i in range(attempts):
        try:
            gid = gen_store.create_generation(
                conn,
                kind=FILING_GENERATION_KIND,
                config_hash=FILING_CONFIG_HASH,
                watermark=filing_watermark(conn),
                commit=False,
            )
            gen_store.activate_generation(conn, gid, commit=False)
            if commit:
                conn.commit()
            return gid
        except Exception as exc:
            if not commit or not _is_busy_error(exc):
                raise
            err = exc
            try:
                conn.rollback()
            except Exception:
                pass
            if i + 1 < attempts:
                time.sleep(HTTP_BUSY_BASE_S * (2 ** i))
    assert err is not None
    raise err


def _register_reader(state: State, db: Database) -> None:
    assert state.registry_lock is not None and state.reader_registry is not None
    with state.registry_lock:
        state.reader_registry.append(db)


def _unregister_reader(state: State, db: Database) -> None:
    assert state.registry_lock is not None and state.reader_registry is not None
    with state.registry_lock:
        if db in state.reader_registry:
            state.reader_registry.remove(db)


def _live_centroids(engine: FilingEngine) -> dict[str, list[float]]:
    """Live facet blobs unpacked (read-only; ghosts never repaired here)."""
    out: dict[str, list[float]] = {}
    for fid, blob in engine.conn.execute(
        "SELECT f.facet_id, f.centroid_blob FROM facets f"
        " WHERE f.centroid_blob IS NOT NULL"
        " AND EXISTS (SELECT 1 FROM memberships m WHERE m.facet_id = f.facet_id)"
    ).fetchall():
        out[str(fid)] = unpack_vector(bytes(blob))
    return out


def _warm_reader_private(router: CascadeRouter,
                         engine: FilingEngine) -> dict[str, int]:
    """Per-thread private warm: the exact pre-R3 read-only reader warm.

    ``CascadeRouter.warm()`` issues ``UPDATE``/``COMMIT`` statements
    (stale-centroid clearing, per-facet ``refresh_centroid`` for NULL-blob
    facets). On a reader those writes contend with the writer's
    ``BEGIN IMMEDIATE`` transactions and stall on ``busy_timeout`` for
    seconds — the exact queue-behind-sync the packet forbids. The writer
    already maintains every blob incrementally (``assign_facets``/
    ``detach_atom`` persist per edit, ``clear_stale_centroids`` runs after
    every batch), so readers only need live blobs plus the warmed dense
    matrix + shared beam view (``FlatHybrid.warm`` is read-only: SELECTs +
    numpy). Runs when the shared pool is disabled or has no generation.
    """
    centroids = _live_centroids(engine)
    engine._centroids.update(centroids)
    vectors = router.hybrid.warm()
    matrix, pos, idf = router.hybrid.matrix_view()
    router.beam.set_vectors(matrix, pos, idf)
    router.warmed = True
    return {"centroids": len(centroids), "vectors": vectors}


def _attach_pooled(router: CascadeRouter, engine: FilingEngine,
                   pooled: dict) -> dict[str, int]:
    """Attach a pooled view by reference (zero encodes, zero copies).

    The dense arrays are attached read-only (numpy reads are thread-safe
    and the pooled objects are never mutated in place); centroid vectors
    are cheaply copied per reader so the pooled dict stays immutable.
    """
    router.hybrid.attach_shared(pooled["matrix"], pooled["ids"],
                                pooled["pos"], pooled["idf"],
                                pooled["digests"], pooled["delta_seq"])
    engine._centroids.update({k: list(v)
                              for k, v in pooled["centroids"].items()})
    router.beam.set_vectors(pooled["matrix"], pooled["pos"], pooled["idf"])
    router.warmed = True
    return {"centroids": len(pooled["centroids"]),
            "vectors": len(pooled["pos"])}


def _pooled_generation_id(engine: FilingEngine) -> str | None:
    try:
        active = gen_store.get_active(engine.conn, FILING_GENERATION_KIND)
    except Exception:
        return None
    return str(active["generation_id"]) if active else None


#: R3 pool telemetry (process-wide; read by ``/api/status``).
#: ``hits`` = readers attached by reference, ``builds`` = single-rebuilder
#: encodes, ``rekeys`` = generation advances with zero atom changes
#: (identical view re-labeled, no repack), ``fallbacks`` = correct
#: private warms under churn.
SHARED_WARM_STATS = {"hits": 0, "builds": 0, "rekeys": 0, "fallbacks": 0}


def shared_warm_status(state: State) -> dict[str, Any]:
    """Additive ``/api/status`` R3 pool block (read-only telemetry)."""
    pooled = state.shared_hybrid
    return {
        "enabled": shared_warm_enabled_from_env(),
        "pooled_generation_id": (pooled or {}).get("generation_id"),
        "pooled_vectors": len((pooled or {}).get("pos") or {}),
        "hits": SHARED_WARM_STATS["hits"],
        "builds": SHARED_WARM_STATS["builds"],
        "rekeys": SHARED_WARM_STATS["rekeys"],
        "fallbacks": SHARED_WARM_STATS["fallbacks"],
    }


def _warm_reader(state: State, router: CascadeRouter,
                 engine: FilingEngine) -> dict[str, int]:
    """Read-only reader warm (C6-3 + R3 pooled generation view).

    R3: exactly ONE immutable warmed view exists per generation,
    published under ``state.shared_lock`` by a single rebuilder; every
    reader attaches it by reference instead of re-encoding the corpus
    (pre-R3 each thread paid a full GIL-bound re-encode on first touch,
    serializing N threads into an N× storm that starved serving). The
    pooled view is built by the same ``FlatHybrid.warm()`` code, so
    vectors are bit-identical to a private warm; the rebuilder publishes
    only when the generation it built for is still active, and the pool
    advances by pointer swap while in-flight readers retain their pinned
    snapshot — synchronous visibility invalidation is preserved (a stale
    reader is detected by generation mismatch in :func:`get_reader` and
    re-attaches, never served stale-as-complete).
    """
    if not shared_warm_enabled_from_env() or state.shared_lock is None:
        return _warm_reader_private(router, engine)
    for _ in range(SHARED_WARM_BUILD_ATTEMPTS):
        gen_id = _pooled_generation_id(engine)
        if gen_id is None:
            return _warm_reader_private(router, engine)
        with state.shared_lock:
            pooled = state.shared_hybrid
            if pooled is not None and pooled["generation_id"] == gen_id:
                SHARED_WARM_STATS["hits"] += 1
                return _attach_pooled(router, engine, pooled)
            # Single rebuilder: this thread encodes while concurrent
            # readers block here briefly, then attach the published view
            # above — one encode per generation, never one per thread.
            # ``warm()`` is delta-aware (full only on first build) and
            # copy-on-write (a frozen attach thaws to private arrays, so
            # the previous pooled view is never mutated); the second call
            # cheaply converges commits that landed mid-build.
            prev_seq = router.hybrid._delta_seq
            had_matrix = router.hybrid._matrix is not None
            router.hybrid.warm()
            router.hybrid.warm()
            if _pooled_generation_id(engine) != gen_id:
                # Generation moved mid-build: loop — the hybrid above
                # already advanced, so the next pass converges via delta.
                continue
            last = router.hybrid.last_warm
            if (pooled is not None and had_matrix
                    and not last.get("full", False)
                    and last.get("encodes", 0) == 0
                    and last.get("removed", 0) == 0
                    and router.hybrid._delta_seq == prev_seq):
                # No atom changes under this generation (worker-only
                # publication: projections moved, atoms/facets identical —
                # every facet write accompanies an atom delta row, so an
                # empty delta proves both unchanged). Re-key the identical
                # pooled arrays instead of repacking them.
                SHARED_WARM_STATS["rekeys"] += 1
                state.shared_hybrid = dict(pooled, generation_id=gen_id)
                return _attach_pooled(router, engine, state.shared_hybrid)
            snap = router.hybrid.shared_snapshot()
            centroids = _live_centroids(engine)
            SHARED_WARM_STATS["builds"] += 1
            router.hybrid.freeze_shared()
            state.shared_hybrid = {
                "generation_id": gen_id,
                "matrix": snap[0], "ids": snap[1], "pos": snap[2],
                "idf": snap[3], "digests": snap[4], "delta_seq": snap[5],
                "centroids": centroids,
            }
            engine._centroids.update({k: list(v)
                                      for k, v in centroids.items()})
            router.beam.set_vectors(snap[0], snap[2], snap[3])
            router.warmed = True
            return {"centroids": len(centroids), "vectors": len(snap[2])}
    # Churn fallback (bounded): the generation moved under every attempt.
    # Serve a correct private view without poisoning the pool; the next
    # generation swap re-enters the pooled path.
    SHARED_WARM_STATS["fallbacks"] += 1
    return _warm_reader_private(router, engine)


def _build_reader(state: State) -> ReaderView:
    """Open a private WAL read stack and warm it (caller owns lifecycle).

    The open replays idempotent DDL, which takes the write lock briefly
    and can race worker holds: bounded busy retry with handle cleanup,
    then a retryable 503 (G2 cycle 2; never a 500 for contention). The
    warm itself is read-only, so retries only replay a cheap open.
    """
    assert state.db_path is not None
    err: Exception | None = None
    for i in range(READER_BUSY_ATTEMPTS):
        db: Database | None = None
        try:
            db = Database(state.db_path)  # one-instance-per-thread, WAL reader
            taxonomy = Taxonomy(db.conn)
            engine = FilingEngine(db.conn, taxonomy)
            engine.bind_database(db)
            router = CascadeRouter(db.conn, engine)
            _warm_reader(state, router, engine)
            active = gen_store.get_active(db.conn, FILING_GENERATION_KIND)
            view = ReaderView(
                db=db, taxonomy=taxonomy, engine=engine, router=router,
                generation_id=(str(active["generation_id"])
                               if active else None),
            )
            _register_reader(state, db)
            return view
        except Exception as exc:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
            if not _is_busy_error(exc):
                raise
            err = exc
            if i + 1 < READER_BUSY_ATTEMPTS:
                time.sleep(READER_BUSY_BASE_S * (2 ** i))
    assert err is not None
    raise _busy_503("reader open")


def _close_reader(state: State, view: ReaderView) -> None:
    _unregister_reader(state, view.db)
    try:
        view.db.close()
    except Exception:
        pass


def _refresh_reader(state: State, view: ReaderView) -> ReaderView:
    """In-place generation swap: re-warm the existing stack, keep its connection.

    A full rebuild (new connection + full DDL replay + re-warm) costs 2-3x a
    re-warm and, under threaded load, lands queries in long tails. Reuse is
    snapshot-sound: every statement reads the latest committed state, so
    clearing the facet/centroid caches and re-running the read-only warm
    yields exactly the post-publication view a fresh connection would build —
    with no DDL replay and no connection churn. Falls back to a full rebuild
    when the connection is unusable.
    """
    try:
        view.engine._centroids.clear()
        view.engine._accum.clear()
        view.engine._facet_paths = None
        view.engine._facet_paths_count = None
        view.engine._subtree_cache = {}
        _warm_reader(state, view.router, view.engine)
        active = gen_store.get_active(view.db.conn, FILING_GENERATION_KIND)
        view.generation_id = str(active["generation_id"]) if active else None
        return view
    except Exception:
        _close_reader(state, view)
        return _build_reader(state)


def get_reader(state: State) -> ReaderView:
    """Thread-local reader; refreshes (generation swap) when stale.

    Each thread reuses its own warmed stack across requests. When the
    writer has published a newer ``filing`` generation, the stack is
    refreshed in place and re-pinned so queries always read a coherent
    snapshot at a known generation — never a half-published index.
    """
    assert state.readers is not None
    view = getattr(state.readers, "view", None)
    if view is not None:
        try:
            active = gen_store.get_active(view.db.conn, FILING_GENERATION_KIND)
            active_id = str(active["generation_id"]) if active else None
        except Exception:
            active_id = None  # closed/recycled conn: rebuild below
            view = None
        if view is not None and active_id == view.generation_id:
            return view
        if view is not None:
            view = _refresh_reader(state, view)
            state.readers.view = view
            return view
    fresh = _build_reader(state)
    state.readers.view = fresh
    return fresh


def drop_reader(state: State, view: ReaderView) -> None:
    """Discard a thread-local reader stack (forces rebuild on next use)."""
    assert state.readers is not None
    if getattr(state.readers, "view", None) is view:
        state.readers.view = None
    _close_reader(state, view)


def _ensure_served_vector(view: ReaderView) -> None:
    """Pin the served R03 ANN view to the reader's filing generation.

    Read-only: rebuilds the thread-local router's in-memory VectorIndex/IVF
    from its own snapshot when the serving generation moved, and enables
    holon-guided expansion. An unavailable branch (empty/oversize corpus,
    build failure) detaches quietly so the named lexical fallbacks serve —
    never an error on the query path.
    """
    router = view.router
    router.holon_guided = True
    if view.generation_id is None:
        return
    if router.vector_generation_id == view.generation_id:
        return
    try:
        router.refresh_served_vector(view.generation_id)
    except Exception as exc:
        router.detach_vector(f"vector branch unavailable: build failed ({exc})")


def _ensure_served_persistent_view(state: State, view: ReaderView) -> None:
    """Pin the S4 persistent view on the reader (S6 cutover, gated).

    Drop-in replacement for :func:`_ensure_served_vector` when persistent
    serving is enabled: attaches the published base + bounded delta through
    the existing ANN branch (INT-2 filtering semantics reused unchanged).
    G1 non-blocking contract: the active snapshot pointer is read under a
    momentary lock (< 1ms) and the retained IMMUTABLE snapshot serves the
    whole request — maintenance NEVER detaches the branch and a busy lock
    NEVER detaches it either (the last published snapshot still serves).
    Only a genuinely absent/unservable base detaches with an honest note
    (legitimate cold-start fallback; first query never triggers a build).
    """
    from ..router import ensure_served_persistent

    router = view.router
    router.holon_guided = True
    if view.generation_id is None:
        return
    if router.vector_generation_id == view.generation_id:
        return
    rt = state.served_rt
    if rt is None:  # pragma: no cover - caller branches on this first
        return
    snap = None
    opened_under_lock = False
    if rt.lock.acquire(blocking=False):
        try:
            snap = rt.served
            if snap is None or not snap.servable:
                snap = rt._open_locked()
                opened_under_lock = True
            else:
                snap = rt._reopen_if_current_moved_locked(snap)
        finally:
            rt.lock.release()
    else:
        # Lock momentarily held by a pointer swap: read the pointer without
        # blocking (atomic under the GIL) and serve the retained immutable
        # snapshot — never detach for maintenance.
        snap = rt.served
    if snap is None or not snap.servable:
        if not opened_under_lock:
            # Cold path only: one brief blocking open attempt (maintenance
            # holds the lock solely for pointer-speed swaps now).
            with rt.lock:
                snap = rt.served
                if snap is None or not snap.servable:
                    snap = rt._open_locked()
                else:
                    snap = rt._reopen_if_current_moved_locked(snap)
        if snap is None or not snap.servable:
            note = snap.note if snap is not None else rt.open_note
            router.detach_vector(f"vector branch unavailable: {note}")
            return
    # Attach OUTSIDE the snapshot lock on the retained immutable snapshot:
    # a first-version materialize may merge + train without blocking swaps.
    try:
        ensure_served_persistent(router, snap, str(view.generation_id))
    except Exception as exc:
        router.detach_vector(
            f"vector branch unavailable: persistent attach failed ({exc})")


def route_pinned(state: State, text: str, limit: int, mode: str):
    """Route a query with generation-swap retry (ARCH §3).

    A reader warmed at generation G can race a writer publishing G+1: an
    atom id resolved from the warmed dense view may be gone from the live
    table (Tier-3 fusion fill ``KeyError``). When — and only when — a newer
    active generation explains the miss, the stale stack is dropped and the
    query retries on the fresh generation. A miss with no publication
    behind it is re-raised: it is a real bug, not a race. Bounded fixture
    bound: 4 attempts, then an honest 503 (never stale-as-complete).

    Served retrieval (INT-2): the pinned reader also carries the R03 ANN
    branch (generation-pinned VectorIndex/IVF with honest route labels) and
    holon-guided expansion; the beam/hybrid/lexical branches stay available
    as named fallbacks. S6 cutover: when ``state.served_rt`` is set
    (``TRACE_LITE_SERVED_PERSISTENT=1`` at startup) the branch is pinned
    from the S4 published base + bounded delta via
    :func:`_ensure_served_persistent_view` (never a query-path build);
    otherwise the legacy query-triggered build runs unchanged.
    """
    attempts = 0
    while True:
        attempts += 1
        view = get_reader(state)
        pinned = view.generation_id
        if state.served_rt is not None:
            _ensure_served_persistent_view(state, view)
        else:
            _ensure_served_vector(view)
        try:
            return view, view.router.route(text, limit=limit, mode=mode)
        except KeyError:
            active = gen_store.get_active(view.db.conn, FILING_GENERATION_KIND)
            active_id = str(active["generation_id"]) if active else None
            if active_id == pinned:
                raise
            if attempts >= 4:
                raise HTTPException(
                    status_code=503,
                    detail="index generation changed during query; retry",
                )
            drop_reader(state, view)


@contextmanager
def writer(state: State):
    """Serialize writer-connection use. Readers never enter here."""
    assert state.write_lock is not None
    assert state.db is not None and state.taxonomy is not None and state.engine is not None
    with state.write_lock:
        yield (state.db, state.taxonomy, state.engine)


def _ensure_facet(taxonomy: Taxonomy, dimension: str, name: str,
                  roots: dict[str, str]) -> str:
    root_id = roots.get(dimension)
    if root_id is None:
        # Reuse the stable dimension root: a fresh root per call duplicates
        # the facet subtree on every write and fragments facet membership.
        row = taxonomy.conn.execute(
            "SELECT facet_id FROM facets WHERE dimension = ? AND name = ?"
            " AND parent_facet_id IS NULL ORDER BY rowid LIMIT 1",
            (dimension, dimension),
        ).fetchone()
        if row is None:
            root_id = taxonomy.create_facet(dimension, dimension)
        else:
            root_id = row[0]
        roots[dimension] = root_id
    for child in taxonomy.children(root_id):
        if child.name == name:
            return child.facet_id
    return taxonomy.create_facet(dimension, name, parent_id=root_id)


def _prepare_note(db: Database, taxonomy: Taxonomy,
                  document_name: str, raw: str):
    """Parse a note + ensure its facets (autocommit phase, no atom writes).

    Facet creation commits early (additive, never deletes — always safe) and
    therefore must run OUTSIDE any caller-held write transaction. Returns the
    parsed note, facet ids, body, and raw payload for the INT-1 write
    path (R01 revision + facet mapping + projection drain). Per-note
    pre-write snapshots (prior atoms/facets/revision) are taken by
    :func:`_snapshot_doc` immediately before each publish.
    """
    note = parse_note(document_name, raw)
    roots: dict[str, str] = {}
    facet_ids = []
    for dimension, names in facet_hints(note).items():
        for name in names:
            facet_ids.append(_ensure_facet(taxonomy, dimension, name, roots))
    return note, facet_ids, raw


def _snapshot_doc(db: Database, document_name: str
                ) -> tuple[list[int], list[str], str | None, dict[str, list[str]]]:
    """Pre-write snapshot: prior atom ids, their facets, current revision id,
    and per-facet prior member texts for incremental fold-out.

    Taken BEFORE ``source_service.publish``/``retract`` deletes the old atom
    rows, so facet accumulators can fold the removed members out exactly and
    dependent syntheses invalidated afterwards (dependency rows carry no FK
    and survive the atom delete; membership rows cascade).
    """
    old_rows = db.conn.execute(
        "SELECT id, text FROM atom WHERE doc_id = ?", (document_name,)
    ).fetchall()
    old_atom_ids = [int(r[0]) for r in old_rows]
    facet_texts: dict[str, list[str]] = {}
    for atom_id, text in old_rows:
        for fid in source_service.facets_of_atom(db.conn, int(atom_id)):
            facet_texts.setdefault(fid, []).append(str(text))
    old_facets = sorted(facet_texts)
    cur = source_service.get_current(db.conn, document_name)
    old_rev = str(cur["revision_id"]) if cur is not None else None
    return old_atom_ids, old_facets, old_rev, facet_texts


def _repair_facet_state(engine: FilingEngine,
                        facet_texts: dict[str, list[str]]) -> None:
    """Fold removed members out of facet accumulators (G1 incremental path).

    ``source_service.publish``/``retract`` removes superseded atom rows in
    its own writer transaction (with correct FTS deletes). Each affected
    facet folds exactly its removed member texts out of the running sum
    (O(removed × dim), no member re-scan on warm accumulators); a cold facet
    seeds once from post-delete truth and a stale fold falls back to one
    exact reseed inside ``remove_members``. Always persists, including the
    emptied-facet NULL branch.
    """
    for fid, texts in facet_texts.items():
        engine.remove_members(fid, texts)


def _invalidate_for_edit(engine: FilingEngine, old_revision_id: str | None,
                         old_atom_ids: list[int]) -> dict:
    """Mark dependent syntheses stale after a premise edit/delete (R06-F4).

    Requires cover the superseded revision (reaches prior extraction runs
    and their anchored assertions transitively) plus every removed atom id
    (reaches member-linked holons through surviving dependency rows). The
    pipeline-maintained corpus holon is excluded: its state was already
    refreshed synchronously by grouping, so it needs no repair. Non-holon
    derived rows are traversed but carry no status to flip.
    """
    requires: list[tuple[str, str]] = []
    if old_revision_id is not None:
        requires.append(("revision", old_revision_id))
    requires.extend(("atom", str(aid)) for aid in old_atom_ids)
    if not requires:
        return {"requires": [], "invalidated": [], "closure_size": 0,
                "steps": 0, "budget": 0}
    corpus = projection.find_corpus_holon(engine.conn)
    exclude = {corpus} if corpus is not None else frozenset()
    return engine.invalidate_dependents(requires, exclude=exclude)


def ingest_note(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
                document_name: str, raw: str, event_type: str = "vault.synced",
                *, publish: bool = True) -> int:
    """Idempotent single-note upsert with a decoupled fast ACK. Returns atom id.

    Per-note flow, all under the caller's ``write_lock`` (R06 writer
    serialization stays untouched):

    1. parse + ensure facets (autocommit, additive);
    2. snapshot prior atoms/facets/revision (+ member texts for fold-out);
    3. R01 ``source_service.publish``: one writer transaction inserting the
       immutable revision + lexical delta (atom/FTS) + event + durable
       outbox job. Same key + payload replays return the prior revision
       with NO new job and NO duplicated projections;
    4. incremental facet fold-out for removed members + facet mapping of the
       new atom (legacy query behavior preserved: same hints, same
       memberships);
    5. premise-edit invalidation (R06 ``invalidate_dependents``; corpus
       excluded) + one ``filing`` generation publish: visibility
       invalidation is synchronous (superseded content is immediately
       invisible), while semantic projection drains asynchronously on the
       background worker (generation-swap retry stays sound).

    G1: the write path NEVER drains the projection outbox. Semantic
    coverage converges on the worker; freshness reports honest pending
    until it does. ``publish`` controls the ``filing`` generation bump.
    Replays skip the bump (nothing changed).
    """
    note, facet_ids, payload = _retry_write(
        db, lambda: _prepare_note(db, taxonomy, document_name, raw),
        op="ingest prepare")
    old_atom_ids, _old_facets, _old_rev, facet_texts = _snapshot_doc(
        db, document_name)
    result = _retry_write(
        db,
        lambda: source_service.publish(
            db, document_name, payload, kind="note", locator=document_name,
            idempotency_key=source_service.idempotency_key_for(
                document_name, payload)),
        op="ingest publish")
    if not result.created:
        prior = source_service.atom_id_for_revision(db.conn, result.revision_id)
        return int(prior) if prior is not None else 0
    atom_id = source_service.atom_id_for_revision(db.conn, result.revision_id)
    assert atom_id is not None, "publish committed without a lexical atom"
    _repair_facet_state(engine, facet_texts)
    if facet_ids:
        engine.assign_facets(atom_id, facet_ids)
    db.insert_event(document_name, event_type,
                    {"path": document_name, "tags": note.tags,
                     "revision_id": result.revision_id})
    if result.supersedes is not None:
        _invalidate_for_edit(engine, result.supersedes, old_atom_ids)
    if publish:
        publish_filing_generation(db.conn)
    return atom_id


def remove_note(db: Database, engine: FilingEngine,
                document_name: str) -> int:
    """Retract a note: R01 revision history retained, lexical rows removed.

    Same pipeline shape as :func:`ingest_note`: snapshot, R01 ``retract``
    (atoms + FTS removed, current pointer nulled, retract job enqueued),
    incremental facet fold-out, dependent-synthesis invalidation over the
    removed atoms, one ``filing`` generation publish. Retraction is
    immediately visible (live rows + generation swap); the retract job
    drains asynchronously. Returns atoms removed (0 when the note is
    absent, matching legacy behavior).
    """
    old_atom_ids, _old_facets, old_rev, facet_texts = _snapshot_doc(
        db, document_name)
    if not old_atom_ids:
        return 0
    _retry_write(
        db,
        lambda: source_service.retract(
            db, document_name,
            idempotency_key=source_service.retract_key_for(
                document_name, old_rev)),
        op="retract")
    _repair_facet_state(engine, facet_texts)
    _invalidate_for_edit(engine, old_rev, old_atom_ids)
    publish_filing_generation(db.conn)
    return len(old_atom_ids)


def sync_notes(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
               vault: Path, paths: list[Path]) -> int:
    """Sync an explicit list of vault files (used by the background watcher).

    INT-1 per-source transaction contract (explicit per ARCH §3: batches
    choose all-or-per-source; this path chooses per-source): each note runs
    the :func:`ingest_note` pipeline stages 1–5 (parse, R01 publish with
    idempotent replay for unchanged files, facet fold-out + mapping, legacy
    event), then the batch shares ONE batched invalidation and a SINGLE
    ``filing`` generation publish — so readers rebuild once per sync and
    the generation-swap retry in :func:`route_pinned` stays sound. G1: no
    synchronous drain; projection jobs queue durably for the background
    worker. Unreadable files are skipped; any other error propagates after
    earlier notes committed (per-source, never a half-replaced single
    note). No-op batches (nothing synced) publish nothing. Returns files
    synced (replays count, as before).
    """
    prepared: list[tuple[str, tuple]] = []
    for path in paths:
        if not path.is_file() or path.suffix != ".md":
            continue
        try:
            rel = path.relative_to(vault).as_posix()
        except ValueError:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        prepared.append((rel, _retry_write(
            db, lambda r=rel, t=raw: _prepare_note(db, taxonomy, r, t),
            op="sync prepare")))
    if not prepared:
        return 0
    batched_requires: list[tuple[str, str]] = []
    any_created = False
    for rel, prep in prepared:
        note, facet_ids, payload = prep
        old_atom_ids, _old_facets, _old_rev, facet_texts = _snapshot_doc(db, rel)
        result = _retry_write(
            db,
            lambda r=rel, p=payload: source_service.publish(
                db, r, p, kind="note", locator=r,
                idempotency_key=source_service.idempotency_key_for(r, p)),
            op="sync publish")
        if not result.created:
            continue  # replay: prior revision already exists, no new job
        any_created = True
        atom_id = source_service.atom_id_for_revision(db.conn, result.revision_id)
        assert atom_id is not None, f"publish committed without an atom for {rel}"
        _repair_facet_state(engine, facet_texts)
        if facet_ids:
            engine.assign_facets(atom_id, facet_ids)
        db.insert_event(rel, "vault.synced",
                        {"path": rel, "tags": note.tags,
                         "revision_id": result.revision_id})
        if result.supersedes is not None:
            batched_requires.append(("revision", result.supersedes))
        batched_requires.extend(("atom", str(aid)) for aid in old_atom_ids)
    if batched_requires:
        corpus = projection.find_corpus_holon(engine.conn)
        exclude = {corpus} if corpus is not None else frozenset()
        engine.invalidate_dependents(list(dict.fromkeys(batched_requires)),
                                     exclude=exclude)
    if any_created:
        publish_filing_generation(db.conn)
    return len(prepared)


def sync_vault(db: Database, taxonomy: Taxonomy, engine: FilingEngine,
               vault: Path, relpath: str | None = None) -> int:
    """Atomize vault notes, map tags/folders to facets. Returns notes synced."""
    targets = [vault / relpath] if relpath else sorted(vault.rglob("*.md"))
    return sync_notes(db, taxonomy, engine, vault, targets)


def freshness(conn) -> dict[str, Any]:
    """Honest lexical-vs-semantic readiness (ARCH §3).

    Lexical readiness is synchronous (writer commits before ack). Semantic
    readiness combines the R01 outbox drain state with the per-revision
    projection coverage checklist (INT-1): revisions missing any of
    ``[segmented, extracted, reconciled, indexed]`` keep ``pending`` honest
    even when the outbox queue itself is empty (never vacuous). A revision
    counts only while its source pointer serves it; superseded history and
    unpointed rows never block readiness. ``index_trusted`` /
    ``needs_organization`` are computed from these real signals — never
    hardcoded.

    S2: served from the incrementally maintained coverage state (indexed
    lookups + bounded hole page) instead of correlated EXISTS scans.
    Existing keys keep exact semantics; the ``coverage_*`` keys are
    additive. A high watermark never implies completeness: a derivation
    version mismatch forces ``pending`` even with zero holes.
    """
    unindexed = int(conn.execute(
        "SELECT COUNT(*) FROM facets f WHERE f.centroid_blob IS NULL"
        " AND EXISTS (SELECT 1 FROM memberships m WHERE m.facet_id = f.facet_id)"
    ).fetchone()[0])
    active = gen_store.get_active(conn, FILING_GENERATION_KIND)
    try:
        _need = sorted(NEEDS_ATTENTION)
        stale_holons = int(conn.execute(
            "SELECT COUNT(*) FROM holon_state WHERE status IN (%s)"
            % ", ".join("?" for _ in _need),
            _need,
        ).fetchone()[0])
    except Exception:
        stale_holons = 0
    if coverage_state.coverage_tables_present(conn):
        snap = coverage_state.freshness_snapshot(
            conn, limit=projection.PENDING_REVISIONS_CAP
        )
        pending = snap["live_jobs"]
        uncovered = snap["pending_page"]
        extra = [u for u in uncovered if not u["has_pending_job"]]
        semantic_pending = pending + len(extra)
        version_mismatch = not snap["version_ok"]
        semantic_complete = semantic_pending == 0 and not version_mismatch
        holes_truncated = snap["holes_truncated"] or (
            snap["pending_total"] > len(uncovered)
        )
        return {
            "pending_jobs": pending,
            "semantic_pending": semantic_pending,
            "semantic_status": "complete" if semantic_complete else "pending",
            "coverage_pending": len(uncovered),
            "pending_revisions": [u["revision_id"] for u in uncovered],
            "stale_holons": stale_holons,
            "unindexed_facets": unindexed,
            "generation_id": str(active["generation_id"]) if active else None,
            "watermark": str(active["watermark"]) if active else None,
            "index_trusted": bool(semantic_complete and unindexed == 0),
            "needs_organization": bool(unindexed > 0),
            "coverage_total": snap["pending_total"],
            "coverage_truncated": snap["pending_total"] > len(uncovered),
            "coverage_holes": snap["holes"],
            "holes_truncated": holes_truncated,
            "coverage_watermark": snap["watermark"],
            "projection_version": coverage_state.COVERAGE_VERSION,
            "version_mismatch": version_mismatch,
        }
    pending = outbox_store.pending_count(conn)
    uncovered = projection.pending_coverage(conn)
    extra = [u for u in uncovered if not u["has_pending_job"]]
    semantic_pending = pending + len(extra)
    semantic_complete = semantic_pending == 0
    return {
        "pending_jobs": pending,
        "semantic_pending": semantic_pending,
        "semantic_status": "complete" if semantic_complete else "pending",
        "coverage_pending": len(uncovered),
        "pending_revisions": [u["revision_id"] for u in uncovered],
        "stale_holons": stale_holons,
        "unindexed_facets": unindexed,
        "generation_id": str(active["generation_id"]) if active else None,
        "watermark": str(active["watermark"]) if active else None,
        "index_trusted": bool(semantic_complete and unindexed == 0),
        "needs_organization": bool(unindexed > 0),
        "coverage_total": len(uncovered),
        "coverage_truncated": False,
        "coverage_holes": [
            {
                "revision_id": u["revision_id"],
                "source_id": u["source_id"],
                "missing": u["missing"],
                "reason": "",
            }
            for u in uncovered
        ],
        "holes_truncated": False,
        "coverage_watermark": "",
        "projection_version": coverage_state.COVERAGE_VERSION,
        "version_mismatch": False,
    }


#: Bound on candidate atoms resolved per dossier relation lookup (cap +
#: report pattern: inputs beyond the cap are not resolved; the relation
#: select below carries its own LIMIT).
_SERVED_RELATION_ATOMS_CAP = 2000


def _served_relations(conn, candidates: list[dict[str, Any]], limit: int = 200) -> list[dict[str, Any]]:
    """Re-retrieve graph relations scoped to the candidates' revisions.

    Read-only dossier-route helper (INT-2): maps each atom-bound candidate
    to its served revision, then loads that revision's relation rows
    (bounded). Every returned edge carries its ``revision_id``, so the
    dossier compiler can prove scope (same visibility/version rules as
    candidates); conditions, polarity and endpoint anchors are preserved,
    never dropped. Missing graph tables or unresolvable atoms yield no
    edges — never an error on the read path.
    """
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN"
            " ('atom', 'source_revision', 'relation')").fetchall()}
    except Exception:
        return []
    if names != {"atom", "source_revision", "relation"}:
        return []
    # S2 bounded visibility: one indexed join resolves every candidate's
    # served revision (was one correlated query per candidate). The
    # atom list is capped (cap + report pattern); the relation select
    # below was already LIMIT-bounded.
    pairs: list[tuple[str, int]] = []
    for c in candidates:
        try:
            atom_id = int(c.get("atom_id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        pairs.append((str(c.get("candidate_id", f"atom-{atom_id}")), atom_id))
    if not pairs:
        return []
    bounded = pairs[:_SERVED_RELATION_ATOMS_CAP]
    rev_of: dict[str, str] = {}
    try:
        marks = ",".join("?" for _ in bounded)
        rev_rows = conn.execute(
            "SELECT sr.revision_id, sr.atom_id FROM source_revision sr JOIN source s"
            f" ON s.current_revision_id = sr.revision_id WHERE sr.atom_id IN ({marks})",
            [atom_id for _, atom_id in bounded],
        ).fetchall()
    except Exception:
        return []
    rev_by_atom: dict[int, str] = {}
    for r in rev_rows:
        rev_by_atom.setdefault(int(r["atom_id"]), str(r["revision_id"]))
    for cid, atom_id in bounded:
        if atom_id in rev_by_atom:
            rev_of[cid] = rev_by_atom[atom_id]
    if not rev_of:
        return []
    rev_ids = sorted(set(rev_of.values()))
    marks = ",".join("?" for _ in rev_ids)
    try:
        rows = conn.execute(
            "SELECT rel_type, subject_assertion_id, object_assertion_id,"
            " subject_mention_id, object_mention_id, subject_text, object_text,"
            " polarity, condition_text, epistemic_status, span_id, revision_id"
            f" FROM relation WHERE revision_id IN ({marks})"
            f" ORDER BY rowid LIMIT {int(limit)}",
            rev_ids,
        ).fetchall()
    except Exception:
        return []
    cand_of_rev = {rev: cid for cid, rev in rev_of.items()}
    out = []
    for r in rows:
        rev = str(r["revision_id"])
        subject = r["subject_assertion_id"] or r["subject_mention_id"] or r["subject_text"]
        obj = r["object_assertion_id"] or r["object_mention_id"] or r["object_text"]
        out.append(
            {
                "rel_type": str(r["rel_type"]),
                "subject": str(subject or ""),
                "object": str(obj or ""),
                "evidence": " | ".join(
                    t for t in (str(r["subject_text"] or ""), str(r["object_text"] or ""))
                    if t
                ),
                "condition_text": str(r["condition_text"] or ""),
                "polarity": str(r["polarity"] or ""),
                "epistemic_status": str(r["epistemic_status"] or ""),
                "span_id": str(r["span_id"] or ""),
                "revision_id": rev,
                "candidate_id": cand_of_rev.get(rev, ""),
            }
        )
    return out


def create_app(db_path: str | Path, vault: str | Path | None = None) -> FastAPI:
    state = State()
    state.db_path = Path(db_path)
    state.write_lock = threading.Lock()
    state.readers = threading.local()
    state.reader_registry = []
    state.registry_lock = threading.Lock()
    state.shared_lock = threading.Lock()
    state.shared_hybrid = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from ..obsidian.watcher import DebouncedWatcher

        from ..filing.holon import HolonStore

        # Bound idle-thread reader retention before serving (RSS ceiling).
        _cap_request_threads()
        state.db = Database(db_path, check_same_thread=False)
        state.taxonomy = Taxonomy(state.db.conn)
        state.engine = FilingEngine(state.db.conn, state.taxonomy)
        state.engine.bind_database(state.db)
        # Projection DDL readiness: grouping tables (holons/holon_members)
        # live in HOLON_SCHEMA, not the static schema.sql — ensure them
        # before serving so freshness/coverage reads never race the first
        # write's grouping (idempotent, additive, no behavior change).
        HolonStore(state.db.conn)
        state.vault = Path(vault) if vault else None
        state.started_at = time.time()
        assert state.write_lock is not None
        with state.write_lock:
            # G1: coverage triggers live from boot so every post-start write
            # is tracked even before the first worker pass (idempotent).
            coverage_state.ensure_coverage_schema(state.db.conn)
            publish_filing_generation(state.db.conn)
            # Restart recovery: resume-crash or pre-shutdown pending
            # projection jobs drain here (bounded; R01 outbox resume wired
            # into the served path, not just library-tested).
            assert state.engine is not None
            projection.drain_outbox(state.db, state.engine,
                                    owner="startup-resume")
        # S3 supervised worker (opt-in) + G1 persistent-serving lifecycle:
        # the worker ALWAYS runs under persistent serving so decoupled
        # writes converge autonomously. Owns a private WAL handle; drains
        # in DRAIN_LIMIT batches outside every writer transaction.
        if worker_enabled_from_env() or served_persistent_enabled_from_env():
            start_maintenance_worker(
                state, db_path,
                config=WorkerConfig(
                    batch_limit=projection.DRAIN_LIMIT,
                    poll_interval_s=worker_poll_interval_from_env()))
        # S6 persistent serving (opt-in): cut the query path over to the
        # S4 published base + bounded delta. Opening here is open-only
        # (never a build); the first background seed is scheduled below
        # when atoms exist but no base is servable. All scheduling is
        # non-blocking and coalesced onto one daemon thread.
        if served_persistent_enabled_from_env():
            state.served_rt = ServedRuntime(
                db_path, served_index_dir_from_env(db_path))
            assert state.served_rt is not None
            state.served_rt.request_maintenance()

        def _on_watcher_sync(paths: list[Path]) -> None:
            assert state.db is not None and state.taxonomy is not None
            assert state.engine is not None and state.vault is not None
            assert state.write_lock is not None
            try:
                with state.write_lock:
                    assert state.db is not None and state.taxonomy is not None
                    assert state.engine is not None and state.vault is not None
                    sync_notes(state.db, state.taxonomy, state.engine, state.vault, paths)
            except Exception as exc:
                # Background thread: inner write sections already retried
                # busy; a residual failure is logged, never fatal to the
                # watcher (a dead watcher would stall intake silently).
                log.warning("watcher sync failed for %d paths: %s",
                            len(paths), exc)
                return
            # S6: post-commit served catch-up (background, non-blocking).
            served_after_write(state)

        def _on_watcher_remove(paths: list[Path]) -> None:
            assert state.db is not None and state.vault is not None
            assert state.write_lock is not None
            try:
                with state.write_lock:
                    assert state.db is not None and state.engine is not None
                    assert state.vault is not None
                    for path in paths:
                        try:
                            rel = path.relative_to(state.vault).as_posix()
                        except ValueError:
                            continue
                        if remove_note(state.db, state.engine, rel):
                            state.db.insert_event(rel, "note.removed", {"path": rel})
            except Exception as exc:
                log.warning("watcher remove failed for %d paths: %s",
                            len(paths), exc)
                return
            # S6: post-commit served catch-up (background, non-blocking).
            served_after_write(state)

        if state.vault is not None and state.vault.is_dir():
            state.watcher = DebouncedWatcher(
                state.vault, on_sync=_on_watcher_sync, on_remove=_on_watcher_remove
            )
            state.watcher.start()
        yield
        # S6 clean shutdown (gated: identical teardown unless persistent
        # serving was enabled). Stop scheduling, join the single served
        # maintenance thread with a bound (a stuck build delays, never
        # hangs, shutdown — the thread is a daemon), then drain any
        # residual projection jobs under the writer lock so restart
        # resumes from a drained queue. All best-effort and bounded.
        served_rt = state.served_rt
        state.served_rt = None
        if served_rt is not None:
            served_rt.begin_close()
            served_rt.stop(timeout_s=SERVED_SHUTDOWN_JOIN_S)
        stop_maintenance_worker(state)
        assert state.write_lock is not None
        with state.write_lock:
            if served_rt is not None and state.db is not None:
                try:
                    closer = MaintenanceWorker(
                        db=state.db,
                        config=WorkerConfig(
                            owner="shutdown-drain", batch_limit=64),
                    )
                    closer.run_until_drained(max_batches=4)
                except Exception:
                    pass
            if state.watcher is not None:
                state.watcher.stop()
                state.watcher = None
            assert state.registry_lock is not None and state.reader_registry is not None
            with state.registry_lock:
                leaked = list(state.reader_registry)
                state.reader_registry.clear()
            for rdb in leaked:
                try:
                    rdb.close()
                except Exception:
                    pass
            if state.db is not None:
                state.db.close()
                state.db = None

    app = FastAPI(title="trace-lite", lifespan=lifespan)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        try:
            view = get_reader(state)
        except HTTPException:
            raise
        except Exception as exc:
            if _is_busy_error(exc):
                raise _busy_503("status")
            raise
        facets = view.db.conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0]
        trees = view.db.conn.execute(
            "SELECT COUNT(DISTINCT dimension) FROM facets").fetchone()[0]
        atoms = view.db.count_atoms()
        fresh = freshness(view.db.conn)
        payload: dict[str, Any] = {
            "ok": True,
            "status": "connected",
            "atoms": atoms,
            "total_atoms": atoms,
            "facets": int(facets),
            "total_trees": int(trees),
            "index_trusted": fresh["index_trusted"],
            "needs_organization": fresh["needs_organization"],
            "pending_atoms": fresh["pending_jobs"],
            "semantic_status": fresh["semantic_status"],
            "semantic_pending": fresh["semantic_pending"],
            "coverage_pending": fresh["coverage_pending"],
            "pending_revisions": fresh["pending_revisions"],
            "coverage_total": fresh["coverage_total"],
            "coverage_truncated": fresh["coverage_truncated"],
            "holes_truncated": fresh["holes_truncated"],
            "coverage_watermark": fresh["coverage_watermark"],
            "projection_version": fresh["projection_version"],
            "version_mismatch": fresh["version_mismatch"],
            "stale_holons": fresh["stale_holons"],
            "generation_id": fresh["generation_id"],
            "watermark": fresh["watermark"],
            "unindexed_facets": fresh["unindexed_facets"],
            # S3 additive: supervised-worker lifecycle + writer progress +
            # queue lag. Existing keys keep exact semantics.
            "worker": status_block(state.worker),
            "queue_stats": outbox_store.queue_stats(view.db.conn),
            "job_progress": outbox_store.job_progress(view.db.conn),
            "vault": str(state.vault) if state.vault else None,
            "uptime_s": round(time.time() - state.started_at, 3),
        }
        # S6 additive (present only when persistent serving was enabled at
        # startup): served-snapshot state, seed/catch-up receipts, disk
        # budget verdict, and the queue-lag bound check. Existing keys
        # keep exact semantics; when disabled the key is absent entirely.
        if state.served_rt is not None:
            payload["served_persistent"] = served_status_block(
                state.served_rt, view.db.conn)
        payload["shared_warm"] = shared_warm_status(state)
        return payload

    @app.post("/api/query")
    def query(req: QueryRequest) -> dict[str, Any]:
        text = req.query if req.query is not None else req.query_text
        if not text:
            return {"query": "", "query_text": "", "mode": req.mode, "total_results": 0,
                    "anchors": [], "items": [], "tier_used": 3, "elapsed_ms": 0.0,
                    "sufficiency_state": "insufficient_evidence"}
        limit = req.limit if req.limit is not None else req.top_k if req.top_k is not None else 10
        try:
            view, result = route_pinned(state, text, limit=limit, mode=req.mode)
            fresh = freshness(view.db.conn)
        except HTTPException:
            raise
        except Exception as exc:
            if _is_busy_error(exc):
                raise _busy_503("query")
            raise
        anchors = [
            {"doc_id": a.get("doc_id", ""), "snippet": str(a.get("text", ""))[:300],
             "score": a.get("score", 0.0), "route": str(a.get("route", result.route))}
            for a in result.anchors
        ]
        items = [
            {"atom_id": str(a.get("id", "")), "score": a.get("score", 0.0),
             "route": str(a.get("route", result.route)),
             "source_artifact": {"artifact_id": a.get("doc_id", ""),
                                 "document_name": a.get("doc_id", "")},
             "atom": {"atom_id": str(a.get("id", "")),
                      "content": str(a.get("text", ""))[:1500]}}
            for a in result.anchors
        ]
        return {
            "query": text,
            "query_text": text,
            "mode": req.mode,
            "total_results": len(items),
            "anchors": anchors,
            "items": items,
            "tier_used": result.tier_used,
            # Honest branch labels (INT-2): the primary branch plus the
            # per-branch mix; the ANN branch additionally reports its pinned
            # generation and filtered-coverage adequacy.
            "route": result.route,
            "routes": dict(result.routes),
            "vector_adequate": result.vector_adequate,
            "vector_generation_id": result.vector_generation_id,
            "vector_base_generation_id": result.vector_base_generation_id,
            "elapsed_ms": result.elapsed_ms,
            # Freshness honesty (ARCH §3): results are pinned to a named
            # generation; when semantic projections lag, that is reported as
            # pending coverage — never stale-as-complete.
            "generation_id": view.generation_id,
            "semantic_status": fresh["semantic_status"],
            "semantic_pending": fresh["semantic_pending"],
            "sufficiency_state": "answerable" if result.verdict == "answerable"
            else "insufficient_evidence",
        }

    @app.post("/api/ingest")
    def ingest(req: IngestRequest) -> dict[str, Any]:
        """Remote push path used by the Obsidian plugin (no vault filesystem needed).

        G1 fast ACK: commits the revision + lexical delta + durable outbox
        job and returns immediately; semantic projection converges on the
        background worker. ``pending`` reports honest coverage state for the
        acknowledged revision (a fast worker may already have completed it).
        """
        try:
            with writer(state) as (db, taxonomy, engine):
                atom_id = ingest_note(db, taxonomy, engine,
                                      req.document_name, req.text,
                                      event_type="note.ingested")
            atoms = db.count_atoms()
            cur = source_service.get_current(db.conn, req.document_name)
            revision_id = str(cur["revision_id"]) if cur is not None else None
            coverage = (projection.coverage_for_revision(
                db.conn, revision_id)["stages"]
                if revision_id is not None else {})
            pending = bool(coverage) and not all(coverage.values())
        except HTTPException:
            raise
        except Exception as exc:
            if _is_busy_error(exc):
                raise _busy_503("ingest")
            raise
        # S6: post-commit served catch-up (background, non-blocking).
        served_after_write(state)
        return {"ok": True, "document_name": req.document_name, "doc_id": req.document_name,
                "atom_id": atom_id, "atoms": atoms, "total_atoms": atoms,
                "revision_id": revision_id, "coverage": coverage,
                "pending": pending}

    @app.post("/api/dossier")
    def dossier(req: DossierRequest) -> dict[str, Any]:
        """R07 additive + INT-2 dossier integrity (ARCH §7).

        Read-only: resolves candidates through the generation-pinned reader
        (`route_pinned`, R06 logic untouched), then compiles a dossier with
        revision-provenance citations, explicit disagreements, support states,
        and consumer-tokenizer accounting. Candidates are bound to served
        atoms here but resolved server-side inside the compiler against the
        named immutable revision (namespace/access/version come from the
        store, never from caller claims); relations are re-retrieved scoped
        to the resolved-visible revisions. Scope filters apply inside the
        compiler BEFORE text reveal.
        """
        from ..cordis.dossier import compile_dossier
        from ..cordis.models import (
            AuthorityLevel,
            CommitmentRecord,
            TokenBudget,
        )

        if not req.query.strip():
            return {"query": req.query, "status": "ok",
                    "support_state": "insufficient",
                    "failure_reason": "empty query; no candidates sought"}
        try:
            view, result = route_pinned(state, req.query, limit=req.limit,
                                        mode=req.mode)
            fresh = freshness(view.db.conn)
        except HTTPException:
            raise
        except Exception as exc:
            if _is_busy_error(exc):
                raise _busy_503("dossier")
            raise
        # Per-anchor honest route labels from the cascade itself (vector |
        # beam | hybrid | lexical | holon); the tier number is the legacy
        # position bucket only. Virtual holons are assembled by the dossier
        # compiler (see `virtual_holons`), never claimed from a router tier.
        candidates = []
        for a in result.anchors:
            try:
                atom_id: Any = int(a.get("id"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                atom_id = None
            cand: dict[str, Any] = {
                "candidate_id": f"atom-{a.get('id', '?')}",
                "text": str(a.get("text", "")),
                "doc_id": str(a.get("doc_id", "")),
                "source_id": str(a.get("doc_id", "")),
                "route": str(a.get("route", result.route or "hybrid")),
                "score": float(a.get("score", 0.0)),
            }
            if atom_id is not None:
                cand["atom_id"] = atom_id
            # No caller-claimed namespace/access/version/bytes: the compiler
            # resolves all of them server-side from the atom binding (conn
            # passed below — REQUIRED on this endpoint).
            candidates.append(cand)
        # Scoped re-retrieval: relations for the resolved-visible revisions
        # only (bounded; read-only). Revision binding makes every served edge
        # scope-provable inside the compiler.
        relations = _served_relations(
            view.db.conn,
            [c for c in candidates if c.get("atom_id") is not None],
        )
        mandatory = []
        for m in req.mandatory_invariants or []:
            try:
                mandatory.append(
                    CommitmentRecord(
                        id=str(m.get("id", "?")),
                        target_scopes=list(m.get("target_scopes", ["global"])),
                        authority=AuthorityLevel(str(m.get("authority", "AGENT_DECISION"))),
                        statement=str(m.get("statement", "")),
                    )
                )
            except ValueError:
                raise HTTPException(status_code=422, detail=f"bad invariant {m!r}")
        objective = req.objective if req.objective is not None else req.query
        compiled = compile_dossier(
            query=req.query,
            objective=objective,
            goal_id=req.goal_id,
            scope={
                "namespaces": req.scope_namespaces,
                "access_scopes": req.access_scopes,
                "current_only": req.current_only,
            },
            candidates=candidates,
            relations=relations,
            mandatory=mandatory,
            budget=TokenBudget(
                max_total_tokens=req.max_total_tokens,
                mandatory_invariant_tokens=req.mandatory_invariant_tokens,
                salient_evidence_budget=req.salient_evidence_budget,
            ),
            snapshot={
                "generation_id": view.generation_id,
                "watermark": fresh.get("watermark"),
                "semantic_status": fresh.get("semantic_status"),
                "semantic_pending": fresh.get("semantic_pending"),
                "route": result.route,
                "routes": dict(result.routes),
                "vector_adequate": result.vector_adequate,
                "vector_generation_id": result.vector_generation_id,
                "vector_base_generation_id": result.vector_base_generation_id,
            },
            # REQUIRED: the endpoint resolves candidates server-side to
            # (revision_id, span_id, byte range) against the named immutable
            # revision. The pure function stays usable without conn for
            # tests, but served dossiers always pass it.
            conn=view.db.conn,
        )
        out = compiled.model_dump()
        out["tier_used"] = result.tier_used
        out["route"] = result.route
        out["routes"] = dict(result.routes)
        out["elapsed_ms"] = result.elapsed_ms
        return out

    @app.post("/api/sync")
    def sync(req: SyncRequest) -> dict[str, Any]:
        if state.vault is None and req.path is None:
            return {"synced": 0, "error": "no vault configured"}
        vault = Path(req.path) if req.path else state.vault
        assert vault is not None
        try:
            with writer(state) as (db, taxonomy, engine):
                synced = sync_vault(db, taxonomy, engine, vault)
                atoms = db.count_atoms()
                fresh = freshness(db.conn)
        except HTTPException:
            raise
        except Exception as exc:
            if _is_busy_error(exc):
                raise _busy_503("sync")
            raise
        # S6: post-commit served catch-up (background, non-blocking).
        served_after_write(state)
        return {"synced": synced, "atoms": atoms,
                "coverage_pending": fresh["coverage_pending"],
                "pending_revisions": fresh["pending_revisions"]}

    return app
