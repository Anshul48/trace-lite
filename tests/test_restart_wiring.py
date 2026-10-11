"""P08 acceptance: N06-c restart wiring (N06b RST/U/C, N06a DES/DUR, D4, AMB-N06-6).

EV-1 in-process drills ONLY (close/reopen of Database): these are
pre-qualification self-checks, explicitly NOT reboot proof (N06b EV-3/MAP-18
requires the C-C2d reboot-capable vehicle + N07 legs).

Seam: explicit ``Database.recover_mint()`` immediately after open, before any
mint/serving (U-2/U-6) + clean-shutdown marker written by ``Database.close()``.
``Database.__init__`` does NOT auto-recover (backward compat: existing
``Database(path)`` callers + P03 manual ``init_mint`` keep working; first-init
birth choice stays explicit). See build/designs/N06/DETECTOR.md (AMB-N06-6).
"""

import json

import pytest

from trace_lite.store import Database, restart
from trace_lite.store.mint import (
    MintNotInitialized,
    StaleEpoch,
    advance_epoch,
    counter_row_count,
    mint,
)


def _row_state(conn, epoch, gen):
    row = conn.execute(
        "SELECT state FROM mint_counter WHERE mint_epoch = ? AND generation_id = ?",
        (epoch, gen),
    ).fetchone()
    return row[0] if row is not None else None


def test_clean_restart_no_bump_continues_k(tmp_path):
    """C-2/C-3 leg-2 shape: clean restart issues NO bump; first post-restart
    mint under a previously-LIVE (mint_epoch, G) continues k_next exactly."""
    path = tmp_path / "r.db"
    db = Database(path)
    res = db.recover_mint(expected_birth="birth-1")
    assert res["first_init"] is True and res["epoch"] == 0
    assert mint(db.conn, "gen-A") == (0, 0)
    assert mint(db.conn, "gen-A") == (0, 1)
    db.conn.commit()
    assert restart.marker_path_for(path).exists() is False  # invalidated on open
    db.close()  # clean: writes marker
    assert restart.marker_path_for(path).exists() is True

    db2 = Database(path)
    assert restart.marker_path_for(path).exists() is True  # pre-existing marker
    res2 = db2.recover_mint(expected_birth="birth-1")
    assert res2["first_init"] is False
    assert res2["clean"] is True
    assert res2["epoch"] == 0  # RST-4/C-2: no bump
    assert res2["birth_uuid"] == "birth-1"
    # Review F2: invalidate-on-open on the clean path (a crash after this open
    # but before the next clean close must leave absent => unclean).
    assert restart.marker_path_for(path).exists() is False
    # Leg-2: continued k_next, never reused.
    assert mint(db2.conn, "gen-A") == (0, 2)
    db2.conn.commit()
    assert _row_state(db2.conn, 0, "gen-A") == "LIVE"
    db2.close()


def test_unclean_marker_absent_bumps_and_retires(tmp_path):
    """RST-3/U-2/U-4 leg-3 shape: marker absent (crash-close) => epoch+1 in one
    recovery txn before serving; old rows RETIRED+retained; new mints at new epoch."""
    path = tmp_path / "r.db"
    db = Database(path)
    db.recover_mint(expected_birth="birth-1")
    assert mint(db.conn, "gen-A") == (0, 0)
    assert mint(db.conn, "gen-B") == (0, 0)
    db.conn.commit()
    db.close(clean=False)  # crash: no clean marker
    assert restart.marker_path_for(path).exists() is False

    db2 = Database(path)
    res2 = db2.recover_mint(expected_birth="birth-1")
    assert res2["clean"] is False
    assert res2["epoch"] == 1
    assert res2["prev_epoch"] == 0
    assert res2["birth_uuid"] == "birth-1"  # birth kept (AMB-N06-8: no rotation)
    # D4: old rows RETIRED, retained (never deleted).
    assert counter_row_count(db2.conn) == 2
    assert _row_state(db2.conn, 0, "gen-A") == "RETIRED"
    assert _row_state(db2.conn, 0, "gen-B") == "RETIRED"
    # U-4: post-restart mints carry the new epoch from k=0.
    assert mint(db2.conn, "gen-A") == (1, 0)
    db2.conn.commit()
    assert counter_row_count(db2.conn) == 3
    # Old-epoch validation refused (stale expectation vs fenced epoch 1).
    with pytest.raises(StaleEpoch):
        mint(db2.conn, "gen-A", expected_epoch=0)
    db2.conn.rollback()
    db2.close()


@pytest.mark.parametrize("torn", ["truncate", "garbage", "bad-checksum"])
def test_unclean_torn_marker_bumps(tmp_path, torn):
    """C-1 fail-closed: torn/unverifiable marker => unclean path (bump+retire)."""
    path = tmp_path / "r.db"
    db = Database(path)
    db.recover_mint(expected_birth="birth-1")
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    db.close()  # clean close writes a valid marker
    marker = restart.marker_path_for(path)
    assert marker.exists()

    if torn == "truncate":
        raw = marker.read_bytes()
        marker.write_bytes(raw[: len(raw) // 2])
    elif torn == "garbage":
        marker.write_bytes(b"\x00\xffgarbage-not-json{{{")
    else:  # bad-checksum: valid JSON, wrong checksum nibble
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["sha256"] = "0" * 64
        marker.write_text(json.dumps(payload), encoding="utf-8")

    db2 = Database(path)
    res2 = db2.recover_mint(expected_birth="birth-1")
    assert res2["clean"] is False
    assert res2["epoch"] == 1
    # Invalidate-on-open on the unclean path too (torn marker must be gone).
    assert restart.marker_path_for(path).exists() is False
    assert _row_state(db2.conn, 0, "gen-A") == "RETIRED"
    assert mint(db2.conn, "gen-A") == (1, 0)
    db2.conn.commit()
    db2.close()


def test_birth_mismatch_rejected_no_effects(tmp_path):
    """FAIL-5: wrong expected birth on recover => StaleEpoch with no effects;
    correct retry still sees the clean path (marker untouched by the failure)."""
    path = tmp_path / "r.db"
    db = Database(path)
    db.recover_mint(expected_birth="birth-1")
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    db.close()
    assert restart.marker_path_for(path).exists()

    db2 = Database(path)
    with pytest.raises(StaleEpoch):
        db2.recover_mint(expected_birth="birth-2")
    db2.conn.rollback()
    row = db2.conn.execute("SELECT birth_uuid, epoch FROM mint_epoch").fetchone()
    assert (row[0], int(row[1])) == ("birth-1", 0)
    assert _row_state(db2.conn, 0, "gen-A") == "LIVE"
    # Correct retry: clean path preserved (failed mismatch left no effects).
    res = db2.recover_mint(expected_birth="birth-1")
    assert res["clean"] is True and res["epoch"] == 0
    assert mint(db2.conn, "gen-A") == (0, 1)
    db2.conn.commit()
    db2.close()


def test_mint_before_recover_fails_closed(tmp_path):
    """DUR-4: no mint_epoch row (recover never called) refuses mint/advance;
    recover is the init path, after which mint works."""
    path = tmp_path / "r.db"
    db = Database(path)
    with pytest.raises(MintNotInitialized):
        mint(db.conn, "gen-A")
    with pytest.raises(MintNotInitialized):
        advance_epoch(db.conn, 0)
    res = db.recover_mint(expected_birth="birth-1")
    assert res["first_init"] is True and res["epoch"] == 0
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    db.close()


def test_double_advance_stale_no_effects(tmp_path):
    """U-2 CAS: second advance on the same expected epoch => StaleEpoch with
    no effects (epoch, row states, row count unchanged)."""
    path = tmp_path / "r.db"
    db = Database(path)
    db.recover_mint(expected_birth="birth-1")
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    assert advance_epoch(db.conn, 0, expected_birth="birth-1") == 1
    db.conn.commit()
    before = counter_row_count(db.conn)
    with pytest.raises(StaleEpoch):
        advance_epoch(db.conn, 0, expected_birth="birth-1")
    db.conn.rollback()
    row = db.conn.execute("SELECT epoch FROM mint_epoch").fetchone()
    assert int(row[0]) == 1
    assert counter_row_count(db.conn) == before
    assert _row_state(db.conn, 0, "gen-A") == "RETIRED"
    assert mint(db.conn, "gen-A") == (1, 0)
    db.conn.commit()
    db.close()


def test_birth_generated_once_and_kept(tmp_path):
    """AMB-N06-8 stays OPEN: birth is generated once at first init and kept
    across clean AND unclean restarts (no rotation invented here)."""
    path = tmp_path / "r.db"
    db = Database(path)
    res = db.recover_mint()  # auto-generate
    assert res["first_init"] is True
    birth = res["birth_uuid"]
    assert isinstance(birth, str) and birth
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    db.close()

    db2 = Database(path)
    res2 = db2.recover_mint()
    assert res2["clean"] is True and res2["epoch"] == 0
    assert res2["birth_uuid"] == birth
    assert mint(db2.conn, "gen-A") == (0, 1)
    db2.conn.commit()
    db2.close(clean=False)  # crash

    db3 = Database(path)
    res3 = db3.recover_mint()
    assert res3["clean"] is False and res3["epoch"] == 1
    assert res3["birth_uuid"] == birth  # kept, not rotated
    assert mint(db3.conn, "gen-A") == (1, 0)
    db3.conn.commit()
    db3.close()


def test_recover_bumps_before_serving_after_unclean(tmp_path):
    """U-2/U-6: the bump happens inside recover, before admission. Serving
    (mint) before recover on a reopened unclean DB would still see the old
    epoch — callers must recover first; after recover the new epoch rules."""
    path = tmp_path / "r.db"
    db = Database(path)
    db.recover_mint(expected_birth="birth-1")
    assert mint(db.conn, "gen-A") == (0, 0)
    db.conn.commit()
    db.close(clean=False)

    db2 = Database(path)
    # Hazard demonstration (discarded): pre-recover mint still sees epoch 0.
    assert mint(db2.conn, "gen-A") == (0, 1)
    db2.conn.rollback()
    res = db2.recover_mint(expected_birth="birth-1")
    assert res["clean"] is False and res["epoch"] == 1
    assert mint(db2.conn, "gen-A") == (1, 0)
    db2.conn.commit()
    with pytest.raises(StaleEpoch):
        mint(db2.conn, "gen-A", expected_epoch=0)
    db2.conn.rollback()
    db2.close()
