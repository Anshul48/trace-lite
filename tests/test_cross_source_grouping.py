"""R05 cross-source grouping: C5-1..C5-4 plus containment, accumulators, invalidation.

Behavior-level tests against real SQLite files. Fixtures are bounded; every
database lives in ``tmp_path`` so the repo stays clean.
"""

import sqlite3

import pytest

from trace_lite.filing import HolonError, HolonStore
from trace_lite.filing.engine import text_vector
from trace_lite.store import Database, migrate

COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = ("Under high contention, the batching benefit disappears;"
          " throughput matches the unbatched path.")
THIRD = "Checkpoint latency p99 of 40ms on NVMe storage hardware."


@pytest.fixture()
def setup(tmp_path):
    db = Database(tmp_path / "holon.db")
    holons = HolonStore(db.conn)
    yield db, holons
    db.close()


def _seed_two_docs(db):
    a1 = db.insert_atom("cond-a", COND_A)
    a2 = db.insert_atom("cond-a", "Batching needs careful queue sizing followup.")
    b1 = db.insert_atom("cond-b", COND_B)
    b2 = db.insert_atom("cond-b", THIRD)
    return {"a1": a1, "a2": a2, "b1": b1, "b2": b2}


def test_migration_004_additive_and_rerunnable(tmp_path):
    """Migration-004 adds holon tables only; rows survive; rerun safe."""
    path = tmp_path / "holon004.db"
    db = Database(path)
    db.bulk_ingest([("doc-a", "alpha héllo 🚀"), ("doc-b", "beta text")])
    db.close()
    assert migrate.migrate_legacy(path)["migration_version"] == 1
    assert migrate.migrate_spans_002(path)["migration_version"] == 2
    assert migrate.migrate_graph_003(path)["migration_version"] == 3
    report = migrate.migrate_holon_004(path)
    assert report["migration_version"] == 4
    assert report["tables"] == ["holon_canonical", "holon_containment",
                                "holon_link", "holon_state", "holon_version"]
    assert report["backup"] is not None and report["backup"].endswith(".bak-004")
    again = migrate.migrate_holon_004(path)
    assert again["migration_version"] == 4
    assert again["observed_version"] == 4
    db = Database(path)
    try:
        assert db.conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
        assert db.count_atoms() == 2
        assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        db.close()


def test_migration_004_preserves_version_chain(tmp_path):
    """Static DDL carries no version-4 row: 001/002/003 callers keep their MAX."""
    path = tmp_path / "versions.db"
    db = Database(path)
    db.bulk_ingest([("doc-a", "alpha")])
    db.close()
    migrate.migrate_legacy(path)
    migrate.migrate_spans_002(path)
    migrate.migrate_graph_003(path)
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 3
    finally:
        conn.close()
    migrate.migrate_holon_004(path)
    conn = sqlite3.connect(str(path))
    try:
        assert conn.execute(
            "SELECT MAX(version) FROM schema_version").fetchone()[0] == 4
    finally:
        conn.close()


def test_migration_004_preserves_legacy_holon_ids(tmp_path):
    """Pre-004 holons keep IDs/memberships and gain backfilled state."""
    path = tmp_path / "legacyholon.db"
    db = Database(path)
    ids = [db.insert_atom("note-1", t) for t in ("alpha one", "alpha two")]
    store = HolonStore(db.conn)
    hid = store.create_holon(ids, tier="nav")
    db.close()
    report = migrate.migrate_holon_004(path)
    assert report["legacy_holons_preserved"] == 1
    db = Database(path)
    try:
        store = HolonStore(db.conn)
        holon = store.get_holon(hid)  # same ID, same members
        assert holon.atom_ids == tuple(ids)
        assert holon.kind == "discourse_span" and holon.status == "active"
        assert holon.member_count == 2
        assert store.check_drift(hid)["ok"] is True
    finally:
        db.close()


def test_c51_thematic_cluster_joins_distinct_sources(setup):
    """C5-1: atoms from distinct sources join one thematic_cluster holon."""
    db, holons = setup
    ids = _seed_two_docs(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    holon = holons.get_holon(hid)
    assert holon.kind == "thematic_cluster"
    assert holon.member_count == 2
    assert holon.doc_id == ""  # multi-source sentinel, never a fake doc
    docs = {db.get_atom(a)["doc_id"] for a in holon.atom_ids}
    assert docs == {"cond-a", "cond-b"}
    assert holons.check_holon_integrity(hid)["ok"] is True
    # Text remains separate: source atoms byte-intact, holon holds no prose.
    assert db.get_atom(ids["a1"])["text"] == COND_A
    assert db.get_atom(ids["b1"])["text"] == COND_B
    sql = "\n".join(
        r[0] for r in db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE name IN"
            " ('holons','holon_members','holon_state','holon_version')").fetchall())
    assert "merged" not in sql and "fused" not in sql


def test_overlapping_membership_allowed(setup):
    """One atom may belong to several holons (overlap, not partition)."""
    db, holons = setup
    ids = _seed_two_docs(db)
    h1 = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    h2 = holons.create_holon([ids["a1"], ids["b2"]], kind="thematic_cluster")
    assert h1 != h2
    assert ids["a1"] in holons.get_holon(h1).atom_ids
    assert ids["a1"] in holons.get_holon(h2).atom_ids
    assert {h.holon_id for h in holons.holons_of_atom(ids["a1"])} == {h1, h2}


def test_c52_conflict_roles_coexist_without_consensus(setup):
    """C5-2: contradictory claims coexist with explicit disagreement roles."""
    db, holons = setup
    ids = _seed_two_docs(db)
    hid = holons.create_holon(
        [ids["a1"], ids["a2"], ids["b1"], ids["b2"]],
        kind="claim_cluster",
        roles={ids["a1"]: "claim", ids["a2"]: "qualification",
               ids["b1"]: "counterevidence", ids["b2"]: "premise"},
    )
    holon = holons.get_holon(hid)
    assert holon.member_count == 4
    stored_roles = {
        r[0]: r[1] for r in db.conn.execute(
            "SELECT atom_id, role FROM holon_members WHERE holon_id = ?",
            (hid,)).fetchall()
    }
    assert sorted(stored_roles.values()) == ["claim", "counterevidence",
                                             "premise", "qualification"]
    # Zero false consensus: holon stays active with no merged verdict field.
    assert holon.status == "active"
    assert holon.descriptor != COND_A + COND_B  # token summary, never fused prose
    assert holons.check_holon_integrity(hid)["ok"] is True


def test_claim_roles_enforced(setup):
    """claim_cluster requires explicit valid roles; other kinds take none."""
    db, holons = setup
    ids = _seed_two_docs(db)
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], ids["b1"]], kind="claim_cluster")
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], ids["b1"]], kind="claim_cluster",
                            roles={ids["a1"]: "claim", ids["b1"]: "agree"})
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster",
                            roles={ids["a1"]: "claim"})
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], ids["a2"]],
                            roles={ids["a1"]: "claim"})


def test_c53_discourse_guard(setup):
    """C5-3: discourse_span stays strictly single-source contiguous."""
    db, holons = setup
    ids = _seed_two_docs(db)
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], ids["b1"]])  # multi-source discourse
    extra = db.insert_atom("cond-a", "appended paragraph")
    with pytest.raises(HolonError):
        holons.create_holon([ids["a1"], extra])  # gappy slice
    hid = holons.create_holon([ids["a1"], ids["a2"]])  # legacy path intact
    assert holons.get_holon(hid).doc_id == "cond-a"


def test_c54_cycle_prevention(setup):
    """C5-4: circular containment raises; DAG stays acyclic."""
    db, holons = setup
    ids = _seed_two_docs(db)
    a = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    b = holons.create_holon([ids["a2"], ids["b2"]], kind="thematic_cluster")
    c = holons.create_holon([ids["a1"], ids["b2"]], kind="thematic_cluster")
    holons.add_containment(a, b)
    holons.add_containment(b, c)
    with pytest.raises(HolonError):
        holons.add_containment(c, a)  # would close the cycle
    with pytest.raises(HolonError):
        holons.add_containment(a, a)  # self-containment
    assert holons.descendants(a) == sorted([b, c])
    assert holons.ancestors(c) == sorted([a, b])
    assert holons.depth_of(a) == 3
    dup = holons.add_containment(a, b)
    assert dup["duplicate"] is True  # idempotent re-add, still acyclic


def test_containment_depth_bounded(setup):
    """Chains past MAX_CONTAINMENT_DEPTH are rejected; depth 8 stands."""
    from trace_lite.filing import MAX_CONTAINMENT_DEPTH

    db, holons = setup
    ids = _seed_two_docs(db)
    chain = [holons.create_holon([ids["a1"], ids["b1"]],
                                 kind="thematic_cluster")
             for _ in range(MAX_CONTAINMENT_DEPTH + 1)]
    for parent, child in zip(chain, chain[1:MAX_CONTAINMENT_DEPTH]):
        holons.add_containment(parent, child)
    assert holons.depth_of(chain[0]) == MAX_CONTAINMENT_DEPTH
    with pytest.raises(HolonError):
        holons.add_containment(chain[MAX_CONTAINMENT_DEPTH - 1],
                               chain[MAX_CONTAINMENT_DEPTH])
    assert chain[MAX_CONTAINMENT_DEPTH] not in holons.descendants(chain[0])


def test_traversal_budget_enforced(setup):
    """Budget-exhausted DAG walks raise instead of running away."""
    db, holons = setup
    ids = _seed_two_docs(db)
    chain = [holons.create_holon([ids["a1"], ids["b1"]],
                                 kind="thematic_cluster") for _ in range(5)]
    for parent, child in zip(chain, chain[1:]):
        holons.add_containment(parent, child)
    with pytest.raises(HolonError):
        holons.descendants(chain[0], budget=2)
    with pytest.raises(HolonError):
        holons.ancestors(chain[-1], budget=2)
    assert holons.descendants(chain[0], budget=100) == sorted(chain[1:])


def test_synthesis_invalidation_on_support_removal(setup):
    """Removing support invalidates dependent synthesis (not just membership)."""
    db, holons = setup
    ids = _seed_two_docs(db)
    base = holons.create_holon([ids["a1"], ids["a2"]], kind="thematic_cluster")
    synth = holons.create_holon([ids["a1"], ids["b1"]], kind="query_synthesis",
                                derivation="synthesis of base",
                                derived_from=[base])
    out = holons.remove_members(base, [ids["a2"]])
    assert out["invalidated"] == [synth]  # dependent synthesis flagged
    assert holons.get_holon(base).status == "active"  # edited holon stands
    assert holons.get_holon(synth).status == "invalidated"
    assert holons.revalidate(synth).status == "active"


def test_delete_holon_invalidates_dependents(setup):
    """Deleting support tombstones the holon (ID kept) and invalidates kin."""
    db, holons = setup
    ids = _seed_two_docs(db)
    base = holons.create_holon([ids["a1"], ids["a2"]], kind="thematic_cluster")
    synth = holons.create_holon([ids["a1"], ids["b1"]], kind="query_synthesis",
                                derived_from=[base])
    out = holons.delete_holon(base)
    assert out["invalidated"] == [synth]
    tomb = holons.get_holon(base)
    assert tomb.status == "deleted" and tomb.atom_ids == ()
    # Unsafe results go unavailable immediately: neither tombstone nor the
    # invalidated synthesis is served by the active-only readers.
    assert holons.holons_of_atom(ids["a1"]) == []
    assert holons.get_holon(synth).status == "invalidated"


def test_empty_holon_clears_descriptors(setup):
    """Empty holons clear persistent + cached descriptors and accumulators."""
    db, holons = setup
    ids = _seed_two_docs(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    assert holons.get_holon(hid).descriptor != ""
    holons.remove_members(hid, [ids["a1"], ids["b1"]])
    cleared = holons.get_holon(hid)
    assert cleared.member_count == 0 and cleared.weight_sum == 0.0
    assert cleared.descriptor == "" and cleared.representatives == ()
    assert cleared.member_digest == ""
    row = db.conn.execute(
        "SELECT vector_sum FROM holon_state WHERE holon_id = ?",
        (hid,)).fetchone()
    assert row[0] is None
    assert holons._descriptor_cache.get(hid) == ""


def test_accumulators_incremental_match_exact(setup):
    """S_h/W_h/count track edits; drift vs exact recompute stays ~zero."""
    db, holons = setup
    ids = _seed_two_docs(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster",
                              weights={ids["a1"]: 2.0, ids["b1"]: 0.5})
    holon = holons.get_holon(hid)
    assert holon.weight_sum == pytest.approx(2.5)
    assert holon.member_count == 2
    assert set(holon.representatives) <= set(holon.atom_ids)
    assert holons.check_drift(hid)["ok"] is True
    holons.add_members(hid, [ids["b2"]])
    assert holons.get_holon(hid).weight_sum == pytest.approx(3.5)
    holons.set_weight(hid, ids["b2"], 3.0)
    assert holons.get_holon(hid).weight_sum == pytest.approx(5.5)
    holons.remove_members(hid, [ids["b1"]])
    final = holons.get_holon(hid)
    assert final.weight_sum == pytest.approx(5.0)
    assert holons.check_drift(hid)["ok"] is True
    with pytest.raises(ValueError):
        holons.set_weight(hid, ids["a1"], 0.0)
    # Manual exact check: W_h equals the live weight column sum.
    live = db.conn.execute(
        "SELECT SUM(weight), COUNT(*) FROM holon_members WHERE holon_id = ?",
        (hid,)).fetchone()
    assert final.weight_sum == pytest.approx(float(live[0]))
    assert final.member_count == int(live[1])
    assert text_vector(COND_A) is not None  # encoder space pinned, not ad hoc
