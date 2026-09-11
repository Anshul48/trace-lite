"""P02 acceptance: canonical atom round-trip, WAL governor, external-content FTS5."""

import hashlib
import sqlite3

import pytest

from trace_lite.store import Database, WalGovernor, sha256_bytes


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "storage.db")
    yield database
    database.close()


def test_atom_round_trip_byte_exact_1000(db):
    """C01: 1,000 atoms retrievable by ID and by hash with 100% byte fidelity."""
    ids = []
    for i in range(1000):
        text = f"doc-{i:04d} canonical span with unicode \u00e9\u2603 and symbols *_`[] {i * 7}"
        ids.append(db.insert_atom(doc_id=f"doc-{i:04d}", text=text))
    assert db.count_atoms() == 1000
    for i, atom_id in enumerate(ids):
        text = f"doc-{i:04d} canonical span with unicode \u00e9\u2603 and symbols *_`[] {i * 7}"
        row = db.get_atom(atom_id)
        assert row is not None
        assert row["text"] == text
        assert row["text"].encode("utf-8") == text.encode("utf-8")
        assert row["start_byte"] == 0
        assert row["end_byte"] == len(text.encode("utf-8"))
        by_hash = db.find_atoms_by_hash(hashlib.sha256(text.encode()).digest())
        assert any(r["id"] == atom_id for r in by_hash)
        assert db.find_atoms_by_hash(hashlib.sha256(text.encode()).hexdigest())[0]["text"] == text
    assert sha256_bytes("abc") == hashlib.sha256(b"abc").digest()


def test_wal_governor_fires_post_commit_every_5k(tmp_path):
    """C02: 10,000 bulk docs trigger exactly 2 post-commit checkpoints, zero locks."""
    governor = WalGovernor(threshold=5000)
    with Database(tmp_path / "bulk.db", governor=governor) as db:
        assert db.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        docs = [(f"bulk-{i}", f"bulk document number {i} about filing cabinets") for i in range(10000)]
        db.bulk_ingest(docs)  # must not raise (no locked-table errors)
        assert len(governor.history) == 2
        assert governor.total_committed == 10000
        assert all(isinstance(r.checkpointed_frames, int) for r in governor.history)
        assert db.count_atoms() == 10000
        # Spot-check readability after checkpoint folding.
        assert db.get_atom(1)["doc_id"] == "bulk-0"
        assert db.get_atom(10000)["doc_id"] == "bulk-9999"


def test_governor_rejects_mid_transaction_checkpoint(db):
    """CRIT-04 guard: checkpoint inside an open write txn must fail; post-commit must pass."""
    db.conn.execute("BEGIN IMMEDIATE")
    db.conn.execute(
        "INSERT INTO atom (doc_id, content_hash, start_byte, end_byte, text)"
        " VALUES ('t', x'00', 0, 1, 'x')"
    )
    with pytest.raises(sqlite3.OperationalError):
        db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    db.conn.rollback()
    record = db.checkpoint_now()  # post-commit: must succeed
    assert record.total_committed == 0


def test_fts_external_content_bm25_no_duplication(db):
    """C03: FTS5 search returns BM25-ranked atoms; index stores no text copy."""
    db.insert_atom("d1", "the filing cabinet stores canonical atoms for retrieval")
    db.insert_atom("d2", "unrelated note about gardening and soil")
    db.insert_atom("d3", "retrieval over canonical atoms uses BM25 ranking")
    hits = db.search_fts("canonical atoms", limit=10)
    assert {h["doc_id"] for h in hits} == {"d1", "d3"}
    ranks = [h["bm25_rank"] for h in hits]
    assert ranks == sorted(ranks), "BM25 rank must order best-first"
    # External-content proof: FTS table definition references atom; it holds no shadow text.
    sql = db.fts_table_sql()
    assert "content='atom'" in sql.replace('"', "'")
    stored = db.conn.execute("SELECT DISTINCT text FROM fts_atoms").fetchall()
    assert {r[0] for r in stored} == {
        "the filing cabinet stores canonical atoms for retrieval",
        "unrelated note about gardening and soil",
        "retrieval over canonical atoms uses BM25 ranking",
    }  # reads through to atom (external content), not a second copy


def test_events_append_only_ordered(db):
    e1 = db.insert_event("s1", "atom.ingested", {"doc": "a"})
    e2 = db.insert_event("s1", "atom.ingested", {"doc": "b"})
    events = db.list_events("s1")
    assert [e["event_id"] for e in events] == [e1, e2]
    assert [e["stream_seq"] for e in events] == [0, 1]
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO events (event_id, stream_id, stream_seq, event_type, occurred_at)"
            " VALUES (?, 's1', 99, 'dup', 'now')",
            (e1,),
        )
