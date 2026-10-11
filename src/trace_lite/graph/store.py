"""Persistence for source-local graphs (R04): runs, mentions, assertions, relations.

Insert order follows the foreign keys (run → mentions/assertions → arguments
→ relations); dependency rows are written for every derived record so R06 can
invalidate by input version. :func:`check_integrity` enforces the zero-orphan
contract: every anchored row resolves through the R02 ``excerpt_span``
primitive and every typed endpoint points at a stored row.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .extractor import EXTRACTOR_NAME, EXTRACTOR_VERSION, SCHEMA_VERSION, ExtractResult
from .frames import (
    MENTION_ROLES,
    REL_TYPES,
    STATUS_ASSERTED,
    STATUS_UNRESOLVED,
    AssertionFrame,
    RelationEdge,
)

_VALID_STATUSES = frozenset({STATUS_ASSERTED, STATUS_UNRESOLVED})
_VALID_EPISTEMIC = frozenset({"source_asserted", "validated", "hypothesized"})
_VALID_POLARITY = frozenset({"positive", "negative"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _config_hash(config: str) -> str:
    return hashlib.sha256(config.encode("utf-8")).hexdigest()[:16]


def create_extraction_run(
    conn: sqlite3.Connection,
    revision_id: str,
    view_id: str,
    *,
    config: str = "structural-v1",
    status: str = "complete",
    error: str | None = None,
    run_id: str | None = None,
) -> str:
    """Insert an ``extraction_run`` row; returns the run id."""
    rid = run_id or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO extraction_run (run_id, revision_id, view_id, extractor_name,"
        " extractor_version, schema_version, config_hash, status, error,"
        " span_count, assertion_count, relation_count, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?)",
        (
            rid, revision_id, view_id, EXTRACTOR_NAME, EXTRACTOR_VERSION,
            SCHEMA_VERSION, _config_hash(config), status, error, _utcnow(),
        ),
    )
    return rid


def persist_graph(
    conn: sqlite3.Connection,
    result: ExtractResult,
    *,
    run_id: str,
    revision_id: str,
    commit: bool = True,
) -> dict:
    """Persist frames/edges/mentions plus dependency rows. Returns counts."""
    _validate_result(result)
    mention_assertion: dict[str, str] = {}
    for frame in result.assertions:
        mention_assertion.update({m.mention_id: frame.assertion_id
                                  for m in frame.mentions})
    try:
        for frame in result.assertions:
            conn.execute(
                "INSERT INTO assertion (assertion_id, version, run_id, revision_id,"
                " predicate, polarity, modality, epistemic_status, subject_text,"
                " object_text, condition_text, units, temporal_scope, version_scope,"
                " span_id, start_byte, end_byte, status, created_at)"
                " VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    frame.assertion_id, run_id, revision_id, frame.predicate,
                    frame.polarity, frame.modality, frame.epistemic_status,
                    frame.subject_text, frame.object_text, frame.condition_text,
                    frame.units, frame.temporal_scope, frame.version_scope,
                    frame.span_id, frame.start_byte, frame.end_byte,
                    frame.status, _utcnow(),
                ),
            )
            for pos, mention in enumerate(frame.mentions):
                conn.execute(
                    "INSERT INTO entity_mention (mention_id, run_id, revision_id,"
                    " span_id, start_byte, end_byte, surface, role, canonical_key,"
                    " mapping_version, status)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        mention.mention_id, run_id, revision_id, mention.span_id,
                        mention.start_byte, mention.end_byte, mention.surface,
                        mention.role, mention.canonical_key,
                        mention.mapping_version, mention.status,
                    ),
                )
                conn.execute(
                    "INSERT INTO assertion_argument (argument_id, assertion_id,"
                    " role, target_mention_id, target_text, position)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        f"{frame.assertion_id}:{pos:02d}", frame.assertion_id,
                        mention.role, mention.mention_id, mention.surface, pos,
                    ),
                )
        for edge in result.relations:
            conn.execute(
                "INSERT INTO relation (relation_id, run_id, revision_id, rel_type,"
                " subject_mention_id, subject_assertion_id, object_mention_id,"
                " object_assertion_id, subject_text, object_text, polarity,"
                " condition_text, epistemic_status, span_id, start_byte, end_byte,"
                " status, candidates_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    edge.relation_id, run_id, revision_id, edge.rel_type,
                    edge.subject_mention_id, edge.subject_assertion_id,
                    edge.object_mention_id, edge.object_assertion_id,
                    edge.subject_text, edge.object_text, edge.polarity,
                    edge.condition_text, edge.epistemic_status, edge.span_id,
                    edge.start_byte, edge.end_byte, edge.status,
                    json.dumps(edge.candidates), _utcnow(),
                ),
            )
        # Dependency rows for every derived record (R06 invalidation input).
        for frame in result.assertions:
            _add_dependency(conn, "assertion", frame.assertion_id,
                            "span", frame.span_id)
            _add_dependency(conn, "assertion", frame.assertion_id,
                            "revision", revision_id)
            _add_dependency(conn, "assertion", frame.assertion_id,
                            "run", run_id)
            for mention in frame.mentions:
                _add_dependency(conn, "mention", mention.mention_id,
                                "span", mention.span_id)
                _add_dependency(conn, "mention", mention.mention_id,
                                "run", run_id)
        for edge in result.relations:
            _add_dependency(conn, "relation", edge.relation_id,
                            "span", edge.span_id)
            _add_dependency(conn, "relation", edge.relation_id,
                            "run", run_id)
        _add_dependency(conn, "run", run_id, "revision", revision_id)
        counts = {
            "assertions": len(result.assertions),
            "mentions": sum(len(f.mentions) for f in result.assertions),
            "relations": len(result.relations),
        }
        conn.execute(
            "UPDATE extraction_run SET assertion_count = ?, relation_count = ?,"
            " span_count = ?, status = ? WHERE run_id = ?",
            (counts["assertions"], counts["relations"], result.spans_seen,
             result.status, run_id),
        )
        if commit:
            conn.commit()
        return counts
    except BaseException:
        if commit:
            conn.rollback()
        raise


def _add_dependency(conn: sqlite3.Connection, derived_kind: str, derived_id: str,
                    requires_kind: str, requires_id: str) -> None:
    conn.execute(
        "INSERT INTO dependency (derived_kind, derived_id, requires_kind,"
        " requires_id, dependency_kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (derived_kind, derived_id, requires_kind, requires_id,
         requires_kind, _utcnow()),
    )


def _validate_result(result: ExtractResult) -> None:
    for frame in result.assertions:
        _require(frame.epistemic_status in _VALID_EPISTEMIC, "epistemic_status",
                 frame.assertion_id)
        _require(frame.polarity in _VALID_POLARITY, "polarity", frame.assertion_id)
        _require(frame.status in _VALID_STATUSES, "status", frame.assertion_id)
        if frame.epistemic_status != "source_asserted":
            raise ValueError(
                f"assertion {frame.assertion_id}: R04 emits no "
                f"{frame.epistemic_status} frames without validation lineage"
            )
        for mention in frame.mentions:
            if mention.role not in MENTION_ROLES:
                raise ValueError(f"unknown mention role {mention.role!r}")
    for edge in result.relations:
        if edge.rel_type not in REL_TYPES:
            raise ValueError(f"unknown rel_type {edge.rel_type!r}")
        _require(edge.status in _VALID_STATUSES, "status", edge.relation_id)
        _require(edge.epistemic_status in _VALID_EPISTEMIC, "epistemic_status",
                 edge.relation_id)
        if edge.epistemic_status != "source_asserted":
            raise ValueError(
                f"relation {edge.relation_id}: R04 emits no "
                f"{edge.epistemic_status} edges without validation lineage"
            )


def _require(ok: bool, what: str, rid: str) -> None:
    if not ok:
        raise ValueError(f"row {rid}: invalid {what}")


def check_integrity(conn: sqlite3.Connection, run_id: str) -> dict:
    """Zero-orphan audit for one extraction run.

    Verifies: every assertion/mention/relation anchor resolves through the
    R02 byte-exact excerpt path; every typed endpoint references a stored
    row; every derived record has dependency coverage. Returns a report with
    ``orphans`` / ``unanchored`` lists (both must be empty) and ``ok``.
    """
    from trace_lite.source.views import excerpt_span

    orphans: list[str] = []
    unanchored: list[str] = []
    assertions = conn.execute(
        "SELECT * FROM assertion WHERE run_id = ?", (run_id,)).fetchall()
    mentions = conn.execute(
        "SELECT * FROM entity_mention WHERE run_id = ?", (run_id,)).fetchall()
    relations = conn.execute(
        "SELECT * FROM relation WHERE run_id = ?", (run_id,)).fetchall()
    assertion_ids = {r["assertion_id"] for r in assertions}
    mention_ids = {r["mention_id"] for r in mentions}

    def _anchor_ok(kind: str, rid: str, span_id: str) -> None:
        try:
            excerpt_span(conn, str(span_id))
        except Exception as exc:
            unanchored.append(f"{kind}:{rid} span {span_id}: {exc}")

    for row in assertions:
        _anchor_ok("assertion", str(row["assertion_id"]), str(row["span_id"]))
    for row in mentions:
        _anchor_ok("mention", str(row["mention_id"]), str(row["span_id"]))
    for row in relations:
        rid = str(row["relation_id"])
        _anchor_ok("relation", rid, str(row["span_id"]))
        for side, mid, aid in (
            ("subject", row["subject_mention_id"], row["subject_assertion_id"]),
            ("object", row["object_mention_id"], row["object_assertion_id"]),
        ):
            if mid is not None and str(mid) not in mention_ids:
                orphans.append(f"relation:{rid} {side}_mention {mid} missing")
            if aid is not None and str(aid) not in assertion_ids:
                orphans.append(f"relation:{rid} {side}_assertion {aid} missing")
    args = conn.execute(
        "SELECT aa.* FROM assertion_argument aa JOIN assertion a"
        " ON a.assertion_id = aa.assertion_id WHERE a.run_id = ?",
        (run_id,)).fetchall()
    for row in args:
        if str(row["target_mention_id"]) not in mention_ids:
            orphans.append(
                f"argument:{row['argument_id']} mention"
                f" {row['target_mention_id']} missing")
        if str(row["assertion_id"]) not in assertion_ids:
            orphans.append(
                f"argument:{row['argument_id']} assertion"
                f" {row['assertion_id']} missing")
    derived = (
        [(str(r["assertion_id"])) for r in assertions]
        + [(str(r["mention_id"])) for r in mentions]
        + [(str(r["relation_id"])) for r in relations]
    )
    covered = {r[0] for r in conn.execute(
        "SELECT derived_id FROM dependency WHERE derived_id IN (%s)"
        % ",".join("?" * len(derived)), derived).fetchall()} if derived else set()
    missing_dep = sorted(set(derived) - covered)
    report = {
        "run_id": run_id,
        "assertions": len(assertions),
        "mentions": len(mentions),
        "relations": len(relations),
        "arguments": len(args),
        "orphans": orphans,
        "unanchored": unanchored,
        "missing_dependency": missing_dep,
        "ok": not orphans and not unanchored and not missing_dep,
    }
    return report


def get_graph(conn: sqlite3.Connection, run_id: str) -> dict:
    """Load one run's graph rows (assertions, arguments, relations, run)."""
    run = conn.execute(
        "SELECT * FROM extraction_run WHERE run_id = ?", (run_id,)).fetchone()
    if run is None:
        raise KeyError(f"unknown run {run_id!r}")
    return {
        "run": dict(run),
        "assertions": [dict(r) for r in conn.execute(
            "SELECT * FROM assertion WHERE run_id = ? ORDER BY rowid",
            (run_id,)).fetchall()],
        "mentions": [dict(r) for r in conn.execute(
            "SELECT * FROM entity_mention WHERE run_id = ? ORDER BY rowid",
            (run_id,)).fetchall()],
        "arguments": [dict(r) for r in conn.execute(
            "SELECT aa.* FROM assertion_argument aa JOIN assertion a"
            " ON a.assertion_id = aa.assertion_id WHERE a.run_id = ?"
            " ORDER BY aa.position", (run_id,)).fetchall()],
        "relations": [dict(r) for r in conn.execute(
            "SELECT * FROM relation WHERE run_id = ? ORDER BY rowid",
            (run_id,)).fetchall()],
    }
