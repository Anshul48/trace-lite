"""Cross-source holons: overlapping subgraph grouping with reconciliation.

R05 refactor (contract TL-QN-2026-09-12.1, ARCHITECTURE §5–§6). The legacy
intra-document-only constraint is replaced by kind-aware validation:

- ``discourse_span``: ordered contiguous spans within one source (legacy
  contract preserved byte-for-byte for this kind).
- ``thematic_cluster``: overlapping cross-source members; topic relation only.
- ``claim_cluster`` (alias ``argument``): cross-source evidence with explicit
  roles (premise/claim/qualification/counterevidence); opposing roles coexist.
- ``query_synthesis`` (aliases ``query_dossier``, ``derived_synthesis``):
  query-assembled virtual holon pinned by dependency rows.

Reconciliation ops are structurally distinct: grouping (membership rows here)
vs linking (typed edge rows in ``holon_link``, see ``reconcile.py``) vs
canonicalizing (versioned mapping rows in ``holon_canonical``). Membership
never implies agreement. Similarity scores can never create identity, support,
contradiction, or causation rows — no such API path exists.

Holons hold memberships, never merged strings (NO text fusion). Containment
is an acyclic depth-bounded DAG; semantic traversals use visited sets and
work budgets. Per-holon accumulators (S_h, W_h, count) update incrementally
in O(k*d) on the pure-add fast path (large holons); small holons re-derive
every field exactly per mutation, and any membership anomaly falls back to
the exact full recompute (the oracle). Descriptor/representatives are exact
on the full path and carried forward on the fold path until the next full
recompute (see ``FULL_REFRESH_LIMIT`` and ``refresh_derived``); digests,
counts, and version history are exact on both paths. Empty holons clear
descriptors.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from .engine import pack_vector, text_vector, unpack_vector, vector_tokens

#: Canonical holon kinds. ``argument`` is the ARCH §5 alias of
#: ``claim_cluster``; ``query_dossier`` / ``derived_synthesis`` are the ARCH
#: aliases of the virtual ``query_synthesis`` kind.
HOLON_KINDS = frozenset(
    {
        "discourse_span",
        "thematic_cluster",
        "claim_cluster",
        "argument",
        "query_synthesis",
        "query_dossier",
        "derived_synthesis",
    }
)

CLAIM_KINDS = frozenset({"claim_cluster", "argument"})
SYNTHESIS_KINDS = frozenset({"query_synthesis", "query_dossier", "derived_synthesis"})

#: Explicit evidence roles required on every claim_cluster member.
#: Membership in a role never implies agreement with other members.
CLAIM_ROLES = frozenset({"premise", "claim", "qualification", "counterevidence"})

HOLON_STATUSES = frozenset({"active", "stale", "invalidated", "deleted"})

#: Non-active ``holon_state.status`` tokens that need attention: ``stale``
#: (written by the R06 engine path) and ``invalidated`` (written by the R05
#: holon path). Shared classification for readers that union both tokens
#: (e.g. the ``/api/status`` count). This approves no claim of lifecycle
#: interchange: ``stale`` heals only via repair and ``invalidated`` only via
#: revalidate — healing belongs to N12.
NEEDS_ATTENTION = frozenset({"stale", "invalidated"})

#: Containment DAG bound (ARCH §5: containment is depth-bounded).
MAX_CONTAINMENT_DEPTH = 8
#: Default traversal work budget (node visits) for DAG walks.
TRAVERSAL_BUDGET = 10_000
#: Max representative refs pinned per holon.
MAX_REPRESENTATIVES = 3
#: Tolerance for the accumulator drift check vs exact recompute.
DRIFT_TOL = 1e-9

#: Legacy ``holons.doc_id`` sentinel for multi-source holons. Single-source
#: holons of any kind keep their real doc_id; only multi-source rows use ''.
CROSS_SOURCE_DOC = ""

#: Dimension of the hashed-vector accumulator space (filing engine vectors).
ACC_DIM = 128

#: Membership size above which ``add_members`` maintains state incrementally.
#: At or below this size every mutation takes the exact full recompute
#: (descriptor/representatives/digest/accumulators all re-derived from live
#: members). Above it, pure-add mutations fold the new members into the
#: stored accumulators (O(k*d) encodes) with an exact digest recompute over
#: narrow member rows; descriptor/representatives carry forward until the
#: next full recompute (membership repair or explicit ``refresh_derived``).
#: Set well above every committed-test holon size (<= ~10 members) so the
#: R05 exactness assertions always exercise the full path.
FULL_REFRESH_LIMIT = 64

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
    weight REAL NOT NULL DEFAULT 1.0,
    role TEXT,
    member_kind TEXT NOT NULL DEFAULT 'atom',
    member_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (holon_id, atom_id)
);
CREATE INDEX IF NOT EXISTS idx_holon_doc ON holons(doc_id);
CREATE INDEX IF NOT EXISTS idx_holon_member_atom ON holon_members(atom_id);
"""

#: R05 migration-004 auxiliary tables. Mirrored verbatim in
#: ``store/schema.sql`` (§004) so fresh ``Database`` opens carry them;
#: ``HolonStore`` also ensures them so filing works standalone.
HOLON_AUX_SCHEMA = """
CREATE TABLE IF NOT EXISTS holon_state (
    holon_id             TEXT PRIMARY KEY REFERENCES holons(holon_id) ON DELETE CASCADE,
    kind                 TEXT NOT NULL DEFAULT 'discourse_span',
    status               TEXT NOT NULL DEFAULT 'active',
    descriptor           TEXT NOT NULL DEFAULT '',
    derivation           TEXT NOT NULL DEFAULT '',
    vector_sum           BLOB,
    weight_sum           REAL NOT NULL DEFAULT 0,
    member_count         INTEGER NOT NULL DEFAULT 0,
    representatives_json TEXT NOT NULL DEFAULT '[]',
    member_digest        TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holon_version (
    version_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    holon_id             TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    version              INTEGER NOT NULL,
    kind                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    descriptor           TEXT NOT NULL DEFAULT '',
    derivation           TEXT NOT NULL DEFAULT '',
    weight_sum           REAL NOT NULL DEFAULT 0,
    member_count         INTEGER NOT NULL DEFAULT 0,
    representatives_json TEXT NOT NULL DEFAULT '[]',
    member_digest        TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    UNIQUE (holon_id, version)
);
CREATE TABLE IF NOT EXISTS holon_containment (
    parent_holon_id TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    child_holon_id  TEXT NOT NULL REFERENCES holons(holon_id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (parent_holon_id, child_holon_id),
    CHECK (parent_holon_id != child_holon_id)
);
CREATE TABLE IF NOT EXISTS holon_link (
    link_id      TEXT PRIMARY KEY,
    src_kind     TEXT NOT NULL,
    src_id       TEXT NOT NULL,
    dst_kind     TEXT NOT NULL,
    dst_id       TEXT NOT NULL,
    rel_type     TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holon_canonical (
    canonical_id TEXT NOT NULL,
    version      INTEGER NOT NULL,
    target_kind  TEXT NOT NULL,
    target_id    TEXT NOT NULL,
    label        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
    derivation   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    PRIMARY KEY (canonical_id, version, target_kind, target_id)
);
CREATE INDEX IF NOT EXISTS idx_holon_state_kind ON holon_state(kind, status);
CREATE INDEX IF NOT EXISTS idx_holon_version_holon ON holon_version(holon_id, version);
CREATE INDEX IF NOT EXISTS idx_containment_parent ON holon_containment(parent_holon_id);
CREATE INDEX IF NOT EXISTS idx_containment_child ON holon_containment(child_holon_id);
CREATE INDEX IF NOT EXISTS idx_link_src ON holon_link(src_kind, src_id);
CREATE INDEX IF NOT EXISTS idx_link_dst ON holon_link(dst_kind, dst_id);
CREATE INDEX IF NOT EXISTS idx_canonical_id ON holon_canonical(canonical_id, version);
"""

#: Columns added to pre-004 ``holon_members`` rows (name, DDL).
_MEMBER_COLUMNS = (
    ("weight", "ALTER TABLE holon_members ADD COLUMN weight REAL NOT NULL DEFAULT 1.0"),
    ("role", "ALTER TABLE holon_members ADD COLUMN role TEXT"),
    ("member_kind", "ALTER TABLE holon_members ADD COLUMN member_kind TEXT NOT NULL DEFAULT 'atom'"),
    ("member_version", "ALTER TABLE holon_members ADD COLUMN member_version INTEGER NOT NULL DEFAULT 1"),
)


class HolonError(ValueError):
    """Raised when a holon breaks kind, contiguity, DAG, or budget rules."""


@dataclass(frozen=True)
class Holon:
    holon_id: str
    doc_id: str
    tier: str
    atom_ids: tuple[int, ...]
    start_byte: int
    end_byte: int
    kind: str = "discourse_span"
    status: str = "active"
    weight_sum: float = 0.0
    member_count: int = 0
    representatives: tuple[int, ...] = ()
    descriptor: str = ""
    member_digest: str = ""
    version: int = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_holon_aux(conn: sqlite3.Connection) -> dict:
    """Idempotently ensure R05 holon DDL + backfill state for legacy holons.

    Safe on pre-004 files: legacy ``holons``/``holon_members`` rows and IDs
    are preserved; missing member columns are added with defaults; every
    holon without a state row gets one (kind ``discourse_span`` — the only
    kind that could exist before R05). Returns a small report dict.
    """
    conn.executescript(HOLON_SCHEMA)
    conn.executescript(HOLON_AUX_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(holon_members)").fetchall()}
    added = []
    for name, ddl in _MEMBER_COLUMNS:
        if name not in cols:
            conn.execute(ddl)
            added.append(name)
    backfilled = 0
    for (hid,) in conn.execute(
        "SELECT holon_id FROM holons WHERE holon_id NOT IN (SELECT holon_id FROM holon_state)"
    ).fetchall():
        _insert_state_for(conn, str(hid), kind="discourse_span", status="active",
                          derivation="backfill: pre-004 legacy holon")
        backfilled += 1
    conn.commit()
    return {"member_columns_added": added, "states_backfilled": backfilled}


def _insert_state_for(conn: sqlite3.Connection, holon_id: str, *, kind: str,
                      status: str, derivation: str) -> None:
    members = conn.execute(
        "SELECT m.atom_id, m.weight, m.role, m.member_kind, m.member_version,"
        " a.text FROM holon_members m JOIN atom a ON a.id = m.atom_id"
        " WHERE m.holon_id = ?",
        (holon_id,),
    ).fetchall()
    vecs = [(str(r[5]), float(r[1])) for r in members]
    S, W = _exact_accumulators(vecs)
    reps = _representatives([int(r[0]) for r in members], vecs, S, W)
    texts = [str(r[5]) for r in members]
    conn.execute(
        "INSERT OR IGNORE INTO holon_state (holon_id, kind, status, descriptor,"
        " derivation, vector_sum, weight_sum, member_count,"
        " representatives_json, member_digest, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (holon_id, kind, status, _descriptor(texts),
         derivation, pack_vector(S) if members else None, W, len(members),
         json.dumps(reps), _digest(members), _utcnow()),
    )
    _snapshot(conn, holon_id)


class HolonStore:
    """Kind-aware overlapping holon groups with DAG containment + accumulators."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        ensure_holon_aux(conn)
        self._descriptor_cache: dict[str, str] = {}

    # -- creation ----------------------------------------------------
    def create_holon(self, atom_ids: list[int], tier: str = "nav", *,
                     kind: str = "discourse_span",
                     roles: dict[int, str] | None = None,
                     weights: dict[int, float] | None = None,
                     derivation: str = "",
                     derived_from: list[str] | None = None) -> str:
        """Group atoms into a holon of the given kind. Returns the holon id."""
        if kind not in HOLON_KINDS:
            raise HolonError(f"unknown holon kind {kind!r}")
        if not atom_ids:
            raise HolonError("a holon needs at least 1 atom")
        if kind in SYNTHESIS_KINDS:
            need = 1
        else:
            need = 2
        if len(atom_ids) < need:
            raise HolonError(f"a {kind} holon needs at least {need} atoms")
        if len(set(atom_ids)) != len(atom_ids):
            raise HolonError("duplicate atom in holon membership")
        rows = []
        for aid in atom_ids:
            row = self.conn.execute(
                "SELECT id, doc_id, start_byte, end_byte, text FROM atom WHERE id = ?",
                (aid,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown atom {aid}")
            rows.append(row)
        _check_weights(weights, atom_ids)
        _check_roles(kind, roles, atom_ids)
        doc_ids = {r[1] for r in rows}
        if kind == "discourse_span":
            if len(doc_ids) != 1:
                raise HolonError(
                    f"holon must stay intra-document, saw docs {sorted(doc_ids)}")
            ordered = sorted(rows, key=lambda r: (r[2], r[0]))
            ids = [r[0] for r in ordered]
            doc_id = ordered[0][1]
            doc_order = [
                r[0] for r in self.conn.execute(
                    "SELECT id FROM atom WHERE doc_id = ? ORDER BY start_byte, id",
                    (doc_id,)).fetchall()
            ]
            positions = sorted(doc_order.index(aid) for aid in ids)
            if positions != list(range(positions[0], positions[0] + len(ids))):
                raise HolonError("holon atoms must be contiguous within the document")
            start, end = ordered[0][2], ordered[-1][3]
        else:
            ordered = sorted(rows, key=lambda r: (str(r[1]), r[2], r[0]))
            ids = [r[0] for r in ordered]
            doc_id = ordered[0][1] if len(doc_ids) == 1 else CROSS_SOURCE_DOC
            start = min(r[2] for r in ordered)
            end = max(r[3] for r in ordered)
        for parent in derived_from or []:
            if self.conn.execute(
                    "SELECT 1 FROM holons WHERE holon_id = ?",
                    (parent,)).fetchone() is None:
                raise KeyError(f"unknown parent holon {parent}")
        holon_id = uuid.uuid4().hex
        try:
            self.conn.execute(
                "INSERT INTO holons (holon_id, doc_id, tier, start_byte, end_byte)"
                " VALUES (?, ?, ?, ?, ?)",
                (holon_id, doc_id, tier, start, end),
            )
            self.conn.executemany(
                "INSERT INTO holon_members (holon_id, atom_id, position, weight,"
                " role, member_kind, member_version) VALUES (?, ?, ?, ?, ?, 'atom', 1)",
                [(holon_id, aid, pos,
                  float((weights or {}).get(aid, 1.0)),
                  (roles or {}).get(aid))
                 for pos, aid in enumerate(ids)],
            )
            self._write_state(holon_id, kind=kind, status="active",
                              derivation=derivation)
            for aid in ids:
                _add_dependency(self.conn, "holon", holon_id, "atom", str(aid),
                                "member")
            for parent in derived_from or []:
                _add_dependency(self.conn, "holon", holon_id, "holon", parent,
                                "derived_from")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return holon_id

    # -- reads -------------------------------------------------------
    def get_holon(self, holon_id: str) -> Holon:
        row = self.conn.execute(
            "SELECT holon_id, doc_id, tier, start_byte, end_byte FROM holons"
            " WHERE holon_id = ?",
            (holon_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown holon {holon_id}")
        state = self.conn.execute(
            "SELECT kind, status, descriptor, weight_sum, member_count,"
            " representatives_json, member_digest FROM holon_state WHERE holon_id = ?",
            (holon_id,),
        ).fetchone()
        if state is None:  # pre-004 row never backfilled (foreign writer)
            ensure_holon_aux(self.conn)
            state = self.conn.execute(
                "SELECT kind, status, descriptor, weight_sum, member_count,"
                " representatives_json, member_digest FROM holon_state"
                " WHERE holon_id = ?",
                (holon_id,),
            ).fetchone()
        members = tuple(
            r[0] for r in self.conn.execute(
                "SELECT atom_id FROM holon_members WHERE holon_id = ? ORDER BY position",
                (holon_id,)).fetchall()
        )
        version = self.conn.execute(
            "SELECT COALESCE(MAX(version), 1) FROM holon_version WHERE holon_id = ?",
            (holon_id,)).fetchone()[0]
        return Holon(row[0], row[1], row[2], members, row[3], row[4],
                     kind=str(state[0]), status=str(state[1]),
                     weight_sum=float(state[3]), member_count=int(state[4]),
                     representatives=tuple(json.loads(str(state[5]))),
                     descriptor=str(state[2]), member_digest=str(state[6]),
                     version=int(version))

    def holons_of_doc(self, doc_id: str) -> list[Holon]:
        return [
            self.get_holon(r[0]) for r in self.conn.execute(
                "SELECT h.holon_id FROM holons h JOIN holon_state s"
                " ON s.holon_id = h.holon_id"
                " WHERE h.doc_id = ? AND s.status = 'active' ORDER BY h.start_byte",
                (doc_id,)).fetchall()
        ]

    def holons_of_atom(self, atom_id: int) -> list[Holon]:
        return [
            self.get_holon(r[0]) for r in self.conn.execute(
                "SELECT m.holon_id FROM holon_members m JOIN holon_state s"
                " ON s.holon_id = m.holon_id"
                " WHERE m.atom_id = ? AND s.status = 'active' ORDER BY m.position",
                (atom_id,)).fetchall()
        ]

    def holons_of_kind(self, kind: str) -> list[Holon]:
        if kind not in HOLON_KINDS:
            raise HolonError(f"unknown holon kind {kind!r}")
        return [
            self.get_holon(r[0]) for r in self.conn.execute(
                "SELECT holon_id FROM holon_state WHERE kind = ? AND status = 'active'",
                (kind,)).fetchall()
        ]

    def get_version_history(self, holon_id: str) -> list[dict]:
        self._require_holon(holon_id)
        return [
            {"version": r[0], "kind": r[1], "status": r[2], "descriptor": r[3],
             "derivation": r[4], "weight_sum": r[5], "member_count": r[6],
             "representatives": json.loads(str(r[7])), "member_digest": r[8],
             "created_at": r[9]}
            for r in self.conn.execute(
                "SELECT version, kind, status, descriptor, derivation, weight_sum,"
                " member_count, representatives_json, member_digest, created_at"
                " FROM holon_version WHERE holon_id = ? ORDER BY version",
                (holon_id,)).fetchall()
        ]

    # -- membership edits (group op) ---------------------------------
    def add_members(self, holon_id: str, atom_ids: list[int], *,
                    roles: dict[int, str] | None = None,
                    weights: dict[int, float] | None = None) -> Holon:
        """Add members; refresh state exactly (small) or incrementally (large).

        Membership rows, dependency rows, ``member_count``, ``weight_sum``,
        ``vector_sum`` (within float assoc), ``member_digest``, and the
        version snapshot are exact on both paths. Above
        ``FULL_REFRESH_LIMIT`` members, pure-add mutations fold only the new
        members' vectors into the stored sums (O(k*d) encodes instead of a
        full member-text rescan); descriptor/representatives carry forward
        until the next full recompute. Any anomaly (out-of-band membership
        change such as a cascaded atom delete, concurrent foreign insert,
        missing/dimension-shifted accumulator) falls back to the exact full
        recompute, which also heals the carried fields.
        """
        holon = self.get_holon(holon_id)
        if holon.status == "deleted":
            raise HolonError(f"holon {holon_id} is deleted")
        if not atom_ids:
            raise HolonError("nothing to add")
        if len(set(atom_ids)) != len(atom_ids):
            raise HolonError("duplicate atom in add set")
        existing = set(holon.atom_ids)
        if existing & set(atom_ids):
            raise HolonError("atom already a member of this holon")
        for aid in atom_ids:
            if self.conn.execute(
                    "SELECT 1 FROM atom WHERE id = ?", (aid,)).fetchone() is None:
                raise KeyError(f"unknown atom {aid}")
        _check_weights(weights, atom_ids)
        _check_roles(holon.kind, roles, atom_ids)
        if holon.kind == "discourse_span":
            all_rows = [self._atom_row(a) for a in list(holon.atom_ids) + atom_ids]
            if len({r[1] for r in all_rows}) != 1:
                raise HolonError("discourse_span members must stay single-source")
            doc_id = all_rows[0][1]
            doc_order = [
                r[0] for r in self.conn.execute(
                    "SELECT id FROM atom WHERE doc_id = ? ORDER BY start_byte, id",
                    (doc_id,)).fetchall()
            ]
            positions = sorted(doc_order.index(r[0]) for r in all_rows)
            if positions != list(range(positions[0], positions[0] + len(all_rows))):
                raise HolonError("discourse_span members must stay contiguous")
        try:
            base = self.conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM holon_members WHERE holon_id = ?",
                (holon_id,)).fetchone()[0]
            ordered = sorted(
                atom_ids,
                key=lambda a: (str(self._atom_row(a)[1]), self._atom_row(a)[2], a))
            self.conn.executemany(
                "INSERT INTO holon_members (holon_id, atom_id, position, weight,"
                " role, member_kind, member_version) VALUES (?, ?, ?, ?, ?, 'atom', 1)",
                [(holon_id, aid, base + 1 + pos,
                  float((weights or {}).get(aid, 1.0)),
                  (roles or {}).get(aid))
                 for pos, aid in enumerate(ordered)],
            )
            for aid in ordered:
                _add_dependency(self.conn, "holon", holon_id, "atom", str(aid),
                                "member")
            if (len(existing) + len(ordered) > FULL_REFRESH_LIMIT
                    and self._fold_state_for_add(holon_id, ordered)):
                pass  # incremental fold done (snapshot included)
            else:
                self._refresh_state(holon_id)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return self.get_holon(holon_id)

    def refresh_derived(self, holon_id: str) -> Holon:
        """Force an exact full state recompute for one holon.

        Re-derives every persisted field (accumulators, descriptor,
        representatives, digest) from live members and appends a version
        row. This is the explicit remedy for the carried
        descriptor/representatives on large incrementally maintained
        holons; small holons never need it (every mutation already
        recomputes fully).
        """
        self.get_holon(holon_id)  # validates + backfills legacy state rows
        try:
            self._refresh_state(holon_id)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return self.get_holon(holon_id)

    def set_weight(self, holon_id: str, atom_id: int, weight: float) -> Holon:
        self._require_member(holon_id, atom_id)
        _check_weights({atom_id: weight}, [atom_id])
        try:
            self.conn.execute(
                "UPDATE holon_members SET weight = ? WHERE holon_id = ? AND atom_id = ?",
                (float(weight), holon_id, atom_id),
            )
            self._refresh_state(holon_id)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return self.get_holon(holon_id)

    def remove_members(self, holon_id: str, atom_ids: list[int]) -> dict:
        """Remove members (incremental subtract); invalidates dependents.

        Returns ``{"holon": ..., "removed": [...], "invalidated": [...]}``.
        Deleting support invalidates dependent synthesis and containment
        ancestors via dependency rows — not just the membership removal.
        """
        holon = self.get_holon(holon_id)
        if holon.status == "deleted":
            raise HolonError(f"holon {holon_id} is deleted")
        missing = set(atom_ids) - set(holon.atom_ids)
        if missing:
            raise KeyError(f"atoms not members of {holon_id}: {sorted(missing)}")
        if holon.kind == "discourse_span":
            rest = [a for a in holon.atom_ids if a not in set(atom_ids)]
            if len(rest) >= 2:
                rows = [self._atom_row(a) for a in rest]
                doc_order = [
                    r[0] for r in self.conn.execute(
                        "SELECT id FROM atom WHERE doc_id = ? ORDER BY start_byte, id",
                        (rows[0][1],)).fetchall()
                ]
                positions = sorted(doc_order.index(r[0]) for r in rows)
                if positions != list(range(positions[0], positions[0] + len(rows))):
                    raise HolonError("discourse_span remainder must stay contiguous")
        try:
            for aid in atom_ids:
                self.conn.execute(
                    "DELETE FROM holon_members WHERE holon_id = ? AND atom_id = ?",
                    (holon_id, aid),
                )
                self.conn.execute(
                    "DELETE FROM dependency WHERE derived_kind = 'holon'"
                    " AND derived_id = ? AND requires_kind = 'atom' AND requires_id = ?"
                    " AND dependency_kind = 'member'",
                    (holon_id, str(aid)),
                )
            self._refresh_state(holon_id)
            requires = [("holon", holon_id)] + [("atom", str(a)) for a in atom_ids]
            invalidated = self._invalidate_dependents(requires, exclude={holon_id})
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return {"holon": holon_id, "removed": list(atom_ids),
                "invalidated": invalidated}

    def delete_holon(self, holon_id: str) -> dict:
        """Tombstone a holon (ID preserved); clears descriptors; invalidates."""
        holon = self.get_holon(holon_id)
        if holon.status == "deleted":
            raise HolonError(f"holon {holon_id} is already deleted")
        members = list(holon.atom_ids)
        try:
            self.conn.execute("DELETE FROM holon_members WHERE holon_id = ?",
                              (holon_id,))
            self.conn.execute(
                "DELETE FROM dependency WHERE derived_kind = 'holon' AND derived_id = ?"
                " AND dependency_kind = 'member'",
                (holon_id,),
            )
            self._set_status(holon_id, "deleted", clear=True)
            requires = [("holon", holon_id)] + [("atom", str(a)) for a in members]
            invalidated = self._invalidate_dependents(requires, exclude={holon_id})
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return {"holon": holon_id, "invalidated": invalidated}

    def revalidate(self, holon_id: str) -> Holon:
        holon = self.get_holon(holon_id)
        if holon.status == "deleted":
            raise HolonError(f"holon {holon_id} is deleted")
        if holon.status != "invalidated":
            return holon
        try:
            self._set_status(holon_id, "active", clear=False)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return self.get_holon(holon_id)

    # -- containment DAG ---------------------------------------------
    def add_containment(self, parent_id: str, child_id: str) -> dict:
        """Nest child under parent. Cycle/depth violations raise HolonError."""
        self._require_holon(parent_id)
        self._require_holon(child_id)
        if parent_id == child_id:
            raise HolonError("a holon cannot contain itself")
        for hid in (parent_id, child_id):
            if self.get_holon(hid).status == "deleted":
                raise HolonError(f"holon {hid} is deleted")
        if self.conn.execute(
                "SELECT 1 FROM holon_containment WHERE parent_holon_id = ?"
                " AND child_holon_id = ?", (parent_id, child_id)).fetchone():
            return {"parent": parent_id, "child": child_id, "duplicate": True}
        if self._reachable(child_id, parent_id):
            raise HolonError(
                f"containment {parent_id} -> {child_id} would create a cycle")
        try:
            self.conn.execute(
                "INSERT INTO holon_containment (parent_holon_id, child_holon_id,"
                " created_at) VALUES (?, ?, ?)",
                (parent_id, child_id, _utcnow()),
            )
            if self._component_max_depth(parent_id) > MAX_CONTAINMENT_DEPTH:
                raise HolonError(
                    f"containment would exceed max depth {MAX_CONTAINMENT_DEPTH}")
            _add_dependency(self.conn, "holon", parent_id, "holon", child_id,
                            "contains")
            self._refresh_state(parent_id)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return {"parent": parent_id, "child": child_id, "duplicate": False}

    def remove_containment(self, parent_id: str, child_id: str) -> dict:
        self._require_holon(parent_id)
        self._require_holon(child_id)
        if not self.conn.execute(
                "SELECT 1 FROM holon_containment WHERE parent_holon_id = ?"
                " AND child_holon_id = ?", (parent_id, child_id)).fetchone():
            raise KeyError(f"unknown containment {parent_id} -> {child_id}")
        try:
            self.conn.execute(
                "DELETE FROM holon_containment WHERE parent_holon_id = ?"
                " AND child_holon_id = ?", (parent_id, child_id))
            self.conn.execute(
                "DELETE FROM dependency WHERE derived_kind = 'holon' AND derived_id = ?"
                " AND requires_kind = 'holon' AND requires_id = ?"
                " AND dependency_kind = 'contains'",
                (parent_id, child_id),
            )
            self._refresh_state(parent_id)
            invalidated = self._invalidate_dependents(
                [("holon", parent_id)], exclude={parent_id})
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return {"parent": parent_id, "child": child_id,
                "invalidated": invalidated}

    def children_of(self, holon_id: str, *,
                    budget: int = TRAVERSAL_BUDGET) -> list[str]:
        self._require_holon(holon_id)
        return sorted(r[0] for r in self.conn.execute(
            "SELECT child_holon_id FROM holon_containment WHERE parent_holon_id = ?",
            (holon_id,)).fetchall())[:budget]

    def parents_of(self, holon_id: str, *,
                   budget: int = TRAVERSAL_BUDGET) -> list[str]:
        self._require_holon(holon_id)
        return sorted(r[0] for r in self.conn.execute(
            "SELECT parent_holon_id FROM holon_containment WHERE child_holon_id = ?",
            (holon_id,)).fetchall())[:budget]

    def descendants(self, holon_id: str, *,
                    budget: int = TRAVERSAL_BUDGET) -> list[str]:
        self._require_holon(holon_id)
        seen: set[str] = set()
        stack = [holon_id]
        visits = 0
        while stack:
            cur = stack.pop()
            for (child,) in self.conn.execute(
                    "SELECT child_holon_id FROM holon_containment"
                    " WHERE parent_holon_id = ?", (cur,)).fetchall():
                visits += 1
                if visits > budget:
                    raise HolonError(
                        f"descendant traversal exceeded budget {budget}")
                if child not in seen and child != holon_id:
                    seen.add(child)
                    stack.append(child)
        return sorted(seen)

    def ancestors(self, holon_id: str, *,
                  budget: int = TRAVERSAL_BUDGET) -> list[str]:
        self._require_holon(holon_id)
        seen: set[str] = set()
        stack = [holon_id]
        visits = 0
        while stack:
            cur = stack.pop()
            for (parent,) in self.conn.execute(
                    "SELECT parent_holon_id FROM holon_containment"
                    " WHERE child_holon_id = ?", (cur,)).fetchall():
                visits += 1
                if visits > budget:
                    raise HolonError(
                        f"ancestor traversal exceeded budget {budget}")
                if parent not in seen and parent != holon_id:
                    seen.add(parent)
                    stack.append(parent)
        return sorted(seen)

    def depth_of(self, holon_id: str, *,
                 budget: int = TRAVERSAL_BUDGET) -> int:
        """Longest containment chain below this holon (leaf == 1)."""
        self._require_holon(holon_id)
        memo: dict[str, int] = {}
        visits = [0]

        def _depth(hid: str, trail: frozenset) -> int:
            if hid in memo:
                return memo[hid]
            if hid in trail:
                raise HolonError(f"containment cycle detected at {hid}")
            kids = [r[0] for r in self.conn.execute(
                "SELECT child_holon_id FROM holon_containment"
                " WHERE parent_holon_id = ?", (hid,)).fetchall()]
            if not kids:
                memo[hid] = 1
                return 1
            best = 1
            for kid in kids:
                visits[0] += 1
                if visits[0] > budget:
                    raise HolonError(f"depth traversal exceeded budget {budget}")
                best = max(best, 1 + _depth(kid, trail | {hid}))
            memo[hid] = best
            return best

        return _depth(holon_id, frozenset())

    # -- accumulators / drift / integrity ------------------------------
    def check_drift(self, holon_id: str, tol: float = DRIFT_TOL) -> dict:
        """Compare stored S_h/W_h against an exact recompute. Returns report."""
        self._require_holon(holon_id)
        members = self.conn.execute(
            "SELECT m.weight, a.text FROM holon_members m JOIN atom a ON a.id = m.atom_id"
            " WHERE m.holon_id = ?",
            (holon_id,)).fetchall()
        exact_S, exact_W = _exact_accumulators(
            [(str(r[1]), float(r[0])) for r in members])
        state = self.conn.execute(
            "SELECT vector_sum, weight_sum, member_count FROM holon_state"
            " WHERE holon_id = ?", (holon_id,)).fetchone()
        stored_S = unpack_vector(bytes(state[0])) if state[0] is not None else [0.0] * ACC_DIM
        stored_W = float(state[1])
        diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(exact_S, stored_S)))
        norm = math.sqrt(sum(a * a for a in exact_S))
        drift = diff / max(1.0, norm)
        weight_drift = abs(exact_W - stored_W)
        count_ok = int(state[2]) == len(members)
        ok = drift <= tol and weight_drift <= 1e-9 and count_ok
        return {"holon_id": holon_id, "drift": drift,
                "weight_drift": weight_drift, "count_ok": count_ok, "ok": ok}

    def check_holon_integrity(self, holon_id: str) -> dict:
        """Zero-orphan + descriptor audit for one holon (C5 evidence helper)."""
        self._require_holon(holon_id)
        orphans: list[str] = []
        member_ids = [r[0] for r in self.conn.execute(
            "SELECT atom_id FROM holon_members WHERE holon_id = ?",
            (holon_id,)).fetchall()]
        for aid in member_ids:
            if self.conn.execute(
                    "SELECT 1 FROM atom WHERE id = ?", (aid,)).fetchone() is None:
                orphans.append(f"member atom {aid} missing")
        holon = self.get_holon(holon_id)
        digest_ok = holon.member_digest == self._current_digest(holon_id)
        drift = self.check_drift(holon_id)
        missing_dep = []
        for aid in member_ids:
            if not self.conn.execute(
                    "SELECT 1 FROM dependency WHERE derived_kind = 'holon'"
                    " AND derived_id = ? AND requires_kind = 'atom' AND requires_id = ?",
                    (holon_id, str(aid))).fetchone():
                missing_dep.append(str(aid))
        ok = not orphans and digest_ok and drift["ok"] and not missing_dep
        return {"holon_id": holon_id, "members": len(member_ids),
                "orphans": orphans, "digest_ok": digest_ok, "drift": drift["drift"],
                "missing_dependency": missing_dep, "ok": ok}

    # -- internals -----------------------------------------------------
    def _require_holon(self, holon_id: str) -> None:
        if self.conn.execute(
                "SELECT 1 FROM holons WHERE holon_id = ?",
                (holon_id,)).fetchone() is None:
            raise KeyError(f"unknown holon {holon_id}")

    def _require_member(self, holon_id: str, atom_id: int) -> None:
        self._require_holon(holon_id)
        if not self.conn.execute(
                "SELECT 1 FROM holon_members WHERE holon_id = ? AND atom_id = ?",
                (holon_id, atom_id)).fetchone():
            raise KeyError(f"atom {atom_id} not a member of {holon_id}")

    def _atom_row(self, atom_id: int):  # (id, doc_id, start, end, text)
        row = self.conn.execute(
            "SELECT id, doc_id, start_byte, end_byte, text FROM atom WHERE id = ?",
            (atom_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown atom {atom_id}")
        return row

    def _write_state(self, holon_id: str, *, kind: str, status: str,
                     derivation: str) -> None:
        self.conn.execute(
            "INSERT INTO holon_state (holon_id, kind, status, descriptor, derivation,"
            " vector_sum, weight_sum, member_count, representatives_json,"
            " member_digest, updated_at)"
            " VALUES (?, ?, ?, '', ?, NULL, 0, 0, '[]', '', ?)",
            (holon_id, kind, status, derivation, _utcnow()),
        )
        self._refresh_state(holon_id)

    def _refresh_state(self, holon_id: str) -> None:
        members = self.conn.execute(
            "SELECT m.atom_id, m.weight, m.role, m.member_kind, m.member_version,"
            " a.text FROM holon_members m JOIN atom a ON a.id = m.atom_id"
            " WHERE m.holon_id = ?",
            (holon_id,)).fetchall()
        vecs = [(str(r[5]), float(r[1])) for r in members]
        texts = [str(r[5]) for r in members]
        if members:
            S, W = _exact_accumulators(vecs)
            blob: bytes | None = pack_vector(S)
            desc = _descriptor(texts)
            reps = _representatives([int(r[0]) for r in members], vecs, S, W)
        else:  # empty holons clear persistent + cached descriptors
            S, W, blob, desc, reps = [0.0] * ACC_DIM, 0.0, None, "", []
            self._descriptor_cache.pop(holon_id, None)
        self._descriptor_cache[holon_id] = desc
        self.conn.execute(
            "UPDATE holon_state SET descriptor = ?, vector_sum = ?, weight_sum = ?,"
            " member_count = ?, representatives_json = ?, member_digest = ?,"
            " updated_at = ? WHERE holon_id = ?",
            (desc, blob, W, len(members), json.dumps(reps),
             _digest(members), _utcnow(), holon_id),
        )
        _snapshot(self.conn, holon_id)

    def _fold_state_for_add(self, holon_id: str, new_ids: list[int]) -> bool:
        """Fold newly added members into stored state. True when folded.

        Pure-add fast path for large holons: encodes ONLY the new members'
        texts (O(k*d)) and folds them into the stored sums. The digest gate
        proves the rest of the membership is untouched (live members minus
        the new ids hash exactly to the stored digest, which covers the
        same JOIN row set ``_refresh_state`` would recompute over); the
        digest itself is recomputed exactly over all live rows and a
        version row is appended, so counts/digest/history stay exact.
        Descriptor/representatives carry forward (see ``refresh_derived``).
        Returns False on ANY anomaly (out-of-band delete, foreign insert,
        missing/shifted accumulator, count drift, unexpected error) so the
        caller falls back to the exact full recompute, which heals every
        carried or skewed field.
        """
        try:
            return self._fold_state_inner(holon_id, new_ids)
        except Exception:
            return False

    def _fold_state_inner(self, holon_id: str, new_ids: list[int]) -> bool:
        new_set = {int(a) for a in new_ids}
        live = self.conn.execute(
            "SELECT m.atom_id, m.weight, m.role, m.member_kind, m.member_version,"
            " a.text FROM holon_members m JOIN atom a ON a.id = m.atom_id"
            " WHERE m.holon_id = ?",
            (holon_id,),
        ).fetchall()
        fresh = [r for r in live if int(r[0]) in new_set]
        if len(fresh) != len(new_set):
            return False  # own insert not fully visible; stay exact
        stored = self.conn.execute(
            "SELECT vector_sum, weight_sum, member_count, member_digest"
            " FROM holon_state WHERE holon_id = ?",
            (holon_id,),
        ).fetchone()
        if stored is None or stored[0] is None:
            return False
        old_live = [r for r in live if int(r[0]) not in new_set]
        if _digest(old_live) != str(stored[3]):
            return False  # membership changed out of band; full recompute heals
        if int(stored[2]) != len(old_live):
            return False  # stored count disagrees; stay exact
        S = unpack_vector(bytes(stored[0]))
        dim = len(S)
        if dim <= 0:
            return False
        W = float(stored[1])
        for r in fresh:
            w = float(r[1])
            for j, v in enumerate(text_vector(str(r[5]), dim)):
                S[j] += w * v
            W += w
        self.conn.execute(
            "UPDATE holon_state SET vector_sum = ?, weight_sum = ?,"
            " member_count = ?, member_digest = ?, updated_at = ?"
            " WHERE holon_id = ?",
            (pack_vector(S), W, len(live), _digest(live),
             _utcnow(), holon_id),
        )
        _snapshot(self.conn, holon_id)
        return True

    def _set_status(self, holon_id: str, status: str, *, clear: bool) -> None:
        if clear:
            self.conn.execute(
                "UPDATE holon_state SET status = ?, descriptor = ?, vector_sum = NULL,"
                " weight_sum = 0, member_count = 0, representatives_json = '[]',"
                " member_digest = '', updated_at = ? WHERE holon_id = ?",
                (status, "", _utcnow(), holon_id),
            )
            self._descriptor_cache.pop(holon_id, None)
        else:
            self.conn.execute(
                "UPDATE holon_state SET status = ?, updated_at = ? WHERE holon_id = ?",
                (status, _utcnow(), holon_id),
            )
        _snapshot(self.conn, holon_id)

    def _current_digest(self, holon_id: str) -> str:
        members = self.conn.execute(
            "SELECT atom_id, weight, role, member_kind, member_version FROM holon_members"
            " WHERE holon_id = ?",
            (holon_id,)).fetchall()
        return _digest(members)

    def _reachable(self, start: str, goal: str,
                   budget: int = TRAVERSAL_BUDGET) -> bool:
        seen: set[str] = {start}
        stack = [start]
        visits = 0
        while stack:
            cur = stack.pop()
            for (child,) in self.conn.execute(
                    "SELECT child_holon_id FROM holon_containment"
                    " WHERE parent_holon_id = ?", (cur,)).fetchall():
                visits += 1
                if visits > budget:
                    raise HolonError(f"cycle-check traversal exceeded budget {budget}")
                if child == goal:
                    return True
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return False

    def _component_max_depth(self, holon_id: str,
                             budget: int = TRAVERSAL_BUDGET) -> int:
        roots = {holon_id} | set(self.ancestors(holon_id, budget=budget))
        return max(self.depth_of(r, budget=budget) for r in roots)

    def _invalidate_dependents(self, requires: list[tuple[str, str]],
                               *, exclude: set[str],
                               budget: int = TRAVERSAL_BUDGET) -> list[str]:
        """Mark active dependent holons invalidated via the reverse index.

        BFS over ``dependency`` (derived holon <- required atom/holon) plus
        containment parents, with a visited set and a work budget. The edited
        holon itself (``exclude``) stays active — only its dependents change.
        """
        invalidated: list[str] = []
        visited: set[tuple[str, str]] = set(requires)
        queue = list(requires)
        steps = 0
        while queue:
            kind, rid = queue.pop(0)
            derived = [str(r[0]) for r in self.conn.execute(
                "SELECT derived_id FROM dependency WHERE derived_kind = 'holon'"
                " AND requires_kind = ? AND requires_id = ?",
                (kind, rid)).fetchall()]
            if kind == "holon":
                derived += [str(r[0]) for r in self.conn.execute(
                    "SELECT parent_holon_id FROM holon_containment"
                    " WHERE child_holon_id = ?", (rid,)).fetchall()]
            for did in sorted(set(derived)):
                steps += 1
                if steps > budget:
                    raise HolonError(
                        f"invalidation closure exceeded budget {budget}")
                if did in exclude:
                    continue
                row = self.conn.execute(
                    "SELECT status FROM holon_state WHERE holon_id = ?",
                    (did,)).fetchone()
                if row is None or str(row[0]) != "active":
                    continue
                self.conn.execute(
                    "UPDATE holon_state SET status = 'invalidated', updated_at = ?"
                    " WHERE holon_id = ?", (_utcnow(), did))
                _snapshot(self.conn, did)
                invalidated.append(did)
                if ("holon", did) not in visited:
                    visited.add(("holon", did))
                    queue.append(("holon", did))
        return sorted(invalidated)


def _check_weights(weights: dict[int, float] | None,
                   atom_ids: list[int]) -> None:
    for aid in atom_ids:
        w = float((weights or {}).get(aid, 1.0))
        if not math.isfinite(w) or w <= 0:
            raise ValueError(f"atom {aid}: weight must be positive, saw {w!r}")


def _check_roles(kind: str, roles: dict[int, str] | None,
                 atom_ids: list[int]) -> None:
    if kind in CLAIM_KINDS:
        for aid in atom_ids:
            role = (roles or {}).get(aid)
            if role not in CLAIM_ROLES:
                raise HolonError(
                    f"atom {aid}: claim_cluster members need an explicit role"
                    f" in {sorted(CLAIM_ROLES)}, saw {role!r}")
    elif roles:
        raise HolonError(
            f"kind {kind} takes no member roles (claim_cluster-only)")


def _exact_accumulators(vecs: list[tuple[str, float]]) -> tuple[list[float], float]:
    """Exact S_h = sum(w_i * v_i), W_h = sum(w_i) over member texts."""
    S = [0.0] * ACC_DIM
    W = 0.0
    for text, w in vecs:
        for i, v in enumerate(text_vector(text, ACC_DIM)):
            S[i] += w * v
        W += w
    return S, W


def _representatives(atom_ids: list[int], vecs: list[tuple[str, float]],
                     S: list[float], W: float) -> list[int]:
    """Up-to-3 member atom ids nearest the centroid (representative refs)."""
    if not atom_ids or W <= 0:
        return []
    norm = math.sqrt(sum(v * v for v in S))
    if norm <= 0:
        return sorted(atom_ids)[:MAX_REPRESENTATIVES]
    centroid = [v / norm for v in S]
    scored = []
    for aid, (text, _w) in zip(atom_ids, vecs):
        v = text_vector(text, ACC_DIM)  # already L2-normalized
        scored.append((-(sum(a * b for a, b in zip(centroid, v))), aid))
    scored.sort()
    return [aid for _, aid in scored[:MAX_REPRESENTATIVES]]


def _descriptor(texts: list[str]) -> str:
    """Short token summary (top-3 weighted tokens) — never fused prose."""
    if not texts:
        return ""
    counts: Counter[str] = Counter()
    for text in texts:
        counts.update(vector_tokens(text))
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return ",".join(tok for tok, _ in top)


def _digest(members) -> str:
    members = list(members)
    if not members:  # empty holons carry no digest (cleared, not hashed)
        return ""
    parts = sorted(f"{r[3]}:{r[0]}:{float(r[1]):.6f}:{r[2] or ''}" for r in members)
    return hashlib.sha256(";".join(parts).encode("utf-8")).hexdigest()[:32]


def _snapshot(conn: sqlite3.Connection, holon_id: str) -> int:
    state = conn.execute(
        "SELECT kind, status, descriptor, derivation, weight_sum, member_count,"
        " representatives_json, member_digest FROM holon_state WHERE holon_id = ?",
        (holon_id,)).fetchone()
    prev = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM holon_version WHERE holon_id = ?",
        (holon_id,)).fetchone()[0]
    conn.execute(
        "INSERT INTO holon_version (holon_id, version, kind, status, descriptor,"
        " derivation, weight_sum, member_count, representatives_json,"
        " member_digest, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (holon_id, int(prev) + 1, str(state[0]), str(state[1]), str(state[2]),
         str(state[3]), float(state[4]), int(state[5]), str(state[6]),
         str(state[7]), _utcnow()),
    )
    return int(prev) + 1


def _add_dependency(conn: sqlite3.Connection, derived_kind: str, derived_id: str,
                    requires_kind: str, requires_id: str,
                    dependency_kind: str) -> None:
    conn.execute(
        "INSERT INTO dependency (derived_kind, derived_id, requires_kind,"
        " requires_id, dependency_kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (derived_kind, derived_id, requires_kind, requires_id,
         dependency_kind, _utcnow()),
    )
