"""P03 acceptance: MAP-09 mint counter primitives (N06a DES/DUR/FAIL, N06b RST/U/C/A, D4).

Greenfield module ``trace_lite.store.mint``: caller-transaction primitives that
NEVER commit internally, fence-level mint-retirement with row retention (no
deletion per D4/OWN-01), fail-closed exhaustion (no wrap).
"""

import sqlite3

import pytest

from trace_lite.store import Database
from trace_lite.store.mint import (
    SQLITE_INT64_MAX,
    U64_MAX,
    CounterExhausted,
    EpochExhausted,
    MintNotInitialized,
    RetiredCounter,
    StaleEpoch,
    advance_epoch,
    counter_row_count,
    init_mint,
    mint,
)


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "mint.db")
    yield database
    database.close()


@pytest.fixture()
def live(db):
    """Initialized epoch-0 database (committed); returns the Database."""
    assert init_mint(db.conn, "birth-1") == 0
    db.conn.commit()
    return db


def test_k_sequence_0_1_2(live):
    """DES-4: first mint under ABSENT allocates k=0, then strictly increases."""
    assert mint(live.conn, "gen-A") == (0, 0)
    assert mint(live.conn, "gen-A") == (0, 1)
    assert mint(live.conn, "gen-A") == (0, 2)
    live.conn.commit()
    assert mint(live.conn, "gen-A") == (0, 3)


def test_per_generation_isolation(live):
    """DES-4: k sequences are per-(mint_epoch, G), independent across G."""
    assert mint(live.conn, "gen-A") == (0, 0)
    assert mint(live.conn, "gen-B") == (0, 0)
    assert mint(live.conn, "gen-A") == (0, 1)
    assert mint(live.conn, "gen-B") == (0, 1)


def test_retired_row_rejects_mint(live):
    """D4 fence: a RETIRED (mint_epoch, G) row refuses mint (no resurrection)."""
    assert mint(live.conn, "gen-A") == (0, 0)
    live.conn.commit()
    live.conn.execute(
        "UPDATE mint_counter SET state = 'RETIRED'"
        " WHERE mint_epoch = 0 AND generation_id = 'gen-A'"
    )
    live.conn.commit()
    with pytest.raises(RetiredCounter):
        mint(live.conn, "gen-A")


def test_bump_retires_old_rows_and_mints_fresh_epoch(live):
    """U-2/U-4 + D4: advance fence-marks old-epoch rows RETIRED (retained, not
    deleted); post-bump mints carry the new epoch starting at k=0."""
    mint(live.conn, "gen-A")
    mint(live.conn, "gen-B")
    live.conn.commit()
    assert counter_row_count(live.conn) == 2
    assert advance_epoch(live.conn, 0) == 1
    live.conn.commit()
    # Retention: rows still present, all RETIRED (D4: NO deletion).
    assert counter_row_count(live.conn) == 2
    states = {
        r[0]
        for r in live.conn.execute("SELECT DISTINCT state FROM mint_counter").fetchall()
    }
    assert states == {"RETIRED"}
    # New epoch mints fresh.
    assert mint(live.conn, "gen-A") == (1, 0)
    live.conn.commit()
    assert counter_row_count(live.conn) == 3


def test_k_exhaustion_host_max_seeded(live):
    """DES-10/FAIL-1: k_next at the host-integer ceiling fails closed; the
    failed mint consumes nothing (row unchanged)."""
    live.conn.execute(
        "INSERT INTO mint_counter (mint_epoch, generation_id, k_next, state)"
        " VALUES (0, 'gen-E', ?, 'LIVE')",
        (SQLITE_INT64_MAX,),
    )
    live.conn.commit()
    with pytest.raises(CounterExhausted):
        mint(live.conn, "gen-E")
    row = live.conn.execute(
        "SELECT k_next, state FROM mint_counter"
        " WHERE mint_epoch = 0 AND generation_id = 'gen-E'"
    ).fetchone()
    assert int(row[0]) == SQLITE_INT64_MAX
    assert row[1] == "LIVE"


def test_k_no_wrap_past_u64_max(live):
    """F7.3 analog: k_next beyond u64 range fails closed, never wraps."""
    live.conn.execute(
        "INSERT INTO mint_counter (mint_epoch, generation_id, k_next, state)"
        " VALUES (0, 'gen-W', ?, 'LIVE')",
        # Numeric string converts via INTEGER affinity (stored REAL, lossless for
        # 2**64); the value is above u64 max, exercising the u64 branch.
        (str(U64_MAX + 1),),
    )
    live.conn.commit()
    with pytest.raises(CounterExhausted):
        mint(live.conn, "gen-W")


def test_k_last_allocatable_value_then_closed(live):
    """Boundary: k_next one below the host ceiling allows exactly one final
    mint, then fails closed."""
    live.conn.execute(
        "INSERT INTO mint_counter (mint_epoch, generation_id, k_next, state)"
        " VALUES (0, 'gen-L', ?, 'LIVE')",
        (SQLITE_INT64_MAX - 1,),
    )
    live.conn.commit()
    assert mint(live.conn, "gen-L") == (0, SQLITE_INT64_MAX - 1)
    live.conn.commit()
    with pytest.raises(CounterExhausted):
        mint(live.conn, "gen-L")


def test_caller_txn_rollback_consumes_no_k(live):
    """DES-12/DUR-1: an uncommitted mint leaves no durable k consumption;
    rollback restores the exact pre-mint sequence value."""
    assert mint(live.conn, "gen-A") == (0, 0)
    live.conn.commit()
    assert mint(live.conn, "gen-A") == (0, 1)
    live.conn.rollback()
    assert mint(live.conn, "gen-A") == (0, 1)
    live.conn.rollback()
    assert mint(live.conn, "gen-A") == (0, 1)


def test_primitives_never_commit(live):
    """Caller-txn contract: init/mint/advance/counter leave the caller's
    transaction open (no internal commit)."""
    assert live.conn.in_transaction is False
    mint(live.conn, "gen-A")
    assert live.conn.in_transaction is True
    live.conn.rollback()
    advance_epoch(live.conn, 0)
    assert live.conn.in_transaction is True
    live.conn.rollback()
    assert counter_row_count(live.conn) == 0
    assert live.conn.in_transaction is False


def test_cas_mismatch_aborts(live):
    """U-2/RST-3: advance with a stale expected epoch aborts with no effects;
    a second advance on the same expected value also aborts (no double bump)."""
    mint(live.conn, "gen-A")
    live.conn.commit()
    with pytest.raises(StaleEpoch):
        advance_epoch(live.conn, 5)
    live.conn.rollback()
    row = live.conn.execute("SELECT epoch FROM mint_epoch").fetchone()
    assert int(row[0]) == 0
    state = live.conn.execute(
        "SELECT state FROM mint_counter WHERE mint_epoch = 0 AND generation_id = 'gen-A'"
    ).fetchone()
    assert state[0] == "LIVE"
    assert advance_epoch(live.conn, 0) == 1
    live.conn.commit()
    with pytest.raises(StaleEpoch):
        advance_epoch(live.conn, 0)


def test_counter_row_count_surfaced(live):
    """D4 backpressure: row count (LIVE + RETIRED retained rows) is surfaced."""
    assert counter_row_count(live.conn) == 0
    mint(live.conn, "gen-A")
    mint(live.conn, "gen-B")
    assert counter_row_count(live.conn) == 2
    live.conn.commit()
    advance_epoch(live.conn, 0)
    live.conn.commit()
    assert counter_row_count(live.conn) == 2  # retired rows retained, still counted


def test_init_mint_idempotent_same_birth(live):
    """C-2/C-3: re-init with the same birth is a no-op returning current epoch."""
    assert init_mint(live.conn, "birth-1") == 0
    assert advance_epoch(live.conn, 0) == 1
    live.conn.commit()
    assert init_mint(live.conn, "birth-1") == 1


def test_init_mint_birth_mismatch_rejected(live):
    """FAIL-5: a different birth_uuid against an initialized row is rejected."""
    with pytest.raises(StaleEpoch):
        init_mint(live.conn, "birth-2")


def test_mint_fenced_epoch_validation(live):
    """DUR-4/D-R1.4: first-read (birth, epoch) validation; mismatch aborts."""
    assert mint(live.conn, "gen-A", expected_epoch=0, expected_birth="birth-1") == (0, 0)
    with pytest.raises(StaleEpoch):
        mint(live.conn, "gen-B", expected_epoch=3)
    with pytest.raises(StaleEpoch):
        mint(live.conn, "gen-B", expected_birth="other-birth")


def test_mint_without_init_fails_closed(db):
    """DUR-4: no mint_epoch row (no epoch authority) refuses mint."""
    with pytest.raises(MintNotInitialized):
        mint(db.conn, "gen-A")
    with pytest.raises(MintNotInitialized):
        advance_epoch(db.conn, 0)


def test_epoch_exhaustion_fails_closed(live):
    """DES-9/FAIL-2: bump at the host-integer epoch ceiling fails closed."""
    live.conn.execute("UPDATE mint_epoch SET epoch = ?", (SQLITE_INT64_MAX,))
    live.conn.commit()
    with pytest.raises(EpochExhausted):
        advance_epoch(live.conn, SQLITE_INT64_MAX)
    live.conn.rollback()
    row = live.conn.execute("SELECT epoch FROM mint_epoch").fetchone()
    assert int(row[0]) == SQLITE_INT64_MAX


def test_mint_epoch_single_row_enforced(live):
    """DUR-4: the fenced epoch row is single; a second row cannot be inserted."""
    with pytest.raises(sqlite3.IntegrityError):
        live.conn.execute(
            "INSERT INTO mint_epoch (birth_uuid, epoch) VALUES ('birth-X', 9)"
        )


class _FlipOnMintUpdate:
    """Duck-typed connection proxy: delegates everything to the raw
    connection, but applies `flip` immediately before the mint k_next
    UPDATE — deterministically simulating a concurrent txn's committed
    write landing between mint()'s SELECT and UPDATE."""

    def __init__(self, conn, flip):
        self._conn = conn
        self._flip = flip
        self._flipped = False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, params=()):
        if (
            not self._flipped
            and isinstance(sql, str)
            and sql.startswith("UPDATE mint_counter SET k_next")
        ):
            self._flipped = True
            self._flip(self._conn)
        return self._conn.execute(sql, params)


def test_mint_k_race_lost_is_loud_stale_epoch(live):
    """Review O1: a concurrent k advance landing between SELECT and UPDATE
    raises StaleEpoch (retryable in a new txn) — never a silent duplicate k."""

    def flip(conn):
        conn.execute(
            "UPDATE mint_counter SET k_next = 99 WHERE mint_epoch = 0"
            " AND generation_id = 'gen-A'"
        )

    assert mint(live.conn, "gen-A") == (0, 0)
    live.conn.commit()
    with pytest.raises(StaleEpoch):
        mint(_FlipOnMintUpdate(live.conn, flip), "gen-A")
    live.conn.rollback()
    row = live.conn.execute(
        "SELECT k_next, state FROM mint_counter WHERE mint_epoch = 0"
        " AND generation_id = 'gen-A'"
    ).fetchone()
    assert (int(row[0]), row[1]) == (1, "LIVE")  # our mint allocated nothing


def test_mint_retired_under_mint_refused(live):
    """Review O1: a concurrent fence-mark (retire) landing between SELECT and
    UPDATE raises RetiredCounter — no mint into a retired namespace."""

    def flip(conn):
        conn.execute(
            "UPDATE mint_counter SET state = 'RETIRED' WHERE mint_epoch = 0"
            " AND generation_id = 'gen-A'"
        )

    assert mint(live.conn, "gen-A") == (0, 0)
    live.conn.commit()
    with pytest.raises(RetiredCounter):
        mint(_FlipOnMintUpdate(live.conn, flip), "gen-A")


def test_init_mint_never_commits(db):
    """O3: init_mint leaves the caller's transaction open like the rest."""
    assert db.conn.in_transaction is False
    assert init_mint(db.conn, "birth-9") == 0
    assert db.conn.in_transaction is True
    db.conn.rollback()


def test_bad_arguments_rejected(live):
    """Fail closed on empty identities."""
    with pytest.raises(ValueError):
        mint(live.conn, "")
    with pytest.raises(ValueError):
        init_mint(live.conn, "")
