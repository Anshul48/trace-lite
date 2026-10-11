"""R05 reconciliation: link vs group vs canonicalize stay distinct (C5-1..C5-4).

Similarity scores inform candidates only — they can never mint identity,
support, contradiction, or causation rows. Canonical mappings stay versioned,
reversible, and splittable.
"""

import pytest

from trace_lite.filing import (
    FORBIDDEN_AUTO_RELATIONS,
    HOLON_LINK_TYPES,
    HolonError,
    HolonStore,
    audit_links,
    canonical_history,
    canonicalize,
    create_link,
    get_canonical,
    get_links_for,
    restore_version,
    score_similarity,
    split_canonical,
    suggest_candidates,
)
from trace_lite.store import Database

COND_A = "Batching improves throughput by 30% under low contention (fewer syscalls per item)."
COND_B = ("Under high contention, the batching benefit disappears;"
          " throughput matches the unbatched path.")
CHEN_STORAGE = "Chen (storage team) reports checkpoint latency p99 of 40ms on NVMe."
CHEN_QUERY = "Chen (query team) reports checkpoint latency is not the bottleneck."


@pytest.fixture()
def setup(tmp_path):
    db = Database(tmp_path / "reconcile.db")
    holons = HolonStore(db.conn)
    yield db, holons
    db.close()


def _seed(db):
    a1 = db.insert_atom("cond-a", COND_A)
    b1 = db.insert_atom("cond-b", COND_B)
    c1 = db.insert_atom("amb-a", CHEN_STORAGE)
    c2 = db.insert_atom("amb-b", CHEN_QUERY)
    return {"a1": a1, "b1": b1, "c1": c1, "c2": c2}


def _counts(db):
    q = lambda t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    return {"holon_members": q("holon_members"), "holon_link": q("holon_link"),
            "holon_canonical": q("holon_canonical")}


def test_ops_are_structurally_distinct(setup):
    """group writes memberships, link writes edges, canonicalize writes maps."""
    db, holons = setup
    ids = _seed(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    after_group = _counts(db)
    assert after_group["holon_members"] == 2
    assert after_group["holon_link"] == 0
    assert after_group["holon_canonical"] == 0
    create_link(db.conn, "holon", hid, "atom", str(ids["c1"]), "topic_overlap",
                evidence={"note": "shared throughput topic"})
    after_link = _counts(db)
    assert after_link["holon_link"] == 1
    assert after_link["holon_members"] == 2  # linking changes no membership
    assert holons.get_holon(hid).member_count == 2
    canonicalize(db.conn, [("atom", ids["c1"]), ("atom", ids["c2"])],
                 label="chen-mentions")
    after_canon = _counts(db)
    assert after_canon["holon_canonical"] == 2
    assert after_canon["holon_members"] == 2
    assert after_canon["holon_link"] == 1


def test_similarity_never_yields_forbidden_relations(setup):
    """Even byte-identical inputs cannot mint identity/support/etc rows."""
    db, holons = setup
    ids = _seed(db)
    dup = db.insert_atom("cond-c", COND_A)  # byte-identical to a1
    assert score_similarity(COND_A, COND_A) == pytest.approx(1.0)
    assert score_similarity(COND_A, db.get_atom(dup)["text"]) == pytest.approx(1.0)
    for forbidden in sorted(FORBIDDEN_AUTO_RELATIONS):
        with pytest.raises(ValueError):
            create_link(db.conn, "atom", str(ids["a1"]), "atom", str(dup),
                        forbidden)
    with pytest.raises(ValueError):
        create_link(db.conn, "atom", str(ids["a1"]), "atom", str(dup),
                    "no_such_relation")
    assert _counts(db)["holon_link"] == 0  # zero leakage into the edge table


def test_suggest_is_read_only_scores_only(setup):
    """Suggestions carry scores, never rel_types, and write nothing."""
    db, holons = setup
    ids = _seed(db)
    before = _counts(db)
    out = suggest_candidates(db.conn, [ids["a1"]], [ids["b1"], ids["c1"]],
                             top_k=2)
    assert [r["atom_id"] for r in out] == sorted(
        [r["atom_id"] for r in out],
        key=lambda a: -next(r["score"] for r in out if r["atom_id"] == a))
    for row in out:
        assert set(row) == {"atom_id", "doc_id", "score"}
        assert "rel_type" not in row
    assert _counts(db) == before
    with pytest.raises(HolonError):
        suggest_candidates(db.conn, [], [ids["b1"]])
    with pytest.raises(KeyError):
        suggest_candidates(db.conn, [999999], [ids["b1"]])


def test_link_happy_path_and_audit(setup):
    """In-vocabulary edges persist with evidence and audit clean."""
    db, holons = setup
    ids = _seed(db)
    h1 = holons.create_holon([ids["a1"], ids["c1"]], kind="thematic_cluster")
    h2 = holons.create_holon([ids["b1"], ids["c2"]], kind="thematic_cluster")
    lid = create_link(db.conn, "holon", h1, "holon", h2, "topic_overlap",
                      evidence={"note": "batching contention topic"})
    assert lid
    assert len(get_links_for(db.conn, "holon", h1)) == 1
    assert len(get_links_for(db.conn, "holon", h2)) == 1  # both directions
    report = audit_links(db.conn)
    assert report == {"links": 1, "orphans": [], "bad_types": [], "ok": True}
    assert set(HOLON_LINK_TYPES) >= {"topic_overlap", "shared_entity",
                                     "temporal_sequence", "references",
                                     "elaborates"}
    with pytest.raises(KeyError):
        create_link(db.conn, "holon", h1, "atom", "999999", "references")
    with pytest.raises(KeyError):
        create_link(db.conn, "holon", "missing", "atom", str(ids["a1"]),
                    "references")


def test_canonicalize_versioned_and_reversible(setup):
    """Entity maps version forward; history retained; restore reverses."""
    db, _holons = setup
    ids = _seed(db)
    cid = canonicalize(db.conn, [("atom", ids["c1"]), ("atom", ids["c2"])],
                       label="chen", derivation="operator alias review")
    v1 = get_canonical(db.conn, cid)
    assert v1["version"] == 1 and len(v1["targets"]) == 2
    extra = db.insert_atom("amb-c", "Chen notes queue depth matters.")
    assert canonicalize(db.conn, [("atom", ids["c1"]), ("atom", ids["c2"]),
                                  ("atom", extra)],
                        canonical_id=cid, label="chen") == cid
    v2 = get_canonical(db.conn, cid)
    assert v2["version"] == 2 and len(v2["targets"]) == 3
    hist = canonical_history(db.conn, cid)
    assert [v["version"] for v in hist] == [1, 2]  # aliases preserved
    restored = restore_version(db.conn, cid, 1)
    assert restored == 3
    assert len(get_canonical(db.conn, cid)["targets"]) == 2
    assert len(canonical_history(db.conn, cid)) == 3


def test_split_canonical_partitions_mistaken_merge(setup):
    """A wrong merge splits into new entities; the old one stays superseded."""
    db, _holons = setup
    ids = _seed(db)
    cid = canonicalize(db.conn, [("atom", ids["c1"]), ("atom", ids["c2"])],
                       label="chen?")
    new_ids = split_canonical(db.conn, cid, [[("atom", ids["c1"])],
                                             [("atom", ids["c2"])]])
    assert len(new_ids) == 2 and new_ids[0] != new_ids[1]
    assert get_canonical(db.conn, cid)["status"] == "superseded"
    assert get_canonical(db.conn, new_ids[0])["targets"] == [
        {"target_kind": "atom", "target_id": str(ids["c1"])}]
    assert "split from" in get_canonical(db.conn, new_ids[0])["derivation"]
    with pytest.raises(ValueError):  # not a partition of current targets
        split_canonical(db.conn, new_ids[0],
                        [[("atom", ids["c1"])], [("atom", 999999)]])
    with pytest.raises(ValueError):  # single group is not a split
        split_canonical(db.conn, new_ids[0], [[("atom", ids["c1"])]])
    with pytest.raises(KeyError):
        split_canonical(db.conn, "missing", [[("atom", ids["c1"])],
                                             [("atom", ids["c2"])]])


def test_canonical_validates_targets(setup):
    """Unknown targets and empty maps are rejected, never half-written."""
    db, _holons = setup
    ids = _seed(db)
    with pytest.raises(KeyError):
        canonicalize(db.conn, [("atom", 999999)])
    with pytest.raises(HolonError):
        canonicalize(db.conn, [])
    with pytest.raises(HolonError):
        canonicalize(db.conn, [("atom", ids["c1"]), ("atom", ids["c1"])])
    with pytest.raises(KeyError):
        canonicalize(db.conn, [("atom", ids["c1"])], canonical_id="missing")
    assert _counts(db)["holon_canonical"] == 0


def test_dependency_rows_for_derived_holons(setup):
    """Members, containment, and synthesis lineage all pin dependency rows."""
    db, holons = setup
    ids = _seed(db)
    base = holons.create_holon([ids["a1"], ids["c1"]], kind="thematic_cluster")
    child = holons.create_holon([ids["b1"], ids["c2"]], kind="thematic_cluster")
    holons.add_containment(base, child)
    synth = holons.create_holon([ids["a1"], ids["b1"]], kind="query_synthesis",
                                derived_from=[base])
    kinds = {(r[0], r[1]) for r in db.conn.execute(
        "SELECT requires_kind, dependency_kind FROM dependency"
        " WHERE derived_kind = 'holon' AND derived_id = ?", (synth,)).fetchall()}
    assert ("atom", "member") in kinds
    assert ("holon", "derived_from") in kinds
    contains = db.conn.execute(
        "SELECT 1 FROM dependency WHERE derived_kind = 'holon' AND derived_id = ?"
        " AND requires_kind = 'holon' AND requires_id = ?"
        " AND dependency_kind = 'contains'", (base, child)).fetchone()
    assert contains is not None
    reverse = db.conn.execute(
        "SELECT derived_id FROM dependency WHERE requires_kind = 'atom'"
        " AND requires_id = ?", (str(ids["a1"]),)).fetchall()
    assert {r[0] for r in reverse} >= {base, synth}  # reverse index serves R06


def test_no_text_fusion_anywhere(setup):
    """Descriptors are token summaries; member texts are never concatenated."""
    db, holons = setup
    ids = _seed(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    holon = holons.get_holon(hid)
    fused = COND_A + COND_B
    assert holon.descriptor != fused
    assert len(holon.descriptor) < min(len(COND_A), len(COND_B))
    assert "," in holon.descriptor  # top-token csv shape
    row = db.conn.execute(
        "SELECT descriptor FROM holon_state WHERE holon_id = ?",
        (hid,)).fetchone()
    assert str(row[0]) == holon.descriptor


def test_version_history_pins_member_sets(setup):
    """Every mutation appends a holon_version row with a fresh digest."""
    db, holons = setup
    ids = _seed(db)
    hid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    holons.add_members(hid, [ids["c1"]])
    holons.set_weight(hid, ids["c1"], 2.0)
    holons.remove_members(hid, [ids["b1"]])
    history = holons.get_version_history(hid)
    assert [v["version"] for v in history] == [1, 2, 3, 4]
    digests = [v["member_digest"] for v in history]
    assert len(set(digests)) == 4
    assert [v["member_count"] for v in history] == [2, 3, 3, 2]
    assert holons.get_holon(hid).version == 4


def test_containment_removal_invalidates_ancestors(setup):
    """Structural retraction invalidates ancestors + dependent synthesis."""
    db, holons = setup
    ids = _seed(db)
    grand = holons.create_holon([ids["a1"], ids["c1"]], kind="thematic_cluster")
    parent = holons.create_holon([ids["b1"], ids["c2"]], kind="thematic_cluster")
    kid = holons.create_holon([ids["a1"], ids["b1"]], kind="thematic_cluster")
    holons.add_containment(grand, parent)
    holons.add_containment(parent, kid)
    synth = holons.create_holon([ids["a1"], ids["b1"]], kind="query_synthesis",
                                derived_from=[parent])
    out = holons.remove_containment(parent, kid)
    assert set(out["invalidated"]) == {grand, synth}
    assert holons.get_holon(parent).status == "active"
    assert holons.get_holon(grand).status == "invalidated"
    with pytest.raises(KeyError):
        holons.remove_containment(parent, kid)  # already gone


def test_invalidated_holons_go_unavailable(setup):
    """Active-only readers hide invalidated holons until revalidated."""
    db, holons = setup
    c1 = db.insert_atom("solo", COND_A)
    c2 = db.insert_atom("solo", "Second solo sentence about batching queues.")
    base = holons.create_holon([c1, c2])  # single-source discourse holon
    assert [h.holon_id for h in holons.holons_of_doc("solo")] == [base]
    synth = holons.create_holon([c1], kind="query_synthesis",
                                derived_from=[base])
    assert holons.get_holon(synth).doc_id == "solo"
    holons.remove_members(base, [c2])
    assert holons.get_holon(synth).status == "invalidated"
    assert [h.holon_id for h in holons.holons_of_doc("solo")] == [base]
    assert holons.holons_of_kind("query_synthesis") == []
    assert holons.revalidate(synth).status == "active"
    assert [h.holon_id for h in holons.holons_of_kind("query_synthesis")] == [synth]
