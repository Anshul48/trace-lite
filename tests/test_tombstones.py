"""P07 acceptance: generation-aware atom retirement with tombstones (A1 §1-§5).

Delete outcomes are return-codes (D6): ``deleted`` | ``already-retired`` |
``never-existed``; raise is forbidden for never-existed. ``retire_gen`` is an
interim opaque ``gen1:<id>`` label (D5) and is never parsed as identity here.
"""

import hashlib
import sqlite3

import pytest

from trace_lite.store import Database, migrate


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "tombs.db")
    yield database
    database.close()


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).digest()


def _atom_dump(conn):
    return [
        (r[0], r[1], bytes(r[2]), r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT id, doc_id, content_hash, start_byte, end_byte, text"
            " FROM atom ORDER BY id"
        ).fetchall()
    ]


def _tomb_dump(conn):
    return [
        (r[0], bytes(r[1]), r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT atom_id, content_hash, doc_id, retire_gen, reason, created_at"
            " FROM atom_tombstone ORDER BY atom_id"
        ).fetchall()
    ]


def _fts_probe(conn, terms):
    return {
        t: [
            r[0]
            for r in conn.execute(
                "SELECT rowid FROM fts_atoms WHERE fts_atoms MATCH ? ORDER BY rowid",
                (t,),
            ).fetchall()
        ]
        for t in terms
    }


PROBE_TERMS = ("quokka", "zyzzyva", "conservation", "taxonomy", "zzyzxqy")


def test_delete_retires_atom_atomically_with_tombstone(db):
    """One delete call: tombstone row + atom row gone + postings gone + index healthy."""
    keep = db.insert_atom("note-keep", "quokka habitat conservation notes")
    gone_text = "zyzzyva beetle taxonomy remarks"
    gone = db.insert_atom("note-gone", gone_text)
    assert [h["id"] for h in db.search_fts("zyzzyva")] == [gone]

    outcome = db.delete_atom(gone)

    assert outcome == "deleted"
    assert db.get_atom(gone) is None
    tomb = db.get_tombstone(gone)
    assert tomb is not None
    assert tomb["atom_id"] == gone
    assert bytes(tomb["content_hash"]) == _sha(gone_text)
    assert tomb["doc_id"] == "note-gone"
    assert tomb["retire_gen"].startswith("gen1:") and len(tomb["retire_gen"]) > 5
    assert tomb["reason"] == "retract"
    assert tomb["created_at"]
    assert db.get_tombstone(keep) is None
    assert db.count_atoms() == 1
    assert db.count_tombstones() == 1
    assert db.search_fts("zyzzyva") == []
    assert [h["id"] for h in db.search_fts("quokka")] == [keep]
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_retired_vs_unknown_resolution(db):
    """get_atom is None for both; only the tombstone table tells retired from unknown."""
    text = "zyzzyva beetle taxonomy remarks"
    retired = db.insert_atom("d1", text)
    assert db.delete_atom(retired) == "deleted"
    unknown = retired + 1000000

    assert db.get_atom(retired) is None
    assert db.get_tombstone(retired) is not None
    assert db.get_atom(unknown) is None
    assert db.get_tombstone(unknown) is None

    digest = _sha(text)
    assert db.find_atoms_by_hash(digest) == []  # live rows only, unchanged
    assert [t["atom_id"] for t in db.find_tombstones_by_hash(digest)] == [retired]
    assert [t["atom_id"] for t in db.find_tombstones_by_hash(digest.hex())] == [retired]
    assert db.find_tombstones_by_hash(_sha("nothing like this")) == []
    assert db.count_atoms() == 0
    assert db.count_tombstones() == 1


def test_redelete_returns_already_retired_with_zero_mutation(db):
    """P-REPLAY: re-delete is idempotent — no write, no new generation."""
    aid = db.insert_atom("d1", "zyzzyva beetle taxonomy remarks")
    assert db.delete_atom(aid) == "deleted"
    tomb_before = db.get_tombstone(aid)
    assert tomb_before is not None
    atoms_before = _atom_dump(db.conn)
    tombs_before = _tomb_dump(db.conn)
    gens_before = db.conn.execute("SELECT COUNT(*) FROM projection_generation").fetchone()[0]
    deltas_before = db.conn.execute("SELECT COUNT(*) FROM atom_delta").fetchone()[0]
    changes_before = db.conn.total_changes

    assert db.delete_atom(aid) == "already-retired"

    assert db.conn.total_changes == changes_before  # zero row mutation
    assert _atom_dump(db.conn) == atoms_before
    assert _tomb_dump(db.conn) == tombs_before
    assert db.get_tombstone(aid) == tomb_before  # same row, no new generation
    assert db.conn.execute("SELECT COUNT(*) FROM projection_generation").fetchone()[0] == gens_before
    assert db.conn.execute("SELECT COUNT(*) FROM atom_delta").fetchone()[0] == deltas_before
    assert db.count_tombstones() == 1


def test_delete_never_existed_is_explicit_noop(db):
    """P-REPLAY: unknown id is an explicit outcome — no raise, no write."""
    live = db.insert_atom("d1", "quokka habitat conservation notes")
    changes_before = db.conn.total_changes
    assert db.delete_atom(987654321) == "never-existed"  # must not raise (D6)
    assert db.conn.total_changes == changes_before
    assert db.count_atoms() == 1
    assert db.count_tombstones() == 0
    assert db.get_tombstone(987654321) is None
    assert db.get_atom(live)["text"] == "quokka habitat conservation notes"


def test_rebuild_fts_does_not_resurrect_retired(db):
    db.insert_atom("note-keep", "quokka habitat conservation notes")
    gone = db.insert_atom("note-gone", "zyzzyva beetle taxonomy remarks")
    assert db.delete_atom(gone) == "deleted"

    assert db.rebuild_fts() == 1

    assert db.search_fts("zyzzyva") == []
    assert db.get_atom(gone) is None
    assert db.get_tombstone(gone) is not None
    assert db.count_atoms() == 1
    assert db.count_tombstones() == 1
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_006_upgrades_preexisting_db(tmp_path):
    """Pre-006 file: backup, oracle equivalence, empty tombstones, old deletes stay unknown."""
    path = tmp_path / "pre006.db"
    db = Database(path)
    texts = {
        "doc-a": "quokka habitat conservation notes",
        "doc-b": "zyzzyva beetle taxonomy remarks",
        "doc-c": "plain filler sentence about filing",
    }
    ids = {doc: db.insert_atom(doc, text) for doc, text in texts.items()}
    db.close()

    # Simulate a pre-006 file: no tombstone table, plus one old-style
    # physical delete (FTS delete + row-drop, no tombstone — unrecoverable).
    raw = sqlite3.connect(str(path))
    try:
        raw.execute("DROP TABLE atom_tombstone")
        raw.commit()
        doomed = ids["doc-b"]
        original = raw.execute("SELECT text FROM atom WHERE id = ?", (doomed,)).fetchone()[0]
        raw.execute(
            "INSERT INTO fts_atoms(fts_atoms, rowid, text) VALUES('delete', ?, ?)",
            (doomed, original),
        )
        raw.execute("DELETE FROM atom WHERE id = ?", (doomed,))
        raw.commit()
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master").fetchall()}
        assert "atom_tombstone" not in tables
        pre_atoms = _atom_dump(raw)
        pre_fts = _fts_probe(raw, PROBE_TERMS)
        pre_version = raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        raw.close()

    report = migrate.migrate_tombstones_006(path)
    assert report["migrated"] is True
    assert report["migration_version"] == 6
    assert report["observed_version"] == 6
    assert report["pre_version"] == pre_version == 1
    assert report["backup"] is not None and report["backup"].endswith(".bak-006")
    assert report["tables"] == ["atom_tombstone"]
    assert report["scratch_mismatches"] == 0
    assert report["live_mismatches"] == 0

    # Backup is a valid pre-migration snapshot (no tombstone table, old atoms).
    backup = sqlite3.connect(report["backup"])
    try:
        btables = {r[0] for r in backup.execute("SELECT name FROM sqlite_master").fetchall()}
        assert "atom_tombstone" not in btables
        assert backup.execute("SELECT COUNT(*) FROM atom").fetchone()[0] == 2
    finally:
        backup.close()

    db = Database(path)
    try:
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
        assert _atom_dump(db.conn) == pre_atoms  # oracle (i): atoms byte-identical
        assert _fts_probe(db.conn, PROBE_TERMS) == pre_fts  # oracle (ii): FTS identical
        assert db.count_tombstones() == 0  # oracle (iii): table exists, empty
        assert db.get_atom(doomed) is None
        assert db.get_tombstone(doomed) is None  # pre-006 delete resolves unknown
        assert {h["doc_id"] for h in db.search_fts("quokka")} == {"doc-a"}
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def test_migration_006_rerun_reverifies_and_preserves_tombstones(tmp_path):
    path = tmp_path / "r006.db"
    db = Database(path)
    live = db.insert_atom("d1", "quokka notes stay live")
    soon = db.insert_atom("d2", "zyzzyva remarks to retire")
    db.close()

    first = migrate.migrate_tombstones_006(path)
    assert first["migrated"] is True

    db = Database(path)
    try:
        assert db.delete_atom(soon) == "deleted"
        tomb_before = _tomb_dump(db.conn)
        assert len(tomb_before) == 1
    finally:
        db.close()

    second = migrate.migrate_tombstones_006(path)
    assert second["migrated"] is False
    assert second["observed_version"] == 6

    db = Database(path)
    try:
        assert _tomb_dump(db.conn) == tomb_before  # live tombstone preserved
        assert db.get_atom(live)["text"] == "quokka notes stay live"
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 6
    finally:
        db.close()


def test_delete_doc_removes_all_atoms_and_returns_count(db):
    doomed = [db.insert_atom("doc-a", f"zyzzyva piece {i}") for i in range(3)]
    survivor = db.insert_atom("doc-b", "quokka notes stay live")

    assert db.delete_doc("doc-a") == 3

    assert all(db.get_atom(a) is None for a in doomed)
    assert all(db.get_tombstone(a) is not None for a in doomed)
    assert db.count_tombstones() == 3
    assert db.get_atom(survivor)["text"] == "quokka notes stay live"
    assert [h["id"] for h in db.search_fts("quokka")] == [survivor]
    assert db.delete_doc("missing-doc") == 0
    assert db.delete_doc("doc-a") == 0  # already retired: nothing left to count
    assert db.count_tombstones() == 3


def test_racing_delete_loser_reports_already_retired(db):
    """Deterministic race-path simulation: a tombstone committed between our
    lookup and INSERT (here pre-seeded) must yield already-retired, not a
    UNIQUE-constraint raise, with our partial txn rolled back."""
    target = db.insert_atom("doc-a", "quokka race piece")
    pre_atoms = _atom_dump(db.conn)
    pre_fts = _fts_probe(db.conn, PROBE_TERMS)
    db.conn.execute(
        "INSERT INTO atom_tombstone"
        " (atom_id, content_hash, doc_id, retire_gen, reason, created_at)"
        " VALUES (?, ?, ?, ?, 'retract', ?)",
        (target, _sha("quokka race piece"), "doc-a", "gen1:racewinner", "now"),
    )
    db.conn.commit()
    assert db.delete_atom(target) == "already-retired"
    assert _atom_dump(db.conn) == pre_atoms  # partial txn rolled back
    assert _fts_probe(db.conn, PROBE_TERMS) == pre_fts


# NOTE (coordinator): a threads-based same-id delete race test was tried here and
# REMOVED. Racing delete_atom calls from multiple connections fail with transient
# "database disk image is malformed" from the FTS5 external-content 'delete' path —
# IDENTICALLY on true-pristine (pre-existing hazard, not a P07 regression; final DB
# integrity holds). Multi-connection racing deletes violate the single-writer
# constraint (DES-11/P1); the deterministically testable conflict path (loser's
# tombstone INSERT vs winner's commit) is covered by
# test_racing_delete_loser_reports_already_retired above.
