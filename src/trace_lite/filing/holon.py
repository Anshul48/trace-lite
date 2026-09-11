"""Document-local holons: contiguous A-B-A chunk groups with intra-doc boundaries."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass

HOLON_SCHEMA = """
CREATE TABLE IF NOT EXISTS holons (
    holon_id   TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    tier       TEXT NOT NULL DEFAULT 'nav',
    start_byte INTEGER NOT NULL,
    end_byte   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS holon_members (
    holon_id TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    atom_id  INTEGER NOT NULL REFERENCES atom(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (holon_id, atom_id)
);
CREATE INDEX IF NOT EXISTS idx_holon_doc ON holons(doc_id);
"""


class HolonError(ValueError):
    """Raised when a holon spans documents or breaks contiguity."""


@dataclass(frozen=True)
class Holon:
    holon_id: str
    doc_id: str
    tier: str
    atom_ids: tuple[int, ...]
    start_byte: int
    end_byte: int


class HolonStore:
    """Groups contiguous same-document atoms into semantic holon spans."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.executescript(HOLON_SCHEMA)

    def create_holon(self, atom_ids: list[int], tier: str = "nav") -> str:
        if len(atom_ids) < 2:
            raise HolonError("a holon needs at least 2 atoms")
        rows = []
        for aid in atom_ids:
            row = self.conn.execute(
                "SELECT id, doc_id, start_byte, end_byte FROM atom WHERE id = ?", (aid,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown atom {aid}")
            rows.append(row)
        doc_ids = {r[1] for r in rows}
        if len(doc_ids) != 1:
            raise HolonError(f"holon must stay intra-document, saw docs {sorted(doc_ids)}")
        ordered = sorted(rows, key=lambda r: (r[2], r[0]))
        ids = [r[0] for r in ordered]
        doc_id = ordered[0][1]
        # Contiguity in per-document order: member ids must form one unbroken
        # slice of the doc's atom sequence (global rowids interleave across docs).
        doc_order = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM atom WHERE doc_id = ? ORDER BY start_byte, id", (doc_id,)
            ).fetchall()
        ]
        positions = sorted(doc_order.index(aid) for aid in ids)
        if positions != list(range(positions[0], positions[0] + len(ids))):
            raise HolonError("holon atoms must be contiguous within the document")
        holon_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO holons (holon_id, doc_id, tier, start_byte, end_byte) VALUES (?, ?, ?, ?, ?)",
            (holon_id, doc_id, tier, ordered[0][2], ordered[-1][3]),
        )
        self.conn.executemany(
            "INSERT INTO holon_members (holon_id, atom_id, position) VALUES (?, ?, ?)",
            [(holon_id, aid, pos) for pos, aid in enumerate(ids)],
        )
        self.conn.commit()
        return holon_id

    def get_holon(self, holon_id: str) -> Holon:
        row = self.conn.execute(
            "SELECT holon_id, doc_id, tier, start_byte, end_byte FROM holons WHERE holon_id = ?",
            (holon_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown holon {holon_id}")
        members = tuple(
            r[0]
            for r in self.conn.execute(
                "SELECT atom_id FROM holon_members WHERE holon_id = ? ORDER BY position",
                (holon_id,),
            ).fetchall()
        )
        return Holon(row[0], row[1], row[2], members, row[3], row[4])

    def holons_of_doc(self, doc_id: str) -> list[Holon]:
        return [
            self.get_holon(r[0])
            for r in self.conn.execute(
                "SELECT holon_id FROM holons WHERE doc_id = ? ORDER BY start_byte", (doc_id,)
            ).fetchall()
        ]
