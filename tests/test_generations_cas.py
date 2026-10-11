"""P04 contract: atomic generation activation via compare-and-swap.

(REUSE-VERIFY.md A2 + N11b-direction.md S3, atomicity half ONLY.)

- activate_generation(..., expected_active_id=X): one atomic UPDATE..WHERE
  guarded on X still being the kind's active generation; rowcount decides.
  Match -> True; mismatch -> False with the DB unchanged (no exception).
- Concurrent conditional activations from a single expected base: exactly
  one winner, single-active invariant holds.
- guarded_activate(allow=False): zero-write stale skip (preserved); the
  allow=True path goes through the CAS activation internally.
- Unconditional activation + unknown-generation KeyError (preserved).

Out of scope (recorded, not built): pin/retention table, full-triple
generation identity, content-derived ids.
"""

from __future__ import annotations

import threading

import pytest

from trace_lite.store import generations as gen_store
from trace_lite.store.database import Database

KIND = "filing"


def _snapshot(conn):
    """Full projection_generation table as comparable tuples."""
    rows = conn.execute(
        "SELECT generation_id, kind, config_hash, watermark, manifest_hash,"
        " active, created_at FROM projection_generation ORDER BY generation_id"
    ).fetchall()
    return [tuple(r) for r in rows]


def _seed(path, kind=KIND):
    """Three same-kind generations with the first one active."""
    db = Database(path)
    a = gen_store.create_generation(db.conn, kind=kind, config_hash="cfg-a", watermark="a")
    b = gen_store.create_generation(db.conn, kind=kind, config_hash="cfg-b", watermark="b")
    c = gen_store.create_generation(db.conn, kind=kind, config_hash="cfg-c", watermark="c")
    gen_store.activate_generation(db.conn, a)
    assert gen_store.get_active(db.conn, kind)["generation_id"] == a
    return db, a, b, c


def _race_worker(barrier, results, i, path, target, expected):
    # Each thread opens its own Database (one instance per thread; sqlite
    # connections are thread-affine). The barrier fires after all opens so
    # only the conditional activate() calls race.
    db = Database(path)
    try:
        barrier.wait(timeout=30)
        results[i] = gen_store.activate_generation(
            db.conn, target, expected_active_id=expected
        )
    except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
        results[i] = exc
    finally:
        db.close()


def test_concurrent_activation_race_exactly_one_winner(tmp_path):
    """Racing conditional activations from one base: exactly one wins."""
    path = tmp_path / "gen-race.db"
    db, a, b, c = _seed(path)
    nthreads = 6
    try:
        for round_no in range(10):
            base = gen_store.get_active(db.conn, KIND)["generation_id"]
            others = [g for g in (a, b, c) if g != base]
            targets = [others[i % len(others)] for i in range(nthreads)]
            barrier = threading.Barrier(nthreads)
            results: list = [None] * nthreads
            threads = [
                threading.Thread(
                    target=_race_worker,
                    args=(barrier, results, i, str(path), targets[i], base),
                )
                for i in range(nthreads)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
                assert not t.is_alive(), f"round {round_no}: worker thread hung"
            errors = [r for r in results if isinstance(r, BaseException)]
            assert errors == [], f"round {round_no}: {errors!r}"
            assert results.count(True) == 1, f"round {round_no}: {results!r}"
            assert results.count(False) == nthreads - 1, f"round {round_no}: {results!r}"
            winner = targets[results.index(True)]
            assert gen_store.get_active(db.conn, KIND)["generation_id"] == winner
            n_active = db.conn.execute(
                "SELECT COUNT(*) FROM projection_generation WHERE kind = ? AND active = 1",
                (KIND,),
            ).fetchone()[0]
            assert n_active == 1, f"round {round_no}: {n_active} active rows"
    finally:
        db.close()


def test_cas_mismatch_returns_false_db_unchanged(tmp_path):
    """Lost race -> False, no exception, full-table state unchanged, no open txn."""
    db, a, b, c = _seed(tmp_path / "mismatch.db")
    try:
        before = _snapshot(db.conn)
        # `c` was never active: stale expectation loses.
        assert gen_store.activate_generation(db.conn, b, expected_active_id=c) is False
        assert _snapshot(db.conn) == before
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == a
        assert db.conn.in_transaction is False
        # Unknown expected id is a mismatch, not an error.
        assert (
            gen_store.activate_generation(db.conn, b, expected_active_id="no-such") is False
        )
        assert _snapshot(db.conn) == before
        # Matching expectation succeeds.
        assert gen_store.activate_generation(db.conn, b, expected_active_id=a) is True
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == b
        # Self-activation against itself succeeds (no-op, still True).
        assert gen_store.activate_generation(db.conn, b, expected_active_id=b) is True
        # Now `a` is stale: activating `c` against it loses, DB unchanged.
        mid = _snapshot(db.conn)
        assert gen_store.activate_generation(db.conn, c, expected_active_id=a) is False
        assert _snapshot(db.conn) == mid
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == b
    finally:
        db.close()


def test_cas_mismatch_inside_caller_txn_writes_nothing(tmp_path):
    """commit=False mismatch: caller's txn left open and untouched."""
    db, a, b, c = _seed(tmp_path / "txn.db")
    try:
        before = _snapshot(db.conn)
        gid = gen_store.create_generation(
            db.conn, kind=KIND, config_hash="cfg-d", watermark="d", commit=False
        )
        assert db.conn.in_transaction
        assert (
            gen_store.activate_generation(
                db.conn, b, expected_active_id=c, commit=False
            )
            is False
        )
        assert db.conn.in_transaction  # caller still owns the txn
        db.conn.rollback()
        assert _snapshot(db.conn) == before
        assert gid not in {r["generation_id"] for r in gen_store.list_generations(db.conn, KIND)}
    finally:
        db.close()


def test_stale_output_zero_write_skip_preserved(tmp_path):
    """guarded_activate(allow=False): exact skip dict, zero DB writes."""
    db, a, b, c = _seed(tmp_path / "skip.db")
    try:
        before = _snapshot(db.conn)
        changes_before = db.conn.total_changes
        skipped = gen_store.guarded_activate(
            db.conn, b, allow=False, reason="superseded revision"
        )
        assert skipped == {
            "activated": False,
            "generation_id": b,
            "reason": "superseded revision",
        }
        assert db.conn.total_changes == changes_before  # SELECT-only path
        assert _snapshot(db.conn) == before
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == a
    finally:
        db.close()


def test_normal_activate_deactivate_flow_preserved(tmp_path):
    """Unconditional activate returns True; single-active; KeyError kept."""
    db = Database(tmp_path / "flow.db")
    try:
        other = gen_store.create_generation(
            db.conn, kind="other", config_hash="o", watermark="o"
        )
        assert gen_store.activate_generation(db.conn, other) is True
        a = gen_store.create_generation(db.conn, kind=KIND, config_hash="a", watermark="a")
        b = gen_store.create_generation(db.conn, kind=KIND, config_hash="b", watermark="b")
        assert gen_store.get_active(db.conn, KIND) is None
        assert gen_store.activate_generation(db.conn, a) is True
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == a
        assert gen_store.activate_generation(db.conn, b) is True
        got = {
            r["generation_id"]: r["active"]
            for r in gen_store.list_generations(db.conn, KIND)
        }
        assert got == {a: 0, b: 1}
        # Other kinds untouched.
        assert gen_store.get_active(db.conn, "other")["generation_id"] == other
        # Self-activation is a no-op success.
        assert gen_store.activate_generation(db.conn, b) is True
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == b
        # Unknown generation still raises (never False).
        with pytest.raises(KeyError):
            gen_store.activate_generation(db.conn, "no-such-generation")
        with pytest.raises(KeyError):
            gen_store.activate_generation(
                db.conn, "no-such-generation", expected_active_id=a
            )
        with pytest.raises(KeyError):
            gen_store.guarded_activate(db.conn, "no-such-generation", allow=True)
        with pytest.raises(KeyError):
            gen_store.guarded_activate(db.conn, "no-such-generation", allow=False)
        # guarded allow=True still activates through the CAS path.
        out = gen_store.guarded_activate(db.conn, a, allow=True)
        assert out == {"activated": True, "generation_id": a, "reason": ""}
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == a
    finally:
        db.close()


def test_guarded_activate_cas_path(tmp_path):
    """guarded_activate threads the expectation through the CAS activation."""
    db, a, b, c = _seed(tmp_path / "guarded.db")
    try:
        before = _snapshot(db.conn)
        lost = gen_store.guarded_activate(db.conn, b, allow=True, expected_active_id=c)
        assert lost == {"activated": False, "generation_id": b, "reason": ""}
        assert _snapshot(db.conn) == before
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == a
        won = gen_store.guarded_activate(
            db.conn, b, allow=True, expected_active_id=a, reason="current"
        )
        assert won == {"activated": True, "generation_id": b, "reason": "current"}
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == b
        # allow=False short-circuits before any CAS write even with expected set.
        changes_before = db.conn.total_changes
        skipped = gen_store.guarded_activate(
            db.conn, c, allow=False, expected_active_id=b, reason="stale"
        )
        assert skipped == {"activated": False, "generation_id": c, "reason": "stale"}
        assert db.conn.total_changes == changes_before
        assert gen_store.get_active(db.conn, KIND)["generation_id"] == b
    finally:
        db.close()
