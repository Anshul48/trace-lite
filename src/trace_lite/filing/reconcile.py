"""Selective reconciliation: link vs group vs canonicalize (ARCH §5).

Three structurally distinct ops — never interchangeable:

- ``group``: holon membership rows (``HolonStore`` in ``holon.py``).
- ``link``: a typed relation edge row in ``holon_link``. Closed vocabulary;
  similarity scores can NEVER produce identity, support, contradiction, or
  causation — no API path exists from a score to those relations.
- ``canonicalize``: versioned entity-mapping rows in ``holon_canonical``.
  Aliases are preserved as mapping history; mistaken merges stay reversible
  (``restore_version``) and splittable (``split_canonical``).

Polymorphic endpoints (holon/atom/mention/assertion references) are
code-validated at write time (existence-checked) and re-checked by
``audit_links`` — raw strings are never the only consistency mechanism.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .engine import text_vector
from .holon import ACC_DIM, HolonError

#: Closed link vocabulary for holon-level edges. Deliberately excludes every
#: identity / entailment / causal relation: those need validation lineage
#: that a similarity score cannot provide.
HOLON_LINK_TYPES = frozenset(
    {
        "topic_overlap",
        "shared_entity",
        "temporal_sequence",
        "references",
        "elaborates",
    }
)

#: Relation names that similarity alone must never yield. ``create_link``
#: rejects all of these even for byte-identical inputs.
FORBIDDEN_AUTO_RELATIONS = frozenset(
    {
        "identity",
        "identical",
        "same_as",
        "equivalent",
        "equivalence",
        "supports",
        "support",
        "contradicts",
        "contradiction",
        "causes",
        "causation",
        "entails",
        "entailment",
    }
)

_LINK_ENDPOINT_KINDS = frozenset({"holon", "atom"})
_CANONICAL_TARGET_KINDS = frozenset({"holon", "atom", "mention", "assertion"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def score_similarity(text_a: str, text_b: str) -> float:
    """Deterministic cosine similarity in the filing hashed-vector space."""
    va = text_vector(text_a, ACC_DIM)
    vb = text_vector(text_b, ACC_DIM)
    return sum(a * b for a, b in zip(va, vb))


def suggest_candidates(conn: sqlite3.Connection, seed_atom_ids: list[int],
                       candidate_atom_ids: list[int], *,
                       top_k: int = 5) -> list[dict]:
    """Rank candidates by max cosine to any seed. Read-only: never writes.

    Returns ``[{"atom_id", "doc_id", "score"}]`` — scores only, deliberately
    no ``rel_type``: promoting a suggestion to a typed edge requires an
    explicit ``create_link`` call with human/evidence-grounded vocabulary.
    """
    if not seed_atom_ids:
        raise HolonError("need at least 1 seed atom")
    seeds = [_atom_text(conn, aid)[0] for aid in seed_atom_ids]
    scored = []
    for aid in candidate_atom_ids:
        if aid in set(seed_atom_ids):
            continue
        text, doc_id = _atom_text(conn, aid)
        best = max(score_similarity(s, text) for s in seeds)
        scored.append((-best, aid, doc_id, best))
    scored.sort()
    return [{"atom_id": aid, "doc_id": doc_id, "score": score}
            for _, aid, doc_id, score in scored[:top_k]]


def create_link(conn: sqlite3.Connection, src_kind: str, src_id: str,
                dst_kind: str, dst_id: str, rel_type: str, *,
                evidence: dict | None = None, commit: bool = True) -> str:
    """Record a typed holon-level edge. Forbidden types always raise."""
    if src_kind not in _LINK_ENDPOINT_KINDS:
        raise HolonError(f"unknown link endpoint kind {src_kind!r}")
    if dst_kind not in _LINK_ENDPOINT_KINDS:
        raise HolonError(f"unknown link endpoint kind {dst_kind!r}")
    _require_endpoint(conn, src_kind, src_id)
    _require_endpoint(conn, dst_kind, dst_id)
    if rel_type in FORBIDDEN_AUTO_RELATIONS:
        raise ValueError(
            f"rel_type {rel_type!r} needs validation lineage: similarity alone"
            " never yields identity/support/contradiction/causation")
    if rel_type not in HOLON_LINK_TYPES:
        raise ValueError(
            f"unknown holon link type {rel_type!r};"
            f" allowed: {sorted(HOLON_LINK_TYPES)}")
    link_id = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO holon_link (link_id, src_kind, src_id, dst_kind, dst_id,"
            " rel_type, evidence_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (link_id, src_kind, str(src_id), dst_kind, str(dst_id), rel_type,
             json.dumps(evidence or {}), _utcnow()),
        )
        if commit:
            conn.commit()
    except BaseException:
        if commit:
            conn.rollback()
        raise
    return link_id


def get_links_for(conn: sqlite3.Connection, kind: str,
                  ref_id: str) -> list[dict]:
    """All link rows touching (kind, id) in either direction."""
    if kind not in _LINK_ENDPOINT_KINDS:
        raise HolonError(f"unknown link endpoint kind {kind!r}")
    return [
        {"link_id": r[0], "src_kind": r[1], "src_id": r[2], "dst_kind": r[3],
         "dst_id": r[4], "rel_type": r[5], "evidence": json.loads(str(r[6])),
         "created_at": r[7]}
        for r in conn.execute(
            "SELECT link_id, src_kind, src_id, dst_kind, dst_id, rel_type,"
            " evidence_json, created_at FROM holon_link"
            " WHERE (src_kind = ? AND src_id = ?) OR (dst_kind = ? AND dst_id = ?)"
            " ORDER BY created_at",
            (kind, str(ref_id), kind, str(ref_id))).fetchall()
    ]


def audit_links(conn: sqlite3.Connection) -> dict:
    """Every link endpoint must resolve; every rel_type must be in-vocabulary."""
    orphans: list[str] = []
    bad_types: list[str] = []
    count = 0
    for r in conn.execute(
            "SELECT link_id, src_kind, src_id, dst_kind, dst_id, rel_type"
            " FROM holon_link").fetchall():
        count += 1
        for side, kind, ref in (("src", r[1], r[2]), ("dst", r[3], r[4])):
            try:
                _require_endpoint(conn, str(kind), str(ref))
            except (KeyError, HolonError) as exc:
                orphans.append(f"{r[0]} {side} {kind}:{ref}: {exc}")
        if str(r[5]) not in HOLON_LINK_TYPES:
            bad_types.append(f"{r[0]} type {r[5]!r}")
    return {"links": count, "orphans": orphans, "bad_types": bad_types,
            "ok": not orphans and not bad_types}


def canonicalize(conn: sqlite3.Connection,
                 targets: list[tuple[str, str | int]], *,
                 label: str = "", derivation: str = "",
                 canonical_id: str | None = None,
                 commit: bool = True) -> str:
    """Map targets to a versioned canonical entity. Returns the canonical id.

    New id → version 1. Existing id → previous version superseded, new
    version appended (full history retained, hence reversible). Aliases are
    preserved as mapping rows, never collapsed into one string.
    """
    if not targets:
        raise HolonError("canonicalize needs at least 1 target")
    if len(set(targets)) != len(targets):
        raise HolonError("duplicate canonical target")
    for kind, ref in targets:
        if kind not in _CANONICAL_TARGET_KINDS:
            raise HolonError(f"unknown canonical target kind {kind!r}")
        _require_endpoint(conn, kind, str(ref))
    try:
        if canonical_id is None:
            cid, version = uuid.uuid4().hex, 1
        else:
            prev = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM holon_canonical"
                " WHERE canonical_id = ?", (canonical_id,)).fetchone()[0]
            if int(prev) == 0:
                raise KeyError(f"unknown canonical entity {canonical_id}")
            conn.execute(
                "UPDATE holon_canonical SET status = 'superseded'"
                " WHERE canonical_id = ? AND version = ? AND status = 'active'",
                (canonical_id, int(prev)),
            )
            cid, version = canonical_id, int(prev) + 1
        now = _utcnow()
        conn.executemany(
            "INSERT INTO holon_canonical (canonical_id, version, target_kind,"
            " target_id, label, status, derivation, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            [(cid, version, kind, str(ref), label, derivation, now)
             for kind, ref in targets],
        )
        if commit:
            conn.commit()
    except BaseException:
        if commit:
            conn.rollback()
        raise
    return cid


def split_canonical(conn: sqlite3.Connection, canonical_id: str,
                    groups: list[list[tuple[str, str | int]]], *,
                    commit: bool = True) -> list[str]:
    """Split a mistaken merge: partition current targets into ≥2 new entities."""
    current = get_canonical(conn, canonical_id)
    if current is None:
        raise KeyError(f"unknown canonical entity {canonical_id}")
    if len(groups) < 2 or any(not g for g in groups):
        raise ValueError("split needs at least 2 non-empty groups")
    flat = sorted(t for g in groups for t in
                  [(k, str(i)) for k, i in g])
    want = sorted((str(r["target_kind"]), str(r["target_id"]))
                  for r in current["targets"])
    if flat != want:
        raise ValueError("split groups must partition the current targets")
    try:
        conn.execute(
            "UPDATE holon_canonical SET status = 'superseded'"
            " WHERE canonical_id = ? AND version = ? AND status = 'active'",
            (canonical_id, current["version"]),
        )
        new_ids = []
        for group in groups:
            nid = uuid.uuid4().hex
            now = _utcnow()
            conn.executemany(
                "INSERT INTO holon_canonical (canonical_id, version, target_kind,"
                " target_id, label, status, derivation, created_at)"
                " VALUES (?, 1, ?, ?, ?, 'active', ?, ?)",
                [(nid, k, str(i), current["label"],
                  f"split from {canonical_id} v{current['version']}", now)
                 for k, i in group],
            )
            new_ids.append(nid)
        if commit:
            conn.commit()
    except BaseException:
        if commit:
            conn.rollback()
        raise
    return new_ids


def restore_version(conn: sqlite3.Connection, canonical_id: str,
                    version: int, *, commit: bool = True) -> int:
    """Re-activate a historical target set as a new version (reversible)."""
    rows = conn.execute(
        "SELECT target_kind, target_id, label, derivation FROM holon_canonical"
        " WHERE canonical_id = ? AND version = ?",
        (canonical_id, version)).fetchall()
    if not rows:
        raise KeyError(f"unknown canonical version {canonical_id} v{version}")
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM holon_canonical"
        " WHERE canonical_id = ?", (canonical_id,)).fetchone()[0]
    try:
        conn.execute(
            "UPDATE holon_canonical SET status = 'superseded'"
            " WHERE canonical_id = ? AND status = 'active'",
            (canonical_id,),
        )
        now = _utcnow()
        conn.executemany(
            "INSERT INTO holon_canonical (canonical_id, version, target_kind,"
            " target_id, label, status, derivation, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            [(canonical_id, int(current) + 1, str(r[0]), str(r[1]), str(r[2]),
              f"restore of v{version}: {r[3]}", now) for r in rows],
        )
        if commit:
            conn.commit()
    except BaseException:
        if commit:
            conn.rollback()
        raise
    return int(current) + 1


def get_canonical(conn: sqlite3.Connection, canonical_id: str,
                  version: int | None = None) -> dict | None:
    """Latest (or pinned) version of a canonical entity with its targets."""
    if version is None:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM holon_canonical"
            " WHERE canonical_id = ?", (canonical_id,)).fetchone()
        if int(row[0]) == 0:
            return None
        version = int(row[0])
    rows = conn.execute(
        "SELECT target_kind, target_id, label, status, derivation, created_at"
        " FROM holon_canonical WHERE canonical_id = ? AND version = ?",
        (canonical_id, version)).fetchall()
    if not rows:
        return None
    return {"canonical_id": canonical_id, "version": version,
            "label": str(rows[0][2]), "status": str(rows[0][3]),
            "derivation": str(rows[0][4]),
            "targets": [{"target_kind": str(r[0]), "target_id": str(r[1])}
                        for r in rows]}


def canonical_history(conn: sqlite3.Connection,
                      canonical_id: str) -> list[dict]:
    """All versions of a canonical entity, oldest first."""
    versions = [r[0] for r in conn.execute(
        "SELECT DISTINCT version FROM holon_canonical WHERE canonical_id = ?"
        " ORDER BY version", (canonical_id,)).fetchall()]
    return [get_canonical(conn, canonical_id, version=int(v))
            for v in versions]


def _atom_text(conn: sqlite3.Connection, atom_id: int) -> tuple[str, str]:
    row = conn.execute(
        "SELECT text, doc_id FROM atom WHERE id = ?", (atom_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown atom {atom_id}")
    return str(row[0]), str(row[1])


def _require_endpoint(conn: sqlite3.Connection, kind: str, ref: str) -> None:
    table = {"holon": "holons", "atom": "atom", "mention": "entity_mention",
             "assertion": "assertion"}.get(kind)
    if table is None:
        raise HolonError(f"unknown endpoint kind {kind!r}")
    col = {"holon": "holon_id", "atom": "id", "mention": "mention_id",
           "assertion": "assertion_id"}[kind]
    try:
        found = conn.execute(
            f"SELECT 1 FROM {table} WHERE {col} = ?", (ref,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise KeyError(f"unknown {kind} {ref} ({exc})") from exc
    if found is None:
        raise KeyError(f"unknown {kind} {ref}")
