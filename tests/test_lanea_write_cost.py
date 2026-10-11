"""Lane A write-cost regression tests (additive; owns no other test file).

Pins the incremental corpus-holon maintenance in ``filing/holon.py`` and
the revision-scoped job index in ``source/coverage_state.py``:

- fold path keeps accumulators/count/digest/version-history exact vs the
  full-rebuild oracle (drift + integrity checks);
- descriptor/representatives carry forward on the fold path and refresh
  exactly via ``refresh_derived``;
- the digest gate falls back to the exact full recompute after
  out-of-band membership change (cascaded atom delete) and heals;
- small holons stay on the exact full path (threshold boundary);
- concurrent adds from two writers stay membership-complete and heal;
- per-ingest grouping encodes O(k) member texts, not O(corpus).
"""
from __future__ import annotations

import threading
from pathlib import Path

from trace_lite.api.app import ingest_note
from trace_lite.filing import HolonStore
from trace_lite.filing.engine import FilingEngine
from trace_lite.filing.holon import FULL_REFRESH_LIMIT
from trace_lite.filing import holon as holon_mod
from trace_lite.filing.taxonomy import Taxonomy
from trace_lite.source import coverage_state
from trace_lite.store.database import Database


def _stack(tmp_path: Path, name: str = "t.db"):
    db = Database(tmp_path / name, check_same_thread=False)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    engine.bind_database(db)
    store = HolonStore(db.conn)
    return db, taxonomy, engine, store


def _atoms(db: Database, n: int, prefix: str = "doc") -> list[int]:
    ids = []
    for i in range(n):
        ids.append(db.insert_atom(
            f"{prefix}-{i}.md",
            f"# Note {i} {prefix}\n\nBatching improves throughput under low "
            f"contention. Keyword{i % 7} serial {1000 + i}.\n"))
    return ids


def _live_ids(store: HolonStore, hid: str) -> list[int]:
    return [int(r[0]) for r in store.conn.execute(
        "SELECT m.atom_id FROM holon_members m JOIN atom a ON a.id = m.atom_id"
        " WHERE m.holon_id = ? ORDER BY m.position", (hid,)).fetchall()]


def test_fold_matches_full_rebuild_oracle(tmp_path):
    """100 members via successive adds: drift/digest/count/history exact."""
    db, _tx, _en, store = _stack(tmp_path)
    try:
        ids = _atoms(db, 100)
        hid = store.create_holon(ids[:2], kind="thematic_cluster")
        for aid in ids[2:]:
            store.add_members(hid, [aid])
        assert store.check_drift(hid)["ok"] is True
        assert store.check_holon_integrity(hid)["ok"] is True
        holon = store.get_holon(hid)
        assert holon.member_count == 100
        history = store.get_version_history(hid)
        assert [v["version"] for v in history] == list(range(1, 100))
        assert [v["member_count"] for v in history] == list(range(2, 101))
        assert len({v["member_digest"] for v in history}) == 99
    finally:
        db.close()


def test_fold_carries_descriptor_until_refresh(tmp_path):
    """Descriptor/reps freeze on the fold path; refresh_derived heals exact."""
    db, _tx, _en, store = _stack(tmp_path)
    try:
        ids = _atoms(db, 80)
        hid = store.create_holon(ids[:2], kind="thematic_cluster")
        for aid in ids[2:70]:
            store.add_members(hid, [aid])
        carried = store.get_holon(hid).descriptor
        assert carried != ""
        for aid in ids[70:]:
            store.add_members(hid, [aid])
        assert store.get_holon(hid).descriptor == carried
        # Exact reference: same member set through the full path only.
        ref = store.create_holon(list(ids), kind="thematic_cluster")
        try:
            want_desc = store.get_holon(ref).descriptor
            want_reps = set(store.get_holon(ref).representatives)
        finally:
            store.delete_holon(ref)
        store.refresh_derived(hid)
        assert store.get_holon(hid).descriptor == want_desc
        assert set(store.get_holon(hid).representatives) == want_reps
        assert store.check_holon_integrity(hid)["ok"] is True
    finally:
        db.close()


def test_gate_miss_heals_after_cascade_delete(tmp_path):
    """Atom delete cascades membership; next add falls back and heals exact."""
    db, _tx, _en, store = _stack(tmp_path)
    try:
        ids = _atoms(db, 72)
        hid = store.create_holon(ids[:2], kind="thematic_cluster")
        for aid in ids[2:70]:
            store.add_members(hid, [aid])
        victim = ids[10]
        db.conn.execute("DELETE FROM atom WHERE id = ?", (victim,))
        db.conn.commit()
        assert FULL_REFRESH_LIMIT < 70  # fold path was active pre-delete
        store.add_members(hid, [ids[70]])
        live = _live_ids(store, hid)
        assert len(live) == 70 and victim not in live
        assert store.check_drift(hid)["ok"] is True
        assert store.check_holon_integrity(hid)["ok"] is True
        assert store.get_holon(hid).member_count == 70
        ref = store.create_holon(live, kind="thematic_cluster")
        try:
            assert store.get_holon(hid).descriptor == \
                store.get_holon(ref).descriptor
        finally:
            store.delete_holon(ref)
    finally:
        db.close()


def test_small_holons_stay_exact(tmp_path):
    """Below the threshold every add re-derives descriptor/reps exactly."""
    db, _tx, _en, store = _stack(tmp_path)
    try:
        ids = _atoms(db, 10)
        hid = store.create_holon(ids[:2], kind="thematic_cluster")
        for aid in ids[2:]:
            store.add_members(hid, [aid])
        ref = store.create_holon(list(ids), kind="thematic_cluster")
        try:
            assert store.get_holon(hid).descriptor == \
                store.get_holon(ref).descriptor
            assert set(store.get_holon(hid).representatives) == \
                set(store.get_holon(ref).representatives)
        finally:
            store.delete_holon(ref)
    finally:
        db.close()


def test_concurrent_adds_membership_safe_and_heal(tmp_path):
    """Two writers racing disjoint adds: membership complete, state heals."""
    main_db, _tx, _en, store = _stack(tmp_path, "race.db")
    try:
        ids = _atoms(main_db, 91)
        hid = store.create_holon(ids[:2], kind="thematic_cluster")
        for aid in ids[2:70]:
            store.add_members(hid, [aid])
        barrier = threading.Barrier(3)
        errors: list[BaseException] = []

        def hammer(db_path: Path, members: list[int]) -> None:
            db = Database(db_path, check_same_thread=False)
            try:
                st = HolonStore(db.conn)
                barrier.wait(timeout=30)
                for aid in members:
                    st.add_members(hid, [aid])
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)
            finally:
                db.close()

        db_path = tmp_path / "race.db"
        t1 = threading.Thread(target=hammer, args=(db_path, ids[70:80]))
        t2 = threading.Thread(target=hammer, args=(db_path, ids[80:90]))
        t1.start()
        t2.start()
        barrier.wait(timeout=30)
        t1.join(timeout=120)
        t2.join(timeout=120)
        assert not t1.is_alive() and not t2.is_alive()
        assert not errors, f"worker errors: {errors!r}"
        live = set(_live_ids(store, hid))
        assert set(ids[:90]) <= live  # every raced insert committed
        store.add_members(hid, [ids[90]])  # heal pass after the race
        assert store.check_drift(hid)["ok"] is True
        assert store.check_holon_integrity(hid)["ok"] is True
        assert store.get_holon(hid).member_count == 91
    finally:
        main_db.close()


def test_job_rev_index_present_and_used(tmp_path):
    """Drain-path ensure creates + uses the revision-scoped job index."""
    db = Database(tmp_path / "idx.db")
    try:
        coverage_state.ensure_coverage_schema(db.conn)
        names = {r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
        assert "idx_cov_job_rev" in names
        plan = "\n".join(r[-1] for r in db.conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM projection_job"
            " WHERE input_revision_id = 'x' AND state = 'done'").fetchall())
        assert "idx_cov_job_rev" in plan
    finally:
        db.close()


def _note(i: int) -> str:
    return (f"# Note {i}\n\n#tag{i % 9} #tag{(i + 1) % 9}\n\nBatching improves "
            f"throughput under low contention while WAL mode allows one "
            f"writer with concurrent readers {i}.\n")


def test_online_grouping_encodes_bounded_texts(tmp_path, monkeypatch):
    """Notes 100-149 encode O(k) member texts in holon.py, not O(corpus)."""
    from trace_lite.source import project as projection

    db, taxonomy, engine, _store = _stack(tmp_path, "scale.db")
    try:
        real = holon_mod.text_vector
        calls: list[int] = []

        def counting(text, dim=128):
            calls.append(1)
            return real(text, dim)

        monkeypatch.setattr(holon_mod, "text_vector", counting)
        # G1: writes queue; grouping runs on the worker-equivalent drain.
        # Draining per note preserves the measured per-note grouping cost.
        for i in range(100):
            ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", _note(i))
            projection.drain_outbox(db, None, owner="lanea-test")
        del calls[:]
        for i in range(100, 150):
            ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", _note(i))
            projection.drain_outbox(db, None, owner="lanea-test")
        # One fold-add encodes exactly its new member; 150 = 3x headroom.
        # Pre-fix full recompute would issue ~2 x 125 x 50 ~= 12.5k calls.
        assert len(calls) < 150, f"grouping re-encodes corpus: {len(calls)}"
    finally:
        db.close()


def test_encoding_counter_detects_full_recompute(tmp_path, monkeypatch):
    """The counting harness above is not vacuous: forced full path explodes."""
    from trace_lite.source import project as projection

    db, taxonomy, engine, _store = _stack(tmp_path, "scale2.db")
    try:
        real = holon_mod.text_vector
        calls: list[int] = []

        def counting(text, dim=128):
            calls.append(1)
            return real(text, dim)

        monkeypatch.setattr(holon_mod, "text_vector", counting)
        monkeypatch.setattr(holon_mod, "FULL_REFRESH_LIMIT", 10 ** 9)
        for i in range(100):
            ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", _note(i))
            projection.drain_outbox(db, None, owner="lanea-test")
        del calls[:]
        for i in range(100, 120):
            ingest_note(db, taxonomy, engine, f"notes/doc{i:05d}.md", _note(i))
            projection.drain_outbox(db, None, owner="lanea-test")
        assert len(calls) > 2000, f"harness blind: {len(calls)}"
    finally:
        db.close()
