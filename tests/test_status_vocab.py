"""P10 (DEC-01 Option A): holon status-vocabulary contract.

DEC-01 Option A keeps both non-active ``holon_state.status`` tokens (``stale``
from the R06 engine path, ``invalidated`` from the R05 holon path) and hardens
them behind named constants. This file pins that contract. It introduces NO
behavior change: healing paths (repair/revalidate) are untouched — lifecycle
interchange belongs to N12.

RED-vs-PIN honesty: the behavior tests below pin current-correct behavior and
pass identically on the unpatched and patched trees (PIN). Only the
new-constant tests are expected RED (ImportError/AttributeError, or a failed
equality for the fixed constant) on the unpatched tree.
"""

from __future__ import annotations

from trace_lite.api import app as api
from trace_lite.filing.engine import FilingEngine
from trace_lite.filing.holon import HolonStore
from trace_lite.store.database import Database


def _seed_mixed_statuses(tmp_path):
    """One ``stale`` holon (R06 engine path) + one ``invalidated`` (R05 path).

    Uses the production write paths, not raw SQL: ``stale`` via
    ``FilingEngine.invalidate_dependents``, ``invalidated`` via the
    ``remove_members`` dependent cascade.
    """
    db = Database(str(tmp_path / "status_vocab.db"))
    store = HolonStore(db.conn)
    engine = FilingEngine(db.conn)
    a1 = db.insert_atom("s.md", "stale-path premise alpha beta")
    a2 = db.insert_atom("s.md", "stale-path support gamma delta")
    b1 = db.insert_atom("i.md", "base premise epsilon zeta")
    b2 = db.insert_atom("i.md", "base support eta theta")
    c1 = db.insert_atom("i.md", "synthesis extra iota kappa")
    h_stale = store.create_holon([a1, a2], kind="thematic_cluster")
    base = store.create_holon([b1, b2], kind="thematic_cluster")
    h_inv = store.create_holon([b1, c1], kind="query_synthesis",
                               derived_from=[base])
    report = engine.invalidate_dependents([("atom", str(a1))])
    assert report["invalidated"] == [h_stale]
    out = store.remove_members(base, [b2])
    assert out["invalidated"] == [h_inv]
    assert store.get_holon(h_stale).status == "stale"
    assert store.get_holon(h_inv).status == "invalidated"
    assert store.get_holon(base).status == "active"
    return db, store, h_stale, h_inv, base


def test_holon_statuses_covers_all_four_tokens():
    """HOLON_STATUSES names every live holon_state.status token (RED unpatched)."""
    from trace_lite.filing.holon import HOLON_STATUSES

    assert HOLON_STATUSES == frozenset({"active", "stale", "invalidated", "deleted"})


def test_needs_attention_membership():
    """NEEDS_ATTENTION is exactly {stale, invalidated} (RED unpatched)."""
    from trace_lite.filing.holon import NEEDS_ATTENTION

    assert NEEDS_ATTENTION == frozenset({"stale", "invalidated"})
    assert "active" not in NEEDS_ATTENTION
    assert "deleted" not in NEEDS_ATTENTION


def test_status_count_includes_both_tokens(tmp_path):
    """PIN: /api/status count unions stale + invalidated (= 2)."""
    db, _store, _h_stale, _h_inv, _base = _seed_mixed_statuses(tmp_path)
    try:
        assert api.freshness(db.conn)["stale_holons"] == 2
    finally:
        db.close()


def test_active_only_readers_hide_both_tokens(tmp_path):
    """PIN: active-only readers hide stale AND invalidated alike."""
    db, store, h_stale, h_inv, base = _seed_mixed_statuses(tmp_path)
    try:
        assert store.holons_of_kind("thematic_cluster") != []
        assert {h.holon_id for h in store.holons_of_kind("thematic_cluster")} == {base}
        assert store.holons_of_kind("query_synthesis") == []
        for aid_row in db.conn.execute(
                "SELECT atom_id FROM holon_members WHERE holon_id IN (?, ?)",
                (h_stale, h_inv)).fetchall():
            served = {h.holon_id for h in store.holons_of_atom(int(aid_row[0]))}
            assert h_stale not in served and h_inv not in served
        # Direct reads still surface the true per-row status.
        assert store.get_holon(h_stale).status == "stale"
        assert store.get_holon(h_inv).status == "invalidated"
    finally:
        db.close()


def test_not_deleted_filters_treat_both_as_live(tmp_path):
    """PIN: ``!= 'deleted'`` predicates treat both tokens as live/grouped."""
    db, _store, h_stale, h_inv, base = _seed_mixed_statuses(tmp_path)
    try:
        live = {r[0] for r in db.conn.execute(
            "SELECT holon_id FROM holon_state WHERE status != 'deleted'").fetchall()}
        assert {h_stale, h_inv, base} <= live
        grouped = {r[0] for r in db.conn.execute(
            "SELECT DISTINCT m.holon_id FROM holon_members m"
            " JOIN holon_state s ON s.holon_id = m.holon_id"
            " WHERE s.status != 'deleted'").fetchall()}
        assert {h_stale, h_inv, base} <= grouped
    finally:
        db.close()
