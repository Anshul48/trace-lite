"""P03 acceptance: multi-membership, subtree traversal, A-B-A holons, DAG safety."""

import statistics
import time

import pytest

from trace_lite.filing import (
    CircularFacetError,
    FilingEngine,
    HolonError,
    HolonStore,
    Taxonomy,
    UnknownFacetError,
)
from trace_lite.store import Database


@pytest.fixture()
def setup(tmp_path):
    db = Database(tmp_path / "filing.db")
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    holons = HolonStore(db.conn)
    yield db, taxonomy, engine, holons
    db.close()


def _seed_doc(db, engine, taxonomy, doc_id="note-1"):
    texts = [
        "FlashAttention Triton kernels accelerate GPU attention",
        "Triton language compiles tile programs for GPUs",
        "Benchmarks show 2x speedup on attention layers",
    ]
    ids = [db.insert_atom(doc_id, t, i * 100, i * 100 + len(t)) for i, t in enumerate(texts)]
    topics = taxonomy.create_facet("Topics", "GPU")
    attention = taxonomy.create_facet("Topics", "Attention", parent_id=topics)
    entities = taxonomy.create_facet("Entities", "Triton")
    types = taxonomy.create_facet("Types", "Paper")
    projects = taxonomy.create_facet("Projects", "Trace")
    for aid in ids:
        engine.assign_facets(aid, [attention, entities, types, projects])
        engine.refresh_centroid(attention)
    return ids, {"attention": attention, "topics": topics, "entities": entities,
                 "types": types, "projects": projects}


def test_multi_membership_orthogonal_facets(setup):
    """C01: one document visible through 3+ orthogonal dimensions at once."""
    db, taxonomy, engine, _ = setup
    ids, fx = _seed_doc(db, engine, taxonomy)
    for fid in (fx["attention"], fx["entities"], fx["types"], fx["projects"]):
        assert engine.query_facets([fid]) == ids
    assert engine.query_facets([fx["attention"], fx["entities"]], match_all=True) == ids
    other = taxonomy.create_facet("Topics", "Gardening")
    assert engine.query_facets([fx["attention"], other], match_all=True) == []
    assert engine.query_facets([fx["attention"], other], match_all=False) == ids
    assert engine.query_facets([]) == []
    assert sorted(engine.facets_of_atom(ids[0]))


def test_vector_tokens_drop_pure_numbers():
    """Digit serials must not enter hashed vectors (max-IDF collision lottery)."""
    from trace_lite.filing import vector_tokens

    assert "209" not in vector_tokens("document number 209 woolgather")
    toks = vector_tokens("covid-19 report 2020")
    assert "covid" in toks and "19" not in toks and "2020" not in toks
    assert vector_tokens("") == []


def test_idf_weights_rare_terms(tmp_path):
    """IDF: a shared rare term outranks shared common terms in dense space."""
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.router import CascadeRouter
    from trace_lite.store import Database

    db = Database(tmp_path / "idf.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "T")
        common = "the study of records and reports"
        rare_id = db.insert_atom("rare", f"quokka {common}")
        plain_ids = [
            db.insert_atom(f"p-{i}", f"study records reports notes {common}")
            for i in range(10)
        ]
        for aid in [rare_id, *plain_ids]:
            engine.assign_facets(aid, [fid])
        router = CascadeRouter(db.conn, engine)
        router.warm()
        assert router.hybrid._idf is not None
        hits = router.hybrid.search("quokka study", limit=11)
        assert hits and hits[0]["id"] == rare_id
    finally:
        db.close()


def test_bulk_assign_matches_single_assign(setup):
    """Scale path: bulk load equals per-atom assignment, rejects unknown facets."""
    db, taxonomy, engine, _ = setup
    ids = db.bulk_ingest([(f"b-{i}", f"bulk text {i}") for i in range(50)])
    f1 = taxonomy.create_facet("Topics", "Bulk1")
    f2 = taxonomy.create_facet("Topics", "Bulk2")
    assert engine.assign_facets_bulk([(aid, f1 if i % 2 else f2) for i, aid in enumerate(ids)]) == 50
    assert engine.query_facets([f1]) == sorted(ids[1::2])
    assert engine.query_facets([f1, f2], match_all=False) == ids
    with pytest.raises(UnknownFacetError):
        engine.assign_facets_bulk([(ids[0], "missing-facet")])
    with pytest.raises(ValueError):
        engine.assign_facets_bulk([(ids[0], f1)], confidence=0.0)


def test_subtree_traversal_under_5ms(setup):
    """C02: parent query returns child-subtree atoms; median resolution < 5ms."""
    db, taxonomy, engine, _ = setup
    ids, fx = _seed_doc(db, engine, taxonomy)
    latencies = []
    for _ in range(100):
        start = time.perf_counter()
        assert engine.query_facets([fx["topics"]]) == ids
        latencies.append((time.perf_counter() - start) * 1000.0)
    assert statistics.median(latencies) < 5.0
    assert taxonomy.subtree_ids(fx["topics"]) >= {fx["topics"], fx["attention"]}
    assert [c.name for c in taxonomy.children(fx["topics"])] == ["Attention"]


def test_holon_grouping_and_boundaries(setup):
    """C03: contiguous paragraphs group into a holon; cross-doc/gappy spans rejected."""
    db, taxonomy, engine, holons = setup
    ids, _ = _seed_doc(db, engine, taxonomy)
    hid = holons.create_holon(ids, tier="nav")
    holon = holons.get_holon(hid)
    assert holon.doc_id == "note-1" and holon.atom_ids == tuple(ids)
    assert holon.end_byte > holon.start_byte
    assert [h.holon_id for h in holons.holons_of_doc("note-1")] == [hid]
    with pytest.raises(HolonError):
        holons.create_holon([ids[0]])  # too small
    other = db.insert_atom("note-2", "different document text")
    with pytest.raises(HolonError):
        holons.create_holon([ids[0], other])  # cross-document
    extra = db.insert_atom("note-1", "appended paragraph", 999, 1019)
    with pytest.raises(HolonError):
        holons.create_holon([ids[0], extra])  # non-contiguous slice
    with pytest.raises(KeyError):
        holons.create_holon([ids[0], 999999])


def test_circular_parentage_rejected(setup):
    """Failure case: cycles rejected deterministically; paths resolve deterministically."""
    db, taxonomy, engine, _ = setup
    root = taxonomy.create_facet("Topics", "Systems")
    child = taxonomy.create_facet("Topics", "Database", parent_id=root)
    leaf = taxonomy.create_facet("Topics", "WAL", parent_id=child)
    assert taxonomy.get_facet(leaf).path == "Systems/Database/WAL"
    with pytest.raises(CircularFacetError):
        taxonomy.move_facet(root, leaf)
    with pytest.raises(CircularFacetError):
        taxonomy.move_facet(child, child)
    with pytest.raises(UnknownFacetError):
        taxonomy.get_facet("missing")
    with pytest.raises(ValueError):
        taxonomy.create_facet("Topics", "bad/name")
    moved = taxonomy.move_facet(leaf, root)
    assert moved.path == "Systems/WAL"
    atom_id = db.insert_atom("probe", "probe text")
    with pytest.raises(UnknownFacetError):
        engine.assign_facets(atom_id, ["missing-facet"])


def test_delete_atom_clears_stale_centroid_blob(setup):
    """Deleting all atoms from a facet clears its centroid blob in DB and RAM."""
    db, taxonomy, engine, _ = setup
    engine.bind_database(db)
    f1 = taxonomy.create_facet("Topics", "Transient")
    aid = db.insert_atom("doc-1", "transient notes on quantum computing")
    engine.assign_facets(aid, [f1])
    engine.refresh_centroid(f1)
    assert engine.facet_centroid(f1) is not None
    assert f1 in engine._centroids
    row = db.conn.execute("SELECT centroid_blob FROM facets WHERE facet_id = ?", (f1,)).fetchone()
    assert row[0] is not None

    # Delete atom: cascade removes membership and clears centroid_blob in DB AND RAM
    db.delete_atom(aid)
    row_after = db.conn.execute("SELECT centroid_blob FROM facets WHERE facet_id = ?", (f1,)).fetchone()
    assert row_after[0] is None
    assert f1 not in engine._centroids

    # clear_stale_centroids clears any lingering RAM centroid for empty facets
    engine._centroids[f1] = [1.0] * 128
    engine.clear_stale_centroids()
    assert f1 not in engine._centroids
