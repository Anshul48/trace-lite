"""MAP-09 mint counter primitives (N06a DES/DUR/FAIL, N06b RST/U/C/A, D4).

Greenfield relative to ``store/generations.py`` (N06a section 9): this module
owns the fenced ``mint_epoch`` row + per-``(mint_epoch, generation_id)``
counter rows. It never imports or modifies ``generations.py``.

Caller-transaction contract: every primitive takes an open
``sqlite3.Connection`` and NEVER commits (or rolls back) internally. The
caller couples the mint to its publication transaction (DES-6/DES-12/DUR-1)
and commits. A caller rollback therefore consumes no ``k``.

Identity: ``(birth_uuid, mint_epoch, G, k)`` (DES-1); ``mint_epoch`` is the
epoch in force at PUBLICATION time (DES-2); ``k`` is a u64 per-(mint_epoch, G)
sequence from 0 (DES-4; first mint allocates 0). Retirement is fence-level
mint-retirement with row RETENTION (D4): epoch advance marks superseded-epoch
rows RETIRED in the same transaction; rows are never deleted here (OWN-01).

Host bound: SQLite INTEGER is signed 64-bit, so the u64 range above
``SQLITE_INT64_MAX`` is unrepresentable in ``k_next``/``epoch`` integer
storage. Both boundaries fail CLOSED with explicit errors (no wrap, no
silent reuse): values past u64 max raise from the u64 check; values whose
successor would exceed the host integer raise from the host check. The
conservative direction (refuse a mint the host cannot durably record) is
required by DUR-1/D-R8.1.

Single-writer assumption (DES-11/P1): the caller serializes publication
transactions (e.g. holds BEGIN IMMEDIATE). Primitives acquire no locks.
Contract violation (overlapping writers) surfaces LOUDLY, never as silent
duplicate allocation: a lost k-race raises ``StaleEpoch`` (retry in a new
txn), a lost first-mint race raises raw ``sqlite3.IntegrityError``.
"""

from __future__ import annotations

import sqlite3

#: u64 value space for k / epoch (DES-4: k is u64 from 0).
U64_MAX = 2**64 - 1

#: Largest INTEGER storable by the SQLite host (signed 64-bit).
SQLITE_INT64_MAX = 2**63 - 1

_LIVE = "LIVE"
_RETIRED = "RETIRED"


class MintError(Exception):
    """Base class for fail-closed mint authority errors."""


class MintNotInitialized(MintError):
    """No ``mint_epoch`` row: no epoch authority (DUR-4). Call ``init_mint``."""


class StaleEpoch(MintError):
    """(birth, epoch) expectation does not match the fenced row (DUR-4/D-R1.4,
    FAIL-5), or an ``advance_epoch`` CAS lost (U-2) — abort, no effects."""


class RetiredCounter(MintError):
    """The ``(mint_epoch, G)`` row is RETIRED: mint refused, no resurrection."""


class CounterExhausted(MintError):
    """No further ``k`` allocatable for this ``(mint_epoch, G)`` (DES-10,
    FAIL-1): u64 ceiling or host-integer ceiling reached. No wrap."""


class EpochExhausted(MintError):
    """Epoch cannot advance (DES-9, FAIL-2/F7.2 class): fail closed, no commits."""


def init_mint(conn: sqlite3.Connection, birth_uuid: str) -> int:
    """Create the epoch-0 row if absent; return the fenced epoch.

    Idempotent for the same ``birth_uuid`` (clean-restart path C-2/C-3: no
    bump, counters untouched). A different birth against an initialized row
    is rejected (FAIL-5). Never commits.
    """
    if not isinstance(birth_uuid, str) or not birth_uuid:
        raise ValueError("birth_uuid must be a non-empty string")
    row = conn.execute("SELECT birth_uuid, epoch FROM mint_epoch LIMIT 1").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO mint_epoch (birth_uuid, epoch) VALUES (?, 0)",
            (birth_uuid,),
        )
        return 0
    if row[0] != birth_uuid:
        raise StaleEpoch(
            f"birth mismatch: fenced {row[0]!r} != expected {birth_uuid!r}"
        )
    return int(row[1])


def mint(
    conn: sqlite3.Connection,
    generation_id: str,
    *,
    expected_epoch: int | None = None,
    expected_birth: str | None = None,
) -> tuple[int, int]:
    """Mint ``(mint_epoch, k)`` for ``generation_id`` in the caller's txn.

    First read is the fenced ``(birth_uuid, epoch)`` row (DUR-4/D-R1.4);
    absent row or expectation mismatch aborts with no effects. Then get (or
    create at k=0) the LIVE row for ``(fenced_epoch, generation_id)``.
    Never commits.
    """
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("generation_id must be a non-empty string")
    row = conn.execute("SELECT birth_uuid, epoch FROM mint_epoch LIMIT 1").fetchone()
    if row is None:
        raise MintNotInitialized("mint_epoch row absent: call init_mint first")
    birth = row[0]
    epoch = int(row[1])
    if expected_birth is not None and expected_birth != birth:
        raise StaleEpoch(f"birth mismatch: fenced {birth!r} != expected {expected_birth!r}")
    if expected_epoch is not None and int(expected_epoch) != epoch:
        raise StaleEpoch(f"epoch mismatch: fenced {epoch} != expected {expected_epoch}")
    crow = conn.execute(
        "SELECT k_next, state FROM mint_counter WHERE mint_epoch = ? AND generation_id = ?",
        (epoch, generation_id),
    ).fetchone()
    if crow is None:
        conn.execute(
            "INSERT INTO mint_counter (mint_epoch, generation_id, k_next, state)"
            " VALUES (?, ?, 1, 'LIVE')",
            (epoch, generation_id),
        )
        return (epoch, 0)
    state = crow[1]
    if state != _LIVE:
        raise RetiredCounter(
            f"counter ({epoch}, {generation_id!r}) is {state}: mint refused"
        )
    k_next = int(crow[0])
    if k_next < 0 or k_next > U64_MAX:
        raise CounterExhausted(
            f"counter ({epoch}, {generation_id!r}) has k_next={k_next} outside u64: no wrap"
        )
    new_next = k_next + 1
    if new_next > SQLITE_INT64_MAX:
        # The k value itself may be a valid u64, but its successor is
        # unrepresentable on this host: allocating it would leave no durable
        # post-state, so fail closed WITHOUT allocating (DUR-1/D-R8.1).
        raise CounterExhausted(
            f"counter ({epoch}, {generation_id!r}) at host-integer ceiling: no wrap"
        )
    cur = conn.execute(
        "UPDATE mint_counter SET k_next = ?"
        " WHERE mint_epoch = ? AND generation_id = ? AND k_next = ? AND state = 'LIVE'",
        (new_next, epoch, generation_id, k_next),
    )
    if cur.rowcount != 1:
        # Lost a k-race (contract-violating overlapping writer) or the row
        # flipped state under us: re-read to raise the precise error. Never
        # a silent duplicate allocation (review O1).
        state_now = conn.execute(
            "SELECT state FROM mint_counter WHERE mint_epoch = ? AND generation_id = ?",
            (epoch, generation_id),
        ).fetchone()
        if state_now is None or state_now[0] != _LIVE:
            raise RetiredCounter(
                f"counter ({epoch}, {generation_id!r}) retired under mint: refused"
            )
        raise StaleEpoch(
            f"k-race lost for ({epoch}, {generation_id!r}): retry in a new txn"
        )
    return (epoch, k_next)


def advance_epoch(
    conn: sqlite3.Connection,
    expected_epoch: int,
    *,
    expected_birth: str | None = None,
) -> int:
    """Bump the fenced epoch by one (unclean-restart path U-2/O4) via CAS.

    Compare-and-swap on ``expected_epoch`` (rowcount-checked: a lost race or
    stale expectation raises ``StaleEpoch`` with no effects), then fence-mark
    the superseded epoch's LIVE rows RETIRED — same transaction, rows
    RETAINED, never deleted (D4). Returns the new epoch. Never commits.
    """
    row = conn.execute("SELECT birth_uuid, epoch FROM mint_epoch LIMIT 1").fetchone()
    if row is None:
        raise MintNotInitialized("mint_epoch row absent: call init_mint first")
    birth = row[0]
    epoch = int(row[1])
    if expected_birth is not None and expected_birth != birth:
        raise StaleEpoch(f"birth mismatch: fenced {birth!r} != expected {expected_birth!r}")
    if epoch != int(expected_epoch):
        raise StaleEpoch(f"epoch mismatch: fenced {epoch} != expected {expected_epoch}")
    if epoch >= U64_MAX or epoch >= SQLITE_INT64_MAX:
        raise EpochExhausted(f"epoch {epoch} cannot advance: fail closed, no commits")
    new_epoch = epoch + 1
    cur = conn.execute(
        "UPDATE mint_epoch SET epoch = ? WHERE epoch = ?",
        (new_epoch, int(expected_epoch)),
    )
    if cur.rowcount != 1:
        raise StaleEpoch(
            f"CAS lost: expected epoch {expected_epoch} no longer fenced"
        )
    conn.execute(
        "UPDATE mint_counter SET state = 'RETIRED' WHERE mint_epoch = ? AND state = 'LIVE'",
        (int(expected_epoch),),
    )
    return new_epoch


def counter_row_count(conn: sqlite3.Connection) -> int:
    """Total ``mint_counter`` rows (LIVE + retained RETIRED) for D4
    backpressure accounting. Read-only; never commits."""
    row = conn.execute("SELECT COUNT(*) FROM mint_counter").fetchone()
    return int(row[0])
