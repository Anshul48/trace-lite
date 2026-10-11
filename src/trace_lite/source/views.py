"""Versioned segmentation views over retained source revisions (R02).

An ``evidence_span`` row is immutable once written: ``span_id`` derives from
``(revision_id, run_id, ordinal, start_byte, end_byte)`` so re-segmentation
with a new run/config id mints new span IDs and never rewrites prior rows. A
``segmentation_view`` row groups one run's spans; alternatives coexist and prior
references keep resolving.

Failure rule (packet recovery): a segmentation failure produces a retained
revision with a ``status='partial'`` view row carrying zero spans — never
fabricated offsets.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .segmentation import RawSpan, segment_text, segmenter_version_for, verify_spans

CONFIG_DEFAULT = "structural-v1"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(text: str) -> bytes:
    return hashlib.sha256(text.encode("utf-8")).digest()


def _config_hash(config: str) -> str:
    return hashlib.sha256(config.encode("utf-8")).hexdigest()[:16]


def span_id_for(
    revision_id: str, run_id: str, ordinal: int, start_byte: int, end_byte: int
) -> str:
    """Deterministic, per-view span identity (repeats get separate ordinals)."""
    return hashlib.sha256(
        f"{revision_id}:{run_id}:{ordinal}:{start_byte}:{end_byte}".encode("utf-8")
    ).hexdigest()[:32]


def create_segmentation_view(
    conn: sqlite3.Connection,
    revision_id: str,
    *,
    config: str = CONFIG_DEFAULT,
    parser_version: str = "",
    body_byte_base: int = 0,
    signal: dict | None = None,
    status: str = "complete",
) -> dict:
    """Segment a retained revision and persist spans + a new view row.

    Reads the revision's retained text from ``source_revision`` (the canonical
    payload). ``body_byte_base`` + ``signal`` record the verified byte mapping
    when segmenting a transformed slice (e.g. a frontmatter-stripped body):
    body-relative offsets are shifted so stored spans address raw payload bytes.
    Returns ``{"view_id", "run_id", "revision_id", "span_ids", "status", ...}``.
    """
    row = conn.execute(
        "SELECT text FROM source_revision WHERE revision_id = ?", (revision_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown revision {revision_id!r}")
    raw_text = str(row[0])
    blob = raw_text.encode("utf-8")
    if body_byte_base:
        if not (0 <= body_byte_base <= len(blob)):
            raise ValueError("body_byte_base outside retained payload")
        base_text = blob[body_byte_base:].decode("utf-8")
    else:
        base_text = raw_text
    run_id = uuid.uuid4().hex
    segmenter_version = segmenter_version_for(config)
    try:
        raw_spans = segment_text(base_text, body_byte_base=body_byte_base)
        if not verify_spans(raw_text, raw_spans):
            raise ValueError("span byte verification failed")
    except BaseException:
        # Retained source, partial status, zero spans — never fabricated offsets.
        conn.execute(
            "INSERT INTO segmentation_view (view_id, revision_id, config, config_hash,"
            " segmenter_version, parser_version, span_count, status, signal_json,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, 0, 'partial', ?, ?)",
            (
                run_id,
                revision_id,
                config,
                _config_hash(config),
                segmenter_version,
                parser_version,
                json.dumps(signal or {}),
                _utcnow(),
            ),
        )
        conn.commit()
        return {
            "view_id": run_id,
            "run_id": run_id,
            "revision_id": revision_id,
            "span_ids": [],
            "status": "partial",
            "span_count": 0,
        }
    if status == "partial":
        raise ValueError("explicit status='partial' is reserved for failure paths")
    signal_json = json.dumps(
        {
            "body_byte_base": body_byte_base,
            "newline": "crlf" if "\r\n" in raw_text else "lf",
            **(signal or {}),
        }
    )
    conn.execute(
        "INSERT INTO segmentation_view (view_id, revision_id, config, config_hash,"
        " segmenter_version, parser_version, span_count, status, signal_json,"
        " created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (
            run_id,
            revision_id,
            config,
            _config_hash(config),
            segmenter_version,
            parser_version,
            status,
            signal_json,
            _utcnow(),
        ),
    )
    span_ids: list[str] = []
    # 1-hop parent envelope: nearest preceding heading span's ordinal.
    heading_ordinal: int | None = None
    for span in raw_spans:
        if span.kind == "heading":
            heading_ordinal = span.ordinal
            parent = None
        else:
            parent = heading_ordinal
        span_id = span_id_for(
            revision_id, run_id, span.ordinal, span.start_byte, span.end_byte
        )
        conn.execute(
            "INSERT INTO evidence_span (span_id, revision_id, view_id, ordinal,"
            " coordinate_space, start_byte, end_byte, kind, parent_ordinal, header,"
            " text_hash, parser_version, segmenter_version, status)"
            " VALUES (?, ?, ?, ?, 'raw_utf8_bytes', ?, ?, ?, ?, ?, ?, ?, ?, 'complete')",
            (
                span_id,
                revision_id,
                run_id,
                span.ordinal,
                span.start_byte,
                span.end_byte,
                span.kind,
                parent,
                span.header,
                _sha(span.text),
                parser_version,
                segmenter_version,
            ),
        )
        span_ids.append(span_id)
    conn.execute(
        "UPDATE segmentation_view SET span_count = ? WHERE view_id = ?",
        (len(span_ids), run_id),
    )
    conn.commit()
    return {
        "view_id": run_id,
        "run_id": run_id,
        "revision_id": revision_id,
        "span_ids": span_ids,
        "status": status,
        "span_count": len(span_ids),
        "segmenter_version": segmenter_version,
    }


def list_views(conn: sqlite3.Connection, revision_id: str) -> list[dict]:
    """All segmentation views for a revision, oldest first (history preserved)."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM segmentation_view WHERE revision_id = ?"
            " ORDER BY created_at, view_id",
            (revision_id,),
        ).fetchall()
    ]


def get_spans(conn: sqlite3.Connection, view_id: str) -> list[dict]:
    """Spans of one view in ordinal order, each with its resolved ``text``."""
    rows = conn.execute(
        "SELECT es.*, sr.text AS revision_text FROM evidence_span es"
        " JOIN source_revision sr ON sr.revision_id = es.revision_id"
        " WHERE es.view_id = ? ORDER BY es.ordinal",
        (view_id,),
    ).fetchall()
    spans: list[dict] = []
    for r in rows:
        blob = str(r["revision_text"]).encode("utf-8")
        text = blob[int(r["start_byte"]) : int(r["end_byte"])].decode("utf-8")
        span = {k: r[k] for k in r.keys() if k != "revision_text"}
        span["text"] = text
        spans.append(span)
    return spans


def excerpt_span(conn: sqlite3.Connection, span_id: str) -> bytes:
    """Resolve a span to its exact bytes (EVALUATION gate 1 primitive)."""
    row = conn.execute(
        "SELECT es.start_byte, es.end_byte, es.text_hash, sr.text AS revision_text"
        " FROM evidence_span es JOIN source_revision sr"
        " ON sr.revision_id = es.revision_id WHERE es.span_id = ?",
        (span_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown span {span_id!r}")
    blob = str(row["revision_text"]).encode("utf-8")
    excerpt = blob[int(row["start_byte"]) : int(row["end_byte"])]
    if _sha(excerpt.decode("utf-8")) != bytes(row["text_hash"]):
        raise ValueError(f"span {span_id!r} failed hash verification")
    return excerpt


def envelope(conn: sqlite3.Connection, span_id: str) -> dict:
    """1-hop discourse envelope: span + parent heading + ordinal neighbors (C2-4)."""
    row = conn.execute(
        "SELECT * FROM evidence_span WHERE span_id = ?", (span_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown span {span_id!r}")
    view_id = str(row["view_id"])
    ordinal = int(row["ordinal"])
    siblings = {int(r["ordinal"]): dict(r) for r in get_spans(conn, view_id)}
    parent = None
    if row["parent_ordinal"] is not None and int(row["parent_ordinal"]) in siblings:
        parent = siblings[int(row["parent_ordinal"])]
    return {
        "span": dict(row),
        "parent": parent,
        "prev": siblings.get(ordinal - 1),
        "next": siblings.get(ordinal + 1),
    }


def spans_to_dicts(spans: list[RawSpan]) -> list[dict]:
    """Render pure ``RawSpan`` values as JSON-serializable dicts."""
    return [
        {
            "ordinal": s.ordinal,
            "start_byte": s.start_byte,
            "end_byte": s.end_byte,
            "kind": s.kind,
            "header": s.header,
            "chars": len(s.text),
            "text": s.text,
        }
        for s in spans
    ]
