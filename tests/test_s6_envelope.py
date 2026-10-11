"""S6 release envelope: persistent-serving cutover, shutdown/drain, budgets.

Contract TL-QN-2026-09-12.1, steering S6 row. All tests run through the
real HTTP app (``TestClient`` + real DB files) or the real S4 layer;
nothing here mocks a PASS. Heavy operating-point curves (2k/10k/50k)
live in ``evidence/query-native/_integration/S6/`` (background-measured),
not in this file: this suite stays bounded and fast.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from trace_lite.api import app as appmod
from trace_lite.api import create_app
from trace_lite.api.app import (
    S6_FRESHNESS_BUDGET_S,
    ServedRuntime,
    check_disk_budget,
    queue_lag_bound_from_env,
    served_disk_budget_from_env,
    served_index_dir_from_env,
    served_persistent_enabled_from_env,
)
from trace_lite.router.vector_store import ServedVectors
from trace_lite.store import Database

DOC_A = "# Alpha\n\nBatching improves throughput under low contention quorum.\n"
DOC_B = "# Beta\n\nWAL mode allows one writer with concurrent readers quorum.\n"
DOC_C = "# Gamma\n\nRetrieval blends lexical overlap with dense vectors quorum.\n"


def _wait_for(fn, *, timeout_s=30.0, what="condition"):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}: last={last!r}")


def _status(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    return resp.json()


def _ingest(client, name, text):
    resp = client.post("/api/ingest",
                       json={"document_name": name, "text": text})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _query(client, text):
    resp = client.post("/api/query", json={"query": text, "limit": 5})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _s6_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACE_LITE_SERVED_PERSISTENT", "1")
    idx = tmp_path / "served"
    monkeypatch.setenv("TRACE_LITE_SERVED_INDEX_DIR", str(idx))
    return idx


# -- env gates ------------------------------------------------------------

def test_s6_env_helpers_default_off(monkeypatch, tmp_path):
    for var in ("TRACE_LITE_SERVED_PERSISTENT", "TRACE_LITE_SERVED_INDEX_DIR",
                "TRACE_LITE_SERVED_DISK_BUDGET_BYTES",
                "TRACE_LITE_QUEUE_LAG_BOUND_S"):
        monkeypatch.delenv(var, raising=False)
    assert served_persistent_enabled_from_env() is False
    db_path = tmp_path / "a.db"
    assert served_index_dir_from_env(db_path) == tmp_path / "a.served"
    assert served_disk_budget_from_env() is None
    assert queue_lag_bound_from_env() == 60.0


def test_s6_env_helpers_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACE_LITE_SERVED_PERSISTENT", "1")
    monkeypatch.setenv("TRACE_LITE_SERVED_INDEX_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("TRACE_LITE_SERVED_DISK_BUDGET_BYTES", "123")
    monkeypatch.setenv("TRACE_LITE_QUEUE_LAG_BOUND_S", "7.5")
    assert served_persistent_enabled_from_env() is True
    assert served_index_dir_from_env(tmp_path / "a.db") == tmp_path / "x"
    assert served_disk_budget_from_env() == 123
    assert queue_lag_bound_from_env() == 7.5
    monkeypatch.setenv("TRACE_LITE_SERVED_DISK_BUDGET_BYTES", "bogus")
    assert served_disk_budget_from_env() is None
    monkeypatch.setenv("TRACE_LITE_QUEUE_LAG_BOUND_S", "bogus")
    assert queue_lag_bound_from_env() == 60.0


# -- gate 1: identical behavior unless enabled -----------------------------

def test_disabled_by_default_preserves_legacy_path(tmp_path):
    """Flag off: no served_persistent status key; legacy pin contract holds."""
    db_path = tmp_path / "s6off.db"
    with TestClient(create_app(db_path)) as client:
        _ingest(client, "a.md", DOC_A)
        _ingest(client, "b.md", DOC_B)
        status = _status(client)
        assert "served_persistent" not in status
        assert status["worker"] == {"enabled": False}
        q = _query(client, "batching throughput contention")
        # Legacy query-triggered build pins the ANN branch to the filing
        # generation on a tiny corpus (pre-existing contract, unchanged).
        assert q["vector_generation_id"] == q["generation_id"]
        assert q["generation_id"]


# -- gate 2: cutover correctness -------------------------------------------

def test_cutover_serves_persistent_after_background_seed(tmp_path,
                                                         monkeypatch):
    """Flag on: background seed publishes; queries pin without building."""
    idx = _s6_env(monkeypatch, tmp_path)
    db_path = tmp_path / "s6on.db"
    with TestClient(create_app(db_path)) as client:
        _ingest(client, "a.md", DOC_A)
        _ingest(client, "b.md", DOC_B)
        _ingest(client, "c.md", DOC_C)
        # First query may race the seed: it must still be fast and honest
        # (fallbacks), never an error and never a query-path build.
        t0 = time.monotonic()
        early = _query(client, "batching throughput contention")
        assert time.monotonic() - t0 < 5.0
        assert early["generation_id"]
        # Wait for the background seed, then the pin contract holds on the
        # persistent view (same equality the legacy path guarantees).
        settled = _wait_for(
            lambda: _status(client).get("served_persistent"),
            what="served_persistent status block")
        assert settled["enabled"] is True
        _wait_for(
            lambda: _status(client)["served_persistent"]["seed"]["state"]
            in ("done", "failed") and
            _status(client)["served_persistent"]["servable"],
            what="background seed servable")
        # The seed receipt is a truthful one-shot (atoms-at-seed-time); the
        # served view converges via catch-up overlay. Assert converged
        # coverage, not the transient receipt (the old exact-3 receipt
        # assertion raced seed-vs-ingest and failed 2-vs-3 pre-existing).
        _wait_for(
            lambda: (_status(client)["served_persistent"].get("served_atoms")
                     == 3),
            what="served coverage converges to 3 atoms")
        status = _status(client)
        assert status["served_persistent"]["seed"]["receipt"] is not None
        assert status["served_persistent"]["served_atoms"] == 3
        assert status["served_persistent"]["disk_within_budget"] is True
        assert (idx / "CURRENT").exists()
        assert (idx / "s4-cache.json").exists()
        t0 = time.monotonic()
        q = _query(client, "batching throughput contention")
        assert time.monotonic() - t0 < S6_FRESHNESS_BUDGET_S
        assert q["vector_generation_id"] == q["generation_id"]
        assert q["route"] in ("vector", "lexical", "beam", "hybrid")
        # B4/B5 honesty keys travel with every response/status block.
        assert "vector_base_generation_id" in q
        assert "served_atoms" in status["served_persistent"]
        assert "content_version" in status["served_persistent"]


def test_first_query_never_builds_and_never_waits(tmp_path, monkeypatch):
    """A stuck seed cannot stall or fail queries: fallbacks serve fast."""
    idx = _s6_env(monkeypatch, tmp_path)
    gate = threading.Event()
    real_seed = ServedRuntime._run_seed

    def _stuck(self, db):
        assert gate.wait(timeout=60.0), "seed gate never released"
        return real_seed(self, db)

    monkeypatch.setattr(ServedRuntime, "_run_seed", _stuck)
    db_path = tmp_path / "s6stuck.db"
    with TestClient(create_app(db_path)) as client:
        _ingest(client, "a.md", DOC_A)
        _wait_for(
            lambda: _status(client)["served_persistent"][
                "maintenance_running"],
            what="seed in flight")
        # An edit landing mid-seed must not be lost: it chains a second
        # pass after the in-flight job instead of queuing behind nothing.
        _ingest(client, "b.md", DOC_B)
        t0 = time.monotonic()
        q = _query(client, "batching throughput contention")
        assert time.monotonic() - t0 < 5.0
        # Honest detach: no vector pin while maintenance runs, still 200.
        assert q["vector_generation_id"] is None
        t0 = time.monotonic()
        status = _status(client)
        assert time.monotonic() - t0 < 5.0
        assert status["semantic_status"] in ("complete", "pending")
        gate.set()
        _wait_for(
            lambda: _status(client)["served_persistent"]["servable"],
            what="seed recovered and servable")
        q2 = _query(client, "batching throughput contention")
        assert q2["vector_generation_id"] == q2["generation_id"]
        # The chained pass converged: both docs are served atoms.
        assert _status(client)["atoms"] == 2


# -- gate 3: one thread, clean shutdown/drain -------------------------------

def test_single_maintenance_thread_no_duplicates(tmp_path, monkeypatch):
    """Repeated writes + queries never duplicate the background thread."""
    _s6_env(monkeypatch, tmp_path)
    db_path = tmp_path / "s6threads.db"
    name = "s6-served-maintenance"
    with TestClient(create_app(db_path)) as client:
        for i, doc in enumerate((DOC_A, DOC_B, DOC_C)):
            _ingest(client, f"d{i}.md", doc)
        for _ in range(5):
            _query(client, "batching throughput contention")
        _wait_for(
            lambda: _status(client)["served_persistent"]["servable"],
            what="servable")
        alive = [t for t in threading.enumerate()
                 if t.name == name and t.is_alive()]
        assert len(alive) == 1
    # Lifespan teardown joined it: no leaked thread.
    time.sleep(0.3)
    assert [t for t in threading.enumerate()
            if t.name == name and t.is_alive()] == []


def test_shutdown_drains_projection_queue(tmp_path, monkeypatch):
    """Jobs enqueued out-of-band are drained by the shutdown path itself."""
    from trace_lite.source import service as svc

    _s6_env(monkeypatch, tmp_path)
    db_path = tmp_path / "s6drain.db"
    with TestClient(create_app(db_path)) as client:
        ext = Database(db_path)
        try:
            svc.publish(ext, "ext-a.md", DOC_A)
            svc.publish(ext, "ext-b.md", DOC_B)
        finally:
            ext.close()
        # No HTTP write, no replay: close straight into shutdown.
    con = sqlite3.connect(str(db_path))
    try:
        (pending,) = con.execute(
            "SELECT COUNT(*) FROM projection_job"
            " WHERE state IN ('pending', 'claimed')").fetchone()
    finally:
        con.close()
    assert pending == 0


# -- gate 4: lag measured and bounded ---------------------------------------

def test_queue_lag_measured_and_bounded(tmp_path, monkeypatch):
    """queue_lag_s is reported against the frozen bound, empty or loaded."""
    from trace_lite.source import service as svc

    idx = _s6_env(monkeypatch, tmp_path)
    assert idx is not None
    db_path = tmp_path / "s6lag.db"
    with TestClient(create_app(db_path)) as client:
        ext = Database(db_path)
        try:
            svc.publish(ext, "ext-a.md", DOC_A)
            svc.publish(ext, "ext-b.md", DOC_B)
        finally:
            ext.close()
        loaded = _status(client)
        lag = loaded["served_persistent"]["lag"]
        assert lag["bound_s"] == 60.0
        assert lag["queue_lag_s"] is not None and lag["queue_lag_s"] >= 0.0
        assert lag["within_bound"] is True
        # G1: HTTP writes queue (no synchronous drain); the worker,
        # auto-started under persistent serving, clears the backlog: lag
        # None, still within bound (empty queue is the bounded steady state).
        _ingest(client, "c.md", DOC_C)

        def _drained():
            snap = _status(client)
            return snap if snap["queue_stats"]["depth"] == 0 else None

        drained = _wait_for(_drained, what="queue drained")
        assert drained["served_persistent"]["lag"]["queue_lag_s"] is None
        assert drained["served_persistent"]["lag"]["within_bound"] is True


# -- gate 5: disk-budget check ----------------------------------------------

def test_disk_budget_two_generations_plus_wal(tmp_path):
    """Old/new generations + WAL are all measured; tiny budget fails."""
    db_path = tmp_path / "s6disk.db"
    db = Database(db_path)
    try:
        for i in range(10):
            db.insert_atom(f"doc-{i}.md",
                           f"Quorum payload {i} alpha bravo contention.\n")
        db.conn.commit()
        idx = tmp_path / "served"
        sv = ServedVectors(idx)
        sv.seed_from_db(db.conn, "gen-a", watermark="a")
        sv.seed_from_db(db.conn, "gen-b", watermark="b")
        assert (idx / "CURRENT").exists()
        open_sv = ServedVectors.open(idx)
        assert open_sv.servable
        usage = check_disk_budget(idx, db_path, served=open_sv)
        assert len(usage["per_generation"]) >= 2
        assert usage["index_bytes"] > 0
        assert Path(db_path).name in usage["db_files"]
        assert usage["footprint_bytes"] >= usage["index_bytes"]
        assert usage["host_free_bytes"] > 0
        assert usage["within_budget"] is True
        starved = check_disk_budget(idx, db_path, served=open_sv,
                                    budget_bytes=1)
        assert starved["within_budget"] is False
        airless = check_disk_budget(idx, db_path, served=open_sv,
                                    min_headroom_ratio=1e15)
        assert airless["within_budget"] is False
    finally:
        db.close()


def test_disk_budget_without_base_measures_db(tmp_path):
    """No published base: DB/WAL still measured, verdict still computed."""
    db_path = tmp_path / "s6disk0.db"
    db = Database(db_path)
    try:
        db.insert_atom("doc.md", "Quorum payload alpha.\n")
        db.conn.commit()
    finally:
        db.close()
    usage = check_disk_budget(tmp_path / "nosuch", db_path, served=None)
    assert usage["per_generation"] == {}
    assert Path(db_path).name in usage["db_files"]
    assert usage["within_budget"] is True
