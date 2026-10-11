"""P02: atomic outbox claim + race-safe enqueue (REUSE-VERIFY.md A3 + O3).

- claim(): N concurrent claimers on one job -> exactly one winner.
- enqueue(): N concurrent same-key enqueues -> same job id, no raise.
- lease + FIFO ordering semantics preserved (single-threaded).
"""

from __future__ import annotations

import threading

from trace_lite.source import service as svc
from trace_lite.store import outbox as outbox_store
from trace_lite.store.database import Database

DOC = "# Doc\n\nAlpha bravo charlie delta echo foxtrot.\n"


def _open_seeded(path):
    """Open a Database with one published revision; return (db, revision_id).

    The seed job publish() enqueues is drained so the queue starts empty.
    """
    db = Database(path)
    svc.publish(db, "doc.md", DOC)
    row = db.conn.execute("SELECT revision_id FROM source_revision LIMIT 1").fetchone()
    assert row is not None
    seed = outbox_store.claim(db.conn, "seed-drain")
    assert seed is not None
    outbox_store.complete(db.conn, seed.job_id, seed.lease_owner)
    return db, str(row[0])


def _run_threads(nthreads, target, *args):
    barrier = threading.Barrier(nthreads)
    results: list = [None] * nthreads
    threads = [
        threading.Thread(target=target, args=(barrier, results, i) + args)
        for i in range(nthreads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "worker thread hung"
    return results


def _claim_worker(barrier, results, i, path):
    # Each thread opens its own Database (one instance per thread; sqlite
    # connections are thread-affine). The barrier fires after all opens so
    # only the claim() calls race.
    db = Database(path)
    try:
        barrier.wait(timeout=30)
        results[i] = outbox_store.claim(db.conn, f"worker-{i}")
    except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
        results[i] = exc
    finally:
        db.close()


def test_concurrent_double_claim_exactly_one_winner(tmp_path):
    """Concurrent claimers on a single job: exactly one wins, no errors."""
    path = tmp_path / "claim-race.db"
    db, rev = _open_seeded(path)
    nthreads = 6
    try:
        for round_no in range(15):
            job_id = outbox_store.enqueue(
                db.conn,
                job_type="refresh",
                input_revision_id=rev,
                idempotency_key=f"race-claim-{round_no}",
            )
            results = _run_threads(nthreads, _claim_worker, str(path))
            errors = [r for r in results if isinstance(r, BaseException)]
            assert errors == [], f"round {round_no}: {errors!r}"
            winners = [r for r in results if r is not None]
            assert len(winners) == 1, (
                f"round {round_no}: {len(winners)} winners, expected exactly 1"
            )
            assert winners[0].job_id == job_id
            assert winners[0].attempt == 1
            outbox_store.complete(db.conn, job_id, winners[0].lease_owner)
    finally:
        db.close()


def _enqueue_worker(barrier, results, i, path, rev, key):
    db = Database(path)
    try:
        barrier.wait(timeout=30)
        results[i] = outbox_store.enqueue(
            db.conn,
            job_type="refresh",
            input_revision_id=rev,
            idempotency_key=key,
        )
    except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
        results[i] = exc
    finally:
        db.close()


def test_concurrent_same_key_enqueue_returns_existing_id(tmp_path):
    """Concurrent same-key enqueues: same job id everywhere, nothing raised."""
    path = tmp_path / "enqueue-race.db"
    db, rev = _open_seeded(path)
    nthreads = 6
    try:
        for round_no in range(10):
            key = f"race-enqueue-{round_no}"
            results = _run_threads(nthreads, _enqueue_worker, str(path), rev, key)
            errors = [r for r in results if isinstance(r, BaseException)]
            assert errors == [], f"round {round_no}: {errors!r}"
            assert len(set(results)) == 1, f"round {round_no}: divergent ids {results!r}"
            count = db.conn.execute(
                "SELECT COUNT(*) FROM projection_job WHERE idempotency_key = ?",
                (key,),
            ).fetchone()[0]
            assert count == 1
    finally:
        db.close()


def test_claim_fifo_ordering_preserved(tmp_path):
    """Claims drain in enqueue (oldest-first) order; empty queue -> None."""
    with Database(tmp_path / "fifo.db") as db:
        svc.publish(db, "doc.md", DOC)
        rev = str(
            db.conn.execute("SELECT revision_id FROM source_revision LIMIT 1").fetchone()[0]
        )
        # Drain the seed job publish() enqueued so only fifo-N jobs remain.
        seed = outbox_store.claim(db.conn, "seed-drain")
        assert seed is not None
        outbox_store.complete(db.conn, seed.job_id, seed.lease_owner)
        ids = [
            outbox_store.enqueue(
                db.conn,
                job_type="refresh",
                input_revision_id=rev,
                idempotency_key=f"fifo-{i}",
            )
            for i in range(3)
        ]
        assert len(ids) == 3
        seen = []
        for _ in range(3):
            got = outbox_store.claim(db.conn, "fifo-worker")
            assert got is not None
            seen.append(got.job_id)
            outbox_store.complete(db.conn, got.job_id, got.lease_owner)
        assert seen == ids
        assert outbox_store.claim(db.conn, "fifo-worker") is None


def test_claim_lease_semantics_preserved(tmp_path):
    """Lease floor, live-lease block, expiry reclaim, error clear, idempotency."""
    with Database(tmp_path / "lease.db") as db:
        svc.publish(db, "doc.md", DOC)
        rev = str(
            db.conn.execute("SELECT revision_id FROM source_revision LIMIT 1").fetchone()[0]
        )
        # Drain the seed job publish() enqueued so lease-1 is the only work.
        seed = outbox_store.claim(db.conn, "seed-drain")
        assert seed is not None
        outbox_store.complete(db.conn, seed.job_id, seed.lease_owner)
        job_id = outbox_store.enqueue(
            db.conn, job_type="refresh", input_revision_id=rev, idempotency_key="lease-1"
        )
        # Sequential duplicate enqueue returns the existing id; no dup row.
        before = db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0]
        again = outbox_store.enqueue(
            db.conn, job_type="refresh", input_revision_id=rev, idempotency_key="lease-1"
        )
        assert again == job_id
        assert db.conn.execute("SELECT COUNT(*) FROM projection_job").fetchone()[0] == before
        # Lease floor unchanged.
        try:
            outbox_store.claim(db.conn, "short", lease_seconds=5)
        except ValueError:
            pass
        else:
            raise AssertionError("lease floor no longer enforced")
        # Claim at fixed time; live lease blocks a second owner.
        first = outbox_store.claim(db.conn, "owner-a", now=1000.0)
        assert first is not None and first.job_id == job_id
        assert first.attempt == 1
        assert first.lease_owner == "owner-a"
        assert first.lease_expires_at == 1030.0
        assert outbox_store.claim(db.conn, "owner-b", now=1001.0) is None
        # Expired lease is reclaimable by another owner; attempt bumps.
        second = outbox_store.claim(db.conn, "owner-b", now=1031.0)
        assert second is not None and second.job_id == job_id
        assert second.attempt == 2
        assert second.lease_owner == "owner-b"
        # fail() -> pending with error; reclaim clears the error.
        outbox_store.fail(db.conn, job_id, "owner-b", "boom")
        third = outbox_store.claim(db.conn, "owner-c", now=1032.0)
        assert third is not None and third.attempt == 3
        assert outbox_store.get_job(db.conn, job_id)["error"] is None
        # done jobs are skipped.
        outbox_store.complete(db.conn, job_id, "owner-c")
        job2 = outbox_store.enqueue(
            db.conn, job_type="refresh", input_revision_id=rev, idempotency_key="lease-2"
        )
        fourth = outbox_store.claim(db.conn, "owner-c", now=1033.0)
        assert fourth is not None and fourth.job_id == job2


def test_complete_idempotent_double_complete(tmp_path):
    """P09 backfill (N07A G-1): complete() documents idempotency
    ("already-done returns True") — pin it: double-complete returns True
    twice and the queue stays consistent (job done, nothing else claimable)."""
    db, rev = _open_seeded(tmp_path / "idem.db")
    try:
        job_id = outbox_store.enqueue(
            db.conn, job_type="refresh", input_revision_id=rev,
            idempotency_key="idem-1",
        )
        first = outbox_store.claim(db.conn, "w", now=1000.0)
        assert first is not None and first.job_id == job_id
        assert outbox_store.complete(db.conn, job_id, "w") is True
        assert outbox_store.complete(db.conn, job_id, "w") is True
        assert outbox_store.claim(db.conn, "w", now=1001.0) is None
        assert outbox_store.get_job(db.conn, job_id)["state"] == "done"
    finally:
        db.close()
