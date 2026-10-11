"""R01 migration integrity (C1-4): legacy atoms migrate with preserved text + ID map.

Behavior-level tests against real SQLite files. Fixtures are bounded; databases
live in ``tmp_path`` so the repo stays clean.
"""

import hashlib
import sqlite3

import pytest

from trace_lite import source as S
from trace_lite.store import Database, migrate


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).digest()


@pytest.fixture()
def legacy_texts():
    return {
        f"doc-{i:02d}": f"legacy body {i} with unicode héllo ☃ and symbols *_`[] {i * 13}"
        for i in range(20)
    } | {
        "emoji-doc": "launch 🚀 moon 🌕 stars ✨ done",
        "crlf-doc": "line one\r\nline two\r\nline three\r\n",
        "frontmatter-doc": "---\ntitle: old note\ndraft: true\n---\nbody kept verbatim",
    }


@pytest.fixture()
def legacy_db_path(tmp_path, legacy_texts):
    path = tmp_path / "legacy.db"
    db = Database(path)
    db.bulk_ingest(list(legacy_texts.items()))
    db.close()
    return path, legacy_texts


def test_migrate_preserves_byte_exact_text_and_mapping(legacy_db_path):
    """C1-4: 100% text fidelity, full atom->revision map, backup, compat intact."""
    path, legacy_texts = legacy_db_path
    report = migrate.migrate_legacy(path)
    assert report["migrated"] is True
    assert report["migration_version"] == 1
    assert report["fidelity"] == 1.0
    assert report["mapped_atoms"] == report["legacy_atoms"] == len(legacy_texts)
    assert report["legacy_revisions"] == len(legacy_texts)
    assert report["backup"] is not None and report["backup"].endswith(".bak-001")

    # Backup is a valid pre-migration snapshot: original atoms, no migrated rows.
    backup = sqlite3.connect(report["backup"])
    try:
        assert backup.execute("SELECT COUNT(*) FROM atom").fetchone()[0] == len(legacy_texts)
        tables = {r[0] for r in backup.execute("SELECT name FROM sqlite_master").fetchall()}
        if "source" in tables:
            assert backup.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 0
    finally:
        backup.close()

    db = Database(path)
    try:
        assert db.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 1
        # One legacy revision per doc_id, byte-exact, honestly labeled.
        for doc_id, text in legacy_texts.items():
            cur = S.get_current(db.conn, doc_id)
            assert cur is not None, doc_id
            assert cur["text"] == text
            assert cur["text"].encode("utf-8") == text.encode("utf-8")
            assert _sha(cur["text"]) == bytes(cur["content_hash"])
            assert cur["coordinate_space"] == "legacy_atom_text"
            assert cur["atom_id"] is not None  # single-atom doc keeps its lexical row
        # Mapping covers every legacy atom id.
        mapped = {
            r[0] for r in db.conn.execute("SELECT atom_id FROM legacy_atom_map").fetchall()
        }
        atom_ids = {r[0] for r in db.conn.execute("SELECT id FROM atom").fetchall()}
        assert mapped == atom_ids
        # Compat shim: every existing Database method still works.
        assert db.count_atoms() == len(legacy_texts)
        assert db.get_atom(sorted(atom_ids)[0])["text"] in set(legacy_texts.values())
        assert db.find_atoms_by_hash(_sha("launch 🚀 moon 🌕 stars ✨ done"))[0]["doc_id"] == "emoji-doc"
        assert {h["doc_id"] for h in db.search_fts("legacy body", limit=50)} >= {"doc-00"}
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert S.verify_store(db.conn)["ok"] is True
    finally:
        db.close()


def test_migrate_multi_atom_doc_single_revision_with_offsets(tmp_path):
    """C1-4: a multi-atom doc yields one revision; each atom stays byte-exact."""
    path = tmp_path / "multi.db"
    db = Database(path)
    pieces = ["first piece alpha ☃", "second piece beta\r\nwith crlf", "third piece gamma 🚀"]
    for piece in pieces:
        db.insert_atom("multi-doc", piece)
    db.close()

    report = migrate.migrate_legacy(path)
    assert report["fidelity"] == 1.0
    assert report["legacy_revisions"] == 1

    db = Database(path)
    try:
        cur = S.get_current(db.conn, "multi-doc")
        assert cur["text"] == "\n".join(pieces)
        rows = db.conn.execute(
            "SELECT atom_id, start_char, end_char FROM legacy_atom_map"
            " ORDER BY start_char"
        ).fetchall()
        assert len(rows) == 3
        for (atom_id, start, end), piece in zip(rows, pieces):
            assert cur["text"][start:end] == piece
            original = db.get_atom(atom_id)["text"]
            assert original == piece
    finally:
        db.close()


def test_migrate_rerun_is_safe_and_reverifies(legacy_db_path):
    """Migration is versioned and idempotent: rerun skips backfill, keeps parity."""
    path, legacy_texts = legacy_db_path
    first = migrate.migrate_legacy(path)
    assert first["migrated"] is True
    second = migrate.migrate_legacy(path)
    assert second["migrated"] is False
    assert second["fidelity"] == 1.0
    assert second["mapped_atoms"] == len(legacy_texts)
    db = Database(path)
    try:
        assert db.conn.execute("SELECT COUNT(*) FROM source_revision").fetchone()[0] == len(
            legacy_texts
        )
    finally:
        db.close()


def test_migrate_empty_database(tmp_path):
    """An empty store is already at 001 via DDL: nothing to backfill, fidelity vacuous."""
    path = tmp_path / "empty.db"
    Database(path).close()
    report = migrate.migrate_legacy(path)
    assert report["migrated"] is False
    assert report["fidelity"] == 1.0
    assert report["legacy_atoms"] == 0
    assert report["migration_version"] == 1


def test_publish_after_migrate_supersedes_but_retains_legacy(tmp_path, legacy_texts):
    """Replacement of a migrated source retains the legacy revision honestly."""
    path = tmp_path / "legacy.db"
    db = Database(path)
    db.bulk_ingest(list(legacy_texts.items()))
    db.close()
    migrate.migrate_legacy(path)

    db = Database(path)
    try:
        legacy_cur = S.get_current(db.conn, "doc-00")
        new = S.publish(db, "doc-00", "reingested fresh text for doc zero")
        assert new.supersedes == legacy_cur["revision_id"]
        assert S.get_current(db.conn, "doc-00")["revision_id"] == new.revision_id
        old = S.get_revision(db.conn, legacy_cur["revision_id"])
        assert old["status"] == "superseded"
        assert old["text"] == legacy_texts["doc-00"]  # retained byte-exact
        assert old["coordinate_space"] == "legacy_atom_text"
        assert S.search_current(db.conn, "reingested", "default")[0]["doc_id"] == "doc-00"
        assert S.search_current(db.conn, "legacy body", "default", limit=100) != []
        assert S.verify_store(db.conn)["ok"] is True
    finally:
        db.close()


def test_migrated_ids_reject_expected_revision_races(tmp_path):
    """Obsolete-worker rejection also holds for migrated sources (gate 5)."""
    path = tmp_path / "race.db"
    db = Database(path)
    db.insert_atom("race-doc", "legacy race text")
    db.close()
    migrate.migrate_legacy(path)
    db = Database(path)
    try:
        legacy_cur = S.get_current(db.conn, "race-doc")
        v2 = S.publish(
            db, "race-doc", "fresh race text", expected_revision=legacy_cur["revision_id"]
        )
        with pytest.raises(S.ObsoleteRevisionError):
            S.publish(db, "race-doc", "stale race text",
                      expected_revision=legacy_cur["revision_id"])
        assert S.get_current(db.conn, "race-doc")["revision_id"] == v2.revision_id
    finally:
        db.close()
