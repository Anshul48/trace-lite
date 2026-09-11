"""Active WAL Checkpoint Governor.

Issues ``PRAGMA wal_checkpoint(PASSIVE)`` every ``threshold`` committed
documents. Must run strictly outside open write transactions (post-commit):
calling it mid-transaction raises ``database table is locked`` (CRIT-04).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class CheckpointRecord:
    total_committed: int
    busy: int
    log_frames: int
    checkpointed_frames: int


@dataclass
class WalGovernor:
    """Counts committed documents; checkpoints post-commit at each threshold."""

    threshold: int = 5000
    committed_since_checkpoint: int = 0
    total_committed: int = 0
    history: list[CheckpointRecord] = field(default_factory=list)

    def note_commit(self, connection: sqlite3.Connection, doc_count: int = 1) -> list[CheckpointRecord]:
        """Record a post-commit document batch; checkpoint when due. Returns new records."""
        self.committed_since_checkpoint += doc_count
        self.total_committed += doc_count
        fired: list[CheckpointRecord] = []
        while self.committed_since_checkpoint >= self.threshold:
            self.committed_since_checkpoint -= self.threshold
            fired.append(self.checkpoint(connection))
        return fired

    def checkpoint(self, connection: sqlite3.Connection) -> CheckpointRecord:
        """Run PASSIVE checkpoint on an idle (post-commit) connection."""
        row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        busy, log_frames, checkpointed = int(row[0]), int(row[1]), int(row[2])
        record = CheckpointRecord(
            total_committed=self.total_committed,
            busy=busy,
            log_frames=log_frames,
            checkpointed_frames=checkpointed,
        )
        self.history.append(record)
        log.info(
            "wal_checkpoint(PASSIVE): busy=%d log=%d checkpointed=%d total_committed=%d",
            busy, log_frames, checkpointed, self.total_committed,
        )
        return record
