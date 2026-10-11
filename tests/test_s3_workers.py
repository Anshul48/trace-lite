"""S3 supervised maintenance workers (contract TL-QN-2026-09-12.1, steering S3).

Exit criteria under test: jobs finish without user replay requests;
restart resumes; workers never publish obsolete results; writer
progress + queue lag are measured. Profiling split (steering line 83)
is recorded to evidence by the profile test.
"""

from __future__ import annotations

import json
import time

import pytest

from trace_lite.filing.workers import MaintenanceWorker, WorkerConfig
from trace_lite.obsidian.parser import parse_note
from trace_lite.source import project as proj
from trace_lite.source import service as svc
from trace_lite.store import generations as gen_store
from trace_lite.store import outbox as outbox_store
from trace_lite.store.database import Database

S3_DIR = None  # resolved per-test from REPO_ROOT (tests must not hardcode cwd)

DOC_A = "# Alpha\n\nAlpha bravo charlie delta echo foxtrot.\n"
DOC_B = "# Beta\n\nGolf hotel india juliet kilo lima.\n"
DOC_C = "# Gamma\n\nMike november oscar papa quebec romeo.\n"


def _worker(db, **cfg_kwargs):
    return MaintenanceWorker(db=db, config=WorkerConfig(owner="s3-test", **cfg_kwargs))


def test_worker_finishes_jobs_without_user_replay(tmp_path):
    """No ingest/sync/replay calls: publish leaves jobs, the worker drains them."""
    db = Database(tmp_path / "s3.db")
    try:
        for i, text in enumerate([DOC_A, DOC_B, DOC_C]):
            res = svc.publish(db, f"doc-{i}.md", text)
            assert res.created is True
        assert outbox_store.pending_count(db.conn) == 3
        assert len(proj.pending_coverage(db.conn)) == 3

        worker = _worker(db)
        try:
            final = worker.run_until_drained()
        finally:
            worker.close()
        assert final["drained_all"] is True, final
        assert final["completed"] == 3
        assert outbox_store.pending_count(db.conn) == 0
        assert proj.pending_coverage(db.conn) == []
        # One job per revision: a document is not a job, jobs/revision == 1.
        assert db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0] == 3
        assert db.conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0] == 3
        # Served state cites the current revisions (lexical read path intact).
        assert [h["doc_id"] for h in db.search_fts("bravo")] == ["doc-0.md"]
        # The worker published a filing generation for its completed work.
        assert final["completed"] > 0 and worker.stats.publications >= 1
    finally:
        db.close()


def test_worker_lease_expiry_and_steal(tmp_path):
    """A live lease blocks steals; an expired lease is reclaimed and completed."""
    db = Database(tmp_path / "s3-lease.db")
    try:
        svc.publish(db, "a.md", DOC_A)
        svc.publish(db, "b.md", DOC_B)
        dead = outbox_store.claim(db.conn, "dead-worker")
        assert dead is not None
        with pytest.raises(ValueError, match=">= 30"):
            outbox_store.claim(db.conn, "short", lease_seconds=5)

        worker = _worker(db)
        try:
            first = worker.run_once()
        finally:
            worker.close()
        # Only the unclaimed job drained; the live lease was never stolen.
        assert first["drained"] == 1
        held = outbox_store.get_job(db.conn, dead.job_id)
        assert held["state"] == "claimed" and held["lease_owner"] == "dead-worker"

        # Expire the dead lease without waiting 30 wall seconds (R01 precedent).
        db.conn.execute(
            "UPDATE projection_job SET lease_expires_at = ? WHERE job_id = ?",
            (0.0, dead.job_id),
        )
        db.conn.commit()
        worker2 = _worker(db)
        try:
            second = worker2.run_once()
        finally:
            worker2.close()
        assert second["drained"] == 1
        assert outbox_store.pending_count(db.conn) == 0
        assert proj.pending_coverage(db.conn) == []
        stolen = outbox_store.get_job(db.conn, dead.job_id)
        assert stolen["state"] == "done"
    finally:
        db.close()


def test_worker_retry_backoff_then_heals_without_replay(tmp_path):
    """Failing work backs off exponentially; a later write heals it (no replay)."""
    assert outbox_store.retry_delay_s(1) == 1.0
    assert outbox_store.retry_delay_s(2) == 2.0
    assert outbox_store.retry_delay_s(3) == 4.0
    assert outbox_store.retry_delay_s(100) == outbox_store.RETRY_CAP_S
    with pytest.raises(ValueError):
        outbox_store.retry_delay_s(0)

    db = Database(tmp_path / "s3-retry.db")
    try:
        svc.publish(db, "solo.md", DOC_A)  # lone atom: corpus needs >= 2
        worker = _worker(db)
        try:
            rep1 = worker.run_once()
            assert rep1["drained"] == 1 and len(rep1["failed"]) == 1
            assert rep1["backoff_s"] == pytest.approx(1.0)
            assert rep1["published_generation"] is None  # nothing completed
            assert worker.stats.consecutive_failures == 1
            rep2 = worker.run_once()
            assert len(rep2["failed"]) == 1
            assert rep2["backoff_s"] == pytest.approx(2.0)
            assert worker.stats.consecutive_failures == 2
            assert outbox_store.queue_stats(db.conn)["with_error"] == 1

            # A new arrival (not a replay of the failed job) heals the corpus.
            svc.publish(db, "second.md", DOC_B)
            final = worker.run_until_drained()
            assert final["drained_all"] is True, final
            assert proj.pending_coverage(db.conn) == []
            assert worker.stats.consecutive_failures == 0
        finally:
            worker.close()
    finally:
        db.close()


def test_finite_backfill_never_spins_on_permanent_failure(tmp_path):
    """run_until_drained reports futility instead of spinning on poison jobs."""
    db = Database(tmp_path / "s3-futile.db")
    try:
        svc.publish(db, "solo.md", DOC_A)  # lone atom: can never complete alone
        worker = _worker(db)
        try:
            t0 = time.monotonic()
            final = worker.run_until_drained()
            elapsed = time.monotonic() - t0
        finally:
            worker.close()
        assert final["drained_all"] is False
        assert "no forward progress" in final["reason"]
        assert final["batches"] <= 4
        assert elapsed < 20.0
        # The job is still retryable later: nothing was dropped or buried.
        assert outbox_store.queue_stats(db.conn)["depth"] == 1
    finally:
        db.close()


def test_restart_resumes_without_user_replay(tmp_path):
    """Crash-claimed + pending jobs survive a DB reopen and drain with no replay."""
    path = tmp_path / "s3-restart.db"
    db = Database(path)
    res_a = svc.publish(db, "a.md", DOC_A)
    svc.publish(db, "b.md", DOC_B)
    crashed = outbox_store.claim(db.conn, "crashed-worker")
    assert crashed is not None and crashed.job_id == res_a.job_id
    db.close()  # crash: claimed job never completed, no fail() called

    resumed = Database(path)
    try:
        # Live lease still protects the crashed claim immediately after reopen.
        assert outbox_store.get_job(resumed.conn, crashed.job_id)["state"] == "claimed"
        resumed.conn.execute(
            "UPDATE projection_job SET lease_expires_at = ? WHERE job_id = ?",
            (0.0, crashed.job_id),
        )
        resumed.conn.commit()
        worker = _worker(resumed)
        try:
            final = worker.run_until_drained()
        finally:
            worker.close()
        assert final["drained_all"] is True, final
        assert outbox_store.pending_count(resumed.conn) == 0
        assert proj.pending_coverage(resumed.conn) == []
        assert [h["doc_id"] for h in resumed.search_fts("bravo")] == ["a.md"]
    finally:
        resumed.close()


def test_stale_revision_output_skipped_never_served(tmp_path):
    """Superseded revisions complete without projecting; stale generations never activate."""
    db = Database(tmp_path / "s3-stale.db")
    try:
        svc.publish(db, "other.md", DOC_B)  # second live atom: corpus can form
        svc.publish(db, "doc.md", "# Doc\n\nAlpha bravo charlie first version.\n")
        svc.publish(db, "doc.md", "# Doc\n\nZulu yankee xray second version.\n")
        worker = _worker(db)
        try:
            final = worker.run_until_drained()
        finally:
            worker.close()
        assert final["drained_all"] is True, final
        assert final["skipped"] == 1 and final["completed"] == 2
        skipped_event = db.conn.execute(
            "SELECT 1 FROM events WHERE event_type = 'projection.skipped_superseded'"
            " LIMIT 1"
        ).fetchone()
        assert skipped_event is not None
        # The obsolete revision was never projected: no segments for it.
        revs = {
            r["status"]: r["revision_id"]
            for r in db.conn.execute(
                "SELECT status, revision_id FROM source_revision"
                " WHERE source_id = 'doc.md'"
            ).fetchall()
        }
        assert set(revs) == {"superseded", "current"}
        assert db.conn.execute(
            "SELECT COUNT(*) FROM segmentation_view WHERE revision_id = ?",
            (revs["superseded"],),
        ).fetchone()[0] == 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM segmentation_view WHERE revision_id = ?",
            (revs["current"],),
        ).fetchone()[0] == 1
        # Served state is the current revision only.
        assert svc.get_current(db.conn, "doc.md")["revision_id"] == revs["current"]
        assert [h["doc_id"] for h in db.search_fts("yankee")] == ["doc.md"]
        assert db.search_fts("bravo") == []

        # Generation guard: stale output never moves the served pointer.
        active_before = gen_store.get_active(db.conn, "filing")
        assert active_before is not None
        stale_gid = gen_store.create_generation(
            db.conn, kind="filing", config_hash="stale-cfg", watermark="stale",
        )
        skipped = gen_store.guarded_activate(
            db.conn, stale_gid, allow=False, reason="superseded revision"
        )
        assert skipped == {
            "activated": False, "generation_id": stale_gid,
            "reason": "superseded revision",
        }
        assert gen_store.get_active(db.conn, "filing")["generation_id"] == (
            active_before["generation_id"]
        )
        fresh_gid = gen_store.create_generation(
            db.conn, kind="filing", config_hash="fresh-cfg", watermark="fresh",
        )
        allowed = gen_store.guarded_activate(db.conn, fresh_gid, allow=True)
        assert allowed["activated"] is True
        assert gen_store.get_active(db.conn, "filing")["generation_id"] == fresh_gid
        with pytest.raises(KeyError):
            gen_store.guarded_activate(db.conn, "no-such-generation", allow=True)
    finally:
        db.close()


def test_backpressure_bounded_batches_and_reads_unblocked(tmp_path):
    """Deep queues report pressure, drain in bounded batches, never block reads."""
    db = Database(tmp_path / "s3-pressure.db")
    try:
        for i in range(10):
            svc.publish(db, f"doc-{i}.md", f"# Doc {i}\n\nCommon token quorum {i}.\n")
        assert outbox_store.queue_stats(db.conn)["depth"] == 10
        assert outbox_store.pressure_status(db.conn, 6)["pressured"] is True
        assert outbox_store.pressure_status(db.conn, 11)["pressured"] is False
        with pytest.raises(ValueError):
            outbox_store.pressure_status(db.conn, 0)

        worker = _worker(db, batch_limit=4, high_watermark=6)
        try:
            rep = worker.run_once()
            assert rep["pressured"] is True
            assert rep["drained"] <= 4
            assert rep["queue_after"]["depth"] == 10 - rep["drained"]
            # Lexical retrieval is served mid-backlog (worker never blocks readers).
            assert len(db.search_fts("quorum")) >= rep["drained"]
            final = worker.run_until_drained()
            assert final["drained_all"] is True, final
            assert worker.stats.pressure_passes >= 1
            assert proj.pending_coverage(db.conn) == []
        finally:
            worker.close()
    finally:
        db.close()


def test_queue_lag_and_writer_progress_measured(tmp_path):
    """Queue lag + writer progress are observable before, during, and after drain."""
    db = Database(tmp_path / "s3-lag.db")
    try:
        empty = outbox_store.queue_stats(db.conn)
        assert empty == {
            "pending": 0, "claimed": 0, "depth": 0, "max_attempt": 0,
            "with_error": 0, "oldest_created_at": None, "queue_lag_s": None,
        }
        assert outbox_store.job_progress(db.conn) == {
            "pending": 0, "claimed": 0, "done": 0, "total": 0, "done_ratio": 1.0,
        }
        svc.publish(db, "a.md", DOC_A)
        svc.publish(db, "b.md", DOC_B)
        before = outbox_store.queue_stats(db.conn)
        assert before["depth"] == 2 and before["pending"] == 2
        assert before["queue_lag_s"] is not None and before["queue_lag_s"] >= 0.0
        prog = outbox_store.job_progress(db.conn)
        assert (prog["total"], prog["done"], prog["done_ratio"]) == (2, 0, 0.0)

        claim = outbox_store.claim(db.conn, "probe")
        mid = outbox_store.queue_stats(db.conn)
        assert (mid["pending"], mid["claimed"], mid["depth"]) == (1, 1, 2)
        assert outbox_store.complete(db.conn, claim.job_id, "probe") is True

        worker = _worker(db)
        try:
            worker.run_until_drained()
        finally:
            worker.close()
        after = outbox_store.queue_stats(db.conn)
        assert after["depth"] == 0 and after["queue_lag_s"] is None
        done = outbox_store.job_progress(db.conn)
        assert done["done"] == 2 and done["done_ratio"] == 1.0
    finally:
        db.close()


def test_app_worker_drains_autonomously_and_resumes_on_restart(tmp_path, monkeypatch):
    """Wired worker finishes jobs with no sync/ingest call, across a restart."""
    monkeypatch.setenv("TRACE_LITE_WORKER", "1")
    monkeypatch.setenv("TRACE_LITE_WORKER_POLL_S", "0.05")
    from fastapi.testclient import TestClient

    from trace_lite.api import create_app

    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "s3app.db"

    def _settled(client, want_done):
        status = client.get("/api/status").json()
        return (
            status["worker"]["enabled"] is True
            and status["coverage_pending"] == 0
            and status["queue_stats"]["depth"] == 0
            and status["job_progress"]["done"] >= want_done
        )

    with TestClient(create_app(db_path, vault)) as client:
        # External writer bypasses every HTTP write path: no /sync, no /ingest.
        ext = Database(db_path)
        try:
            svc.publish(ext, "ext-a.md", DOC_A)
            svc.publish(ext, "ext-b.md", DOC_B)
        finally:
            ext.close()
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if _settled(client, 2):
                break
            time.sleep(0.1)
        assert _settled(client, 2), "worker did not drain without any replay call"

    # Work enqueued while the server is down resumes on restart, still no replay.
    down = Database(db_path)
    try:
        svc.publish(down, "ext-c.md", DOC_C)
    finally:
        down.close()
    with TestClient(create_app(db_path, vault)) as client2:
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            if _settled(client2, 3):
                break
            time.sleep(0.1)
        assert _settled(client2, 3), "restart did not resume without replay"


def _count_table(conn, table):
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def test_s3_profiling_split(tmp_path):
    """Steering line 83: stage-cost split at small corpus sizes (writes evidence)."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    s3_dir = repo_root / "evidence" / "query-native" / "_integration" / "S3"
    s3_dir.mkdir(parents=True, exist_ok=True)

    sizes = [4, 16, 48]
    profile = {"sizes": {}, "notes": []}
    log_lines = ["# S3 profiling split (parsing, SQL, grouping, commit, checkpoint, publication)"]
    for n in sizes:
        db = Database(tmp_path / f"s3-prof-{n}.db")
        try:
            texts = [
                f"# Doc {i}\n\nQuorum payload {i} with shared alpha bravo terms.\n"
                for i in range(n)
            ]
            total_bytes = sum(len(t.encode("utf-8")) for t in texts)

            t0 = time.perf_counter()
            for i, text in enumerate(texts):
                parse_note(f"doc-{i}.md", text)
            parse_s = time.perf_counter() - t0

            t0 = time.perf_counter()
            for i, text in enumerate(texts):
                svc.publish(db, f"doc-{i}.md", text)
            publish_s = time.perf_counter() - t0

            worker = _worker(db)
            try:
                t0 = time.perf_counter()
                final = worker.run_until_drained()
            finally:
                worker.close()
            drain_s = time.perf_counter() - t0
            assert final["drained_all"] is True, final

            t0 = time.perf_counter()
            checkpoint = db.checkpoint_now()
            checkpoint_s = time.perf_counter() - t0

            t0 = time.perf_counter()
            from trace_lite.api.app import (
                FILING_CONFIG_HASH,
                FILING_GENERATION_KIND,
                filing_watermark,
            )

            gid = gen_store.create_generation(
                db.conn, kind=FILING_GENERATION_KIND,
                config_hash=FILING_CONFIG_HASH,
                watermark=filing_watermark(db.conn), commit=False,
            )
            gen_store.activate_generation(db.conn, gid, commit=False)
            db.conn.commit()
            publish_gen_s = time.perf_counter() - t0

            spans = _count_table(db.conn, "evidence_span")
            members = _count_table(db.conn, "holon_members")
            jobs = _count_table(db.conn, "projection_job")
            revs = _count_table(db.conn, "source_revision")
            entry = {
                "docs": n,
                "bytes": total_bytes,
                "parse_s": round(parse_s, 4),
                "publish_sql_s": round(publish_s, 4),
                "drain_projection_s": round(drain_s, 4),
                "checkpoint_s": round(checkpoint_s, 4),
                "publication_s": round(publish_gen_s, 4),
                "checkpoint_frames": {
                    "busy": checkpoint.busy, "log": checkpoint.log_frames,
                    "checkpointed": checkpoint.checkpointed_frames,
                },
                "rows": {
                    "revisions": revs, "jobs": jobs, "spans": spans,
                    "holon_members": members,
                },
                "ratios": {
                    "spans_per_doc": round(spans / n, 2),
                    "jobs_per_revision": round(jobs / revs, 2) if revs else 0.0,
                    "memberships_per_span": (
                        round(members / spans, 2) if spans else 0.0
                    ),
                    "bytes_per_doc": round(total_bytes / n, 1),
                    "revision_churn": round(revs / n, 2),
                },
                "drain_batches": final["batches"],
            }
            assert entry["ratios"]["jobs_per_revision"] == 1.0
            profile["sizes"][str(n)] = entry
            log_lines.append(
                f"[{n} docs] parse={parse_s:.3f}s publish_sql={publish_s:.3f}s "
                f"drain={drain_s:.3f}s checkpoint={checkpoint_s:.4f}s "
                f"publication={publish_gen_s:.4f}s spans/doc={spans / n:.1f} "
                f"jobs/rev={jobs / revs:.1f}"
            )
        finally:
            db.close()
    profile["notes"] = [
        "drain_projection_s bundles SQL lookup + grouping + transaction commit "
        "(shared drain_outbox; stages commit per job, not separately timed).",
        "No encoder runs in the lexical maintenance pipeline (hashbow/ONNX "
        "encoding is S4 scope); parsing here is note parsing + facet hints.",
    ]
    (s3_dir / "profile.json").write_text(json.dumps(profile, indent=2), encoding="utf-8")
    (s3_dir / "profile.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
