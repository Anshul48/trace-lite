"""R04 source-local graphs: persistence, provenance, migration-003 (C4-1..C4-4).

Behavior-level tests against real SQLite files. Fixtures are bounded; every
database lives in ``tmp_path`` so the repo stays clean.
"""

import sqlite3

import pytest

from trace_lite import source as S
from trace_lite.graph import (
    check_integrity,
    create_extraction_run,
    extract_spans,
    get_graph,
    persist_graph,
)
from trace_lite.source import views as V
from trace_lite.store import Database, migrate

COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = ("Under high contention, the batching benefit disappears;"
          " throughput matches the unbatched path.")
RESTORES_PROBE = "Batching restores throughput benefit under high contention."
NEG_A = "WAL mode allows one writer with concurrent readers."
NEG_B = ("The legacy rollback-journal mode does NOT allow concurrent readers"
         " during a write.")


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "graph.db")
    yield database
    database.close()


def _build_run(db, source_id, text, run_id="run-test"):
    """Publish -> segment -> extract -> persist; returns (view, run_id)."""
    rev = S.publish(db, source_id, text)
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    assert view["status"] == "complete" and view["span_ids"], source_id
    spans = V.get_spans(db.conn, view["view_id"])
    result = extract_spans(spans, run_id=run_id)
    rid = create_extraction_run(db.conn, rev.revision_id, view["view_id"],
                                run_id=run_id)
    counts = persist_graph(db.conn, result, run_id=rid,
                           revision_id=rev.revision_id)
    return view, rid, result, counts


def test_migration_003_additive_and_rerunnable(tmp_path):
    """Migration-003 adds graph tables only; rows survive; rerun safe."""
    path = tmp_path / "graph003.db"
    db = Database(path)
    db.bulk_ingest([("doc-a", "alpha héllo 🚀"), ("doc-b", "beta text")])
    db.close()
    assert migrate.migrate_legacy(path)["migration_version"] == 1
    assert migrate.migrate_spans_002(path)["migration_version"] == 2
    report = migrate.migrate_graph_003(path)
    assert report["migration_version"] == 3
    assert report["tables"] == ["assertion", "assertion_argument", "dependency",
                                "entity_mention", "extraction_run", "relation"]
    assert report["backup"] is not None and report["backup"].endswith(".bak-003")
    again = migrate.migrate_graph_003(path)
    assert again["migration_version"] == 3
    assert again["observed_version"] == 3
    db = Database(path)
    try:
        assert db.conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
        assert db.count_atoms() == 2
        assert S.get_current(db.conn, "doc-a")["text"] == "alpha héllo 🚀"
        # Pre-003 writers still work on the migrated file.
        rev = S.publish(db, "doc-c", "Fresh body improves things.\n")
        view = V.create_segmentation_view(db.conn, rev.revision_id)
        assert view["span_count"] == 1
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def test_migration_003_preserves_001_002_version_observations(tmp_path):
    """Static DDL carries no version-3 row: 001/002 callers keep their MAX."""
    path = tmp_path / "versions.db"
    db = Database(path)
    db.bulk_ingest([("doc-a", "alpha")])
    db.close()
    migrate.migrate_legacy(path)
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 1
    finally:
        conn.close()
    migrate.migrate_spans_002(path)
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_condition_graph_persisted_with_qualifier_edge(db):
    """C4-1/C4-3: cond-a frame + condition edge survive the round trip."""
    _view, rid, _result, counts = _build_run(db, "cond-a", COND_A)
    assert counts["assertions"] == 1 and counts["relations"] == 2
    graph = get_graph(db.conn, rid)
    (row,) = graph["assertions"]
    assert row["subject_text"] == "Batching" and row["predicate"] == "improves"
    assert row["condition_text"] == "under low contention"
    assert row["units"] == "30%" and row["polarity"] == "positive"
    assert row["epistemic_status"] == "source_asserted"
    assert row["version"] == 1
    roles = {(a["role"], a["target_text"]) for a in graph["arguments"]}
    assert ("subject", "Batching") in roles
    assert ("condition", "under low contention") in roles
    assert ("unit", "30%") in roles
    assert ("qualifier", "fewer syscalls per item") in roles
    assert any(r["rel_type"] == "qualifies"
               and r["subject_text"] == "under low contention"
               and r["object_assertion_id"] == row["assertion_id"]
               for r in graph["relations"])


def test_provenance_every_relation_resolves(db):
    """C4-2: 100% of relations resolve via excerpt_span; zero orphans."""
    _view, rid, _result, _counts = _build_run(db, "cond-b", COND_B)
    report = check_integrity(db.conn, rid)
    assert report["orphans"] == [] and report["unanchored"] == []
    assert report["missing_dependency"] == []
    assert report["ok"] is True
    graph = get_graph(db.conn, rid)
    assert graph["relations"], "expected edges from the contention passage"
    for edge in graph["relations"]:
        excerpt = V.excerpt_span(db.conn, edge["span_id"])
        assert excerpt, edge["relation_id"]
        raw = excerpt.decode("utf-8")
        if edge["subject_text"]:
            assert edge["subject_text"] in raw or edge["rel_type"] == "qualifies"


def test_opposing_assertions_coexist_unmerged(db):
    """C4-1: positive and negative contention claims are separate rows."""
    _v1, r1, _r1, _c1 = _build_run(db, "cond-a", COND_A, run_id="run-a")
    _v2, r2, _r2, _c2 = _build_run(db, "cond-b", COND_B, run_id="run-b")
    pos = db.conn.execute(
        "SELECT * FROM assertion WHERE run_id = ?", (r1,)).fetchall()
    neg = db.conn.execute(
        "SELECT * FROM assertion WHERE run_id = ? AND polarity = 'negative'",
        (r2,)).fetchall()
    assert len(pos) == 1 and len(neg) == 1
    assert pos[0]["assertion_id"] != neg[0]["assertion_id"]
    assert check_integrity(db.conn, r1)["ok"] is True
    assert check_integrity(db.conn, r2)["ok"] is True


def test_restores_probe_keeps_condition(db):
    """C4-3: 'restores benefit under contention' retains its qualifier."""
    _view, rid, _result, _counts = _build_run(db, "cond-c", RESTORES_PROBE)
    graph = get_graph(db.conn, rid)
    frames = [a for a in graph["assertions"] if a["predicate"] == "restores"]
    assert len(frames) == 1
    assert frames[0]["condition_text"] == "under high contention"
    assert any(r["rel_type"] == "qualifies"
               and r["subject_text"] == "under high contention"
               for r in graph["relations"])


def test_negation_pair_polarity(db):
    _v1, r1, _a, _c = _build_run(db, "neg-a", NEG_A, run_id="run-neg-a")
    _v2, r2, _b, _d = _build_run(db, "neg-b", NEG_B, run_id="run-neg-b")
    pos = db.conn.execute(
        "SELECT polarity FROM assertion WHERE run_id = ?", (r1,)).fetchall()
    assert [r[0] for r in pos] == ["positive"]
    neg = db.conn.execute(
        "SELECT polarity, temporal_scope FROM assertion WHERE run_id = ?",
        (r2,)).fetchall()
    assert neg[0][0] == "negative" and neg[0][1] == "during a write"


def test_dependency_rows_cover_every_derived_record(db):
    """Every assertion/mention/relation has span+run dependency rows (R06)."""
    _view, rid, result, _counts = _build_run(db, "cond-a", COND_A)
    derived = ([a.assertion_id for a in result.assertions]
               + [m.mention_id for a in result.assertions for m in a.mentions]
               + [e.relation_id for e in result.relations])
    assert derived, "expected derived records"
    for did in derived:
        rows = db.conn.execute(
            "SELECT requires_kind FROM dependency WHERE derived_id = ?",
            (did,)).fetchall()
        kinds = {r[0] for r in rows}
        assert {"span", "run"} <= kinds, did
    # Reverse index: dependents of the source span are reachable (R06 input).
    span_id = result.assertions[0].span_id
    dependents = db.conn.execute(
        "SELECT COUNT(*) FROM dependency WHERE requires_kind = 'span'"
        " AND requires_id = ?", (span_id,)).fetchone()[0]
    assert dependents == len(
        [d for d in derived
         if db.conn.execute("SELECT 1 FROM dependency WHERE derived_id = ?"
                            " AND requires_id = ?", (d, span_id)).fetchone()])


def test_referential_integrity_rejects_orphan_edge(db):
    """FKs make orphan edges unwritable, not just detectable."""
    _view, rid, _result, _counts = _build_run(db, "cond-a", COND_A)
    graph = get_graph(db.conn, rid)
    edge = graph["relations"][0]
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO relation (relation_id, run_id, revision_id, rel_type,"
            " subject_mention_id, span_id, start_byte, end_byte, created_at)"
            " VALUES ('orphan-edge', ?, ?, 'improves', 'no-such-mention', ?, 0, 1,"
            " datetime('now'))",
            (rid, edge["revision_id"], edge["span_id"]))
    db.conn.rollback()


def test_fallback_partial_run_persists_without_crash(db):
    """C4-4 (store half): malformed spans persist as a partial run, no raise."""
    rev = S.publish(db, "doc-hostile", "Batching improves throughput.\n")
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    spans = V.get_spans(db.conn, view["view_id"])
    hostile = list(spans) + [None, 42, {"text": 7}, {"span_id": "ghost"}]
    result = extract_spans(hostile, run_id="run-hostile")  # type: ignore[list-item]
    assert result.status == "partial" and result.assertions
    rid = create_extraction_run(db.conn, rev.revision_id, view["view_id"],
                                run_id="run-hostile", status=result.status)
    persist_graph(db.conn, result, run_id=rid, revision_id=rev.revision_id)
    stored = get_graph(db.conn, rid)
    assert stored["run"]["status"] == "partial"
    assert check_integrity(db.conn, rid)["ok"] is True


def test_unknown_run_raises_key_error(db):
    with pytest.raises(KeyError):
        get_graph(db.conn, "no-such-run")


def test_validated_status_rejected_without_lineage(db):
    """R04 emits no validated/hypothesized rows: the store refuses them."""
    from trace_lite.graph.frames import AssertionFrame
    rev = S.publish(db, "doc-lin", "Batching improves throughput.\n")
    view = V.create_segmentation_view(db.conn, rev.revision_id)
    spans = V.get_spans(db.conn, view["view_id"])
    result = extract_spans(spans, run_id="run-lin")
    result.assertions[0].epistemic_status = "validated"
    rid = create_extraction_run(db.conn, rev.revision_id, view["view_id"],
                                run_id="run-lin")
    with pytest.raises(ValueError, match="validation lineage"):
        persist_graph(db.conn, result, run_id=rid,
                      revision_id=rev.revision_id, commit=False)
    db.conn.rollback()
    assert isinstance(AssertionFrame, type)
