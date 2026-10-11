"""P05 contract: sync_notes publishes a filing generation iff any created result.

Decision D2 (build/DECISIONS.md): the sync_notes docstring promises "No-op
batches (nothing synced) publish nothing", matching workers._maybe_publish
(publish iff real projections completed). An all-replay batch (every
source_service.publish result has created=False) must not publish; any
batch with at least one created result must publish exactly one generation.
"""

from __future__ import annotations

from trace_lite.api.app import FILING_GENERATION_KIND, sync_notes
from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.store import generations as gen_store
from trace_lite.store.database import Database


def _setup(tmp_path, name="p05.db"):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    db = Database(tmp_path / name)
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    return db, taxonomy, engine, vault


def _filing_count(db):
    return len(gen_store.list_generations(db.conn, FILING_GENERATION_KIND))


def test_all_replay_batch_publishes_nothing(tmp_path):
    """All-replay batch: generation count + active id unchanged; replays still counted."""
    db, taxonomy, engine, vault = _setup(tmp_path)
    try:
        a = vault / "a.md"
        b = vault / "b.md"
        a.write_text("# A\n\nAlpha note.\n", encoding="utf-8")
        b.write_text("# B\n\nBeta note.\n", encoding="utf-8")
        assert sync_notes(db, taxonomy, engine, vault, [a, b]) == 2
        before = _filing_count(db)
        assert before >= 1
        active_before = gen_store.get_active(db.conn, FILING_GENERATION_KIND)
        # Unchanged files -> every publish result is a replay (created=False).
        assert sync_notes(db, taxonomy, engine, vault, [a, b]) == 2
        assert _filing_count(db) == before
        active_after = gen_store.get_active(db.conn, FILING_GENERATION_KIND)
        assert active_after is not None and active_before is not None
        assert active_after["generation_id"] == active_before["generation_id"]
    finally:
        db.close()


def test_mixed_batch_publishes(tmp_path):
    """Any-created batch (one replay + one new revision) publishes one generation."""
    db, taxonomy, engine, vault = _setup(tmp_path)
    try:
        a = vault / "a.md"
        b = vault / "b.md"
        a.write_text("# A\n\nAlpha note.\n", encoding="utf-8")
        b.write_text("# B\n\nBeta note.\n", encoding="utf-8")
        assert sync_notes(db, taxonomy, engine, vault, [a, b]) == 2
        before = _filing_count(db)
        b.write_text("# B\n\nBeta note revised.\n", encoding="utf-8")
        assert sync_notes(db, taxonomy, engine, vault, [a, b]) == 2
        assert _filing_count(db) == before + 1
    finally:
        db.close()


def test_all_created_batch_publishes(tmp_path):
    """Fresh all-created batch publishes (guards the gate against over-suppression)."""
    db, taxonomy, engine, vault = _setup(tmp_path)
    try:
        a = vault / "a.md"
        a.write_text("# A\n\nAlpha note.\n", encoding="utf-8")
        before = _filing_count(db)
        assert sync_notes(db, taxonomy, engine, vault, [a]) == 1
        assert _filing_count(db) == before + 1
    finally:
        db.close()
