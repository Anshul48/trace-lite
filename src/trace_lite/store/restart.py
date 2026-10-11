"""Clean-shutdown marker detector (AMB-N06-6, N06b C-1/RST/U/C).

Sidecar-file mechanism: ``<db-path>.mint-shutdown`` (JSON, checksummed).
DB row is the authority (DUR-4); the marker is a hint only. Any
absent/torn/unverifiable marker fails CLOSED to the unclean path (C-1):
the caller bumps (read-current-write-plus-1, U-2), never fabricates.

Discipline (full argument: build/designs/N06/DETECTOR.md):
- Clean ``Database.close()`` writes the marker atomically
  (tmp + fsync + os.replace + fsync dir) binding (birth, epoch).
- ``Database.recover_mint()`` checks the marker against the fenced row,
  then INVALIDATES it (unlink) before serving, so a crash after open but
  before the next clean close leaves absent => unclean.
- Torn content (truncate/garbage/bad checksum), version/flag/field errors,
  or birth/epoch mismatch vs the fenced row => unclean, never clean.

Single-writer assumption (DES-11/P1): callers serialize opens/recovers;
concurrent openers racing marker unlink/write get at worst an extra
fail-closed bump, never silent reuse (CAS in ``mint.advance_epoch``).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

#: Marker format version (bumped only by a new adopted design).
MARKER_VERSION = 1

#: Sidecar suffix: distinct from SQLite ``-wal``/``-shm`` companions.
MARKER_SUFFIX = ".mint-shutdown"

_CHECKSUM_DOMAIN = "mint-shutdown-v1"


def marker_path_for(db_path: str | Path) -> Path:
    """Sidecar marker path for a database file path."""
    return Path(str(db_path) + MARKER_SUFFIX)


def compute_checksum(birth_uuid: str, epoch: int) -> str:
    """Hex checksum binding (birth, epoch) to the marker (torn-write detector)."""
    payload = f"{_CHECKSUM_DOMAIN}:{birth_uuid}:{int(epoch)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_clean_marker(db_path: str | Path, birth_uuid: str, epoch: int) -> None:
    """Atomically write a valid clean-shutdown marker (clean-close path).

    Raises ``ValueError`` for bad arguments, ``OSError`` for I/O failure
    (caller decides: ``Database.close`` swallows I/O failure fail-closed —
    absent marker => next open takes the unclean path with an extra bump).
    """
    if not isinstance(birth_uuid, str) or not birth_uuid:
        raise ValueError("birth_uuid must be a non-empty string")
    epoch = int(epoch)
    if epoch < 0:
        raise ValueError("epoch must be >= 0")
    marker = marker_path_for(db_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "v": MARKER_VERSION,
        "birth_uuid": birth_uuid,
        "epoch": epoch,
        "clean": True,
        "sha256": compute_checksum(birth_uuid, epoch),
    }
    text = json.dumps(doc, sort_keys=True)
    tmp_name = f"{marker.name}.tmp.{os.getpid()}"
    tmp_path = marker.with_name(tmp_name)
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1  # ownership moved to fh
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(marker))
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
    # Fsync the directory so the rename itself survives a crash.
    try:
        dir_fd = os.open(str(marker.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def check_clean_marker(
    db_path: str | Path, expected_birth: str, expected_epoch: int
) -> tuple[bool, str]:
    """Verify the marker against the fenced (birth, epoch).

    Returns ``(is_clean, reason)``; ``reason`` is ``"clean"`` iff clean,
    else one of ``absent``/``read-error``/``json-error``/``version-mismatch``/
    ``clean-flag``/``field-error``/``checksum``/``birth-mismatch``/
    ``epoch-mismatch``. Never raises for torn content (fail-closed False).
    """
    marker = marker_path_for(db_path)
    try:
        raw = marker.read_bytes()
    except FileNotFoundError:
        return (False, "absent")
    except OSError:
        return (False, "read-error")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return (False, "json-error")
    if not isinstance(doc, dict):
        return (False, "json-error")
    if doc.get("v") != MARKER_VERSION:
        return (False, "version-mismatch")
    if doc.get("clean") is not True:
        return (False, "clean-flag")
    birth = doc.get("birth_uuid")
    epoch = doc.get("epoch")
    checksum = doc.get("sha256")
    if (
        not isinstance(birth, str)
        or not birth
        or not isinstance(epoch, int)
        or epoch < 0
        or not isinstance(checksum, str)
    ):
        return (False, "field-error")
    if checksum != compute_checksum(birth, epoch):
        return (False, "checksum")
    if birth != expected_birth:
        return (False, "birth-mismatch")
    if int(epoch) != int(expected_epoch):
        return (False, "epoch-mismatch")
    return (True, "clean")


def invalidate_marker(db_path: str | Path) -> None:
    """Remove the marker (open-path invalidation + crash-close simulation).

    Missing marker is a no-op. I/O errors are swallowed: a stale marker left
    behind mismatches the (possibly bumped) fenced epoch on the next open and
    fails closed to unclean (extra bump, never reuse).
    """
    try:
        marker_path_for(db_path).unlink(missing_ok=True)
    except OSError:
        pass
