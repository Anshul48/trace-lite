"""P04 acceptance: tier latencies, P95 bounds, calibrated abstention, RRF."""

import statistics

import pytest

from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router import (
    CascadeRouter,
    is_syntax_dense,
    rrf_fuse,
)
from trace_lite.store import Database

TOPICS = [
    "wal checkpoint tuning for sqlite filers",
    "faceted classification with hearst patterns",
    "obsidian vault synchronization strategies",
    "context compilation under token budgets",
    "reciprocal rank fusion for hybrid search",
    "truth maintenance across contract boundaries",
]


@pytest.fixture()
def routed(tmp_path):
    db = Database(tmp_path / "router.db")
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    topics = taxonomy.create_facet("Topics", "Systems")
    facet_ids = [topics]
    for i, title in enumerate(TOPICS):
        facet_ids.append(taxonomy.create_facet("Topics", f"Area{i}", parent_id=topics))
    docs = []
    for i in range(240):
        if i % 3 == 0:
            text = f"def get_node_version_{i}(config): return config.load('/etc/trace/{i}.yaml')"
        elif i % 3 == 1:
            text = f"note {i}: {TOPICS[i % len(TOPICS)]} with measurements and analysis"
        else:
            text = f"log entry {i}: checkpoint governor folded wal frames after bulk ingest"
        docs.append((f"doc-{i}", text))
    ids = db.bulk_ingest(docs)
    for aid in ids:
        engine.assign_facets(aid, [facet_ids[aid % len(facet_ids)]])
    for fid in facet_ids:
        engine.refresh_centroid(fid)
    router = CascadeRouter(db.conn, engine)
    warm = router.warm()
    assert warm["vectors"] == 240 and warm["centroids"] >= len(facet_ids)
    yield db, router
    db.close()


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=100)[94]


def test_lexical_shortcircuit_p95_under_5ms(routed):
    """C01: 500 syntax-dense queries, P95 <= 5ms."""
    _, router = routed
    queries = [
        f"get_node_version_{i % 80}(config) -> '/etc/trace/{i % 80}.yaml'" for i in range(500)
    ]
    assert all(is_syntax_dense(q) for q in queries)
    lat = []
    for q in queries:
        r = router.route(q)
        lat.append(r.elapsed_ms)
        assert r.tier_used == 1 and r.verdict == "answerable"
    assert _p95(lat) <= 5.0, f"lexical P95 { _p95(lat):.2f}ms exceeds 5ms"


def test_cascade_p95_under_50ms(routed):
    """C02: 1,000 mixed queries end-to-end, P95 <= 50ms, zero cloud spend (local only)."""
    _, router = routed
    queries = []
    for i in range(1000):
        if i % 4 == 0:
            queries.append(f"config.load('/etc/trace/{i % 90}.yaml')")
        elif i % 4 == 1:
            queries.append(f"how does {TOPICS[i % len(TOPICS)]} behave under load")
        elif i % 4 == 2:
            queries.append("wal checkpoint governor bulk ingest folding")
        else:
            queries.append(f"xqzt{i} blorpt{i} wqkj{i}")
    lat, tiers = [], set()
    for q in queries:
        r = router.route(q)
        lat.append(r.elapsed_ms)
        tiers.add(r.tier_used)
    assert _p95(lat) <= 50.0, f"cascade P95 {_p95(lat):.2f}ms exceeds 50ms"
    assert tiers >= {1, 3}  # both dispatch paths exercised


def test_calibrated_abstention(routed):
    """C03 / failure case: gibberish returns insufficient_evidence with zero hits."""
    _, router = routed
    r = router.route("xqzt blorpt wqkj zzzqq")
    assert r.verdict == "insufficient_evidence" and r.anchors == []
    assert r.to_dict()["sufficiency_state"] == "insufficient_evidence"
    ok = router.route("wal checkpoint governor")
    assert ok.verdict == "answerable" and len(ok.anchors) > 0
    empty = router.route("   ")
    assert empty.verdict == "insufficient_evidence"


def test_beam_apportions_budget_across_facets(tmp_path):
    """F3: a 450-member facet must not starve a 50-member facet out of the beam."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "beam.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        big = taxonomy.create_facet("Topics", "Big")
        small = taxonomy.create_facet("Topics", "Small")
        ids = db.bulk_ingest(
            [(f"d-{i}", f"generic filler document number {i} woolgather") for i in range(500)]
        )
        for aid in ids[:450]:
            engine.assign_facets(aid, [big])
        for aid in ids[450:]:
            engine.assign_facets(aid, [small])
        for fid in (big, small):
            engine.refresh_centroid(fid)
        router = CascadeRouter(db.conn, engine)
        router.warm()
        # Sourcing: the pool must represent every beam facet, not just the biggest.
        pool = set(router.beam.candidate_ids("woolgather filler"))
        small_ids = set(ids[450:])
        assert pool & small_ids, "Small facet starved from beam pool"
        assert pool & set(ids[:450]), "Big facet missing from beam pool"
        assert len(pool) <= 400
        # End to end: a Small-discriminating query surfaces Small atoms first.
        for aid in ids[450:]:
            row = db.get_atom(aid)
            db.conn.execute("UPDATE atom SET text = ? WHERE id = ?",
                            (row["text"] + " quokka marsupial", aid))
        db.rebuild_fts()
        router.warm()  # refresh + re-share the dense matrix with the beam
        hits = router.beam.search("quokka marsupial", limit=10)
        assert hits and hits[0]["id"] in small_ids
    finally:
        db.close()


def test_beam_ghost_facet_cannot_starve_pool(tmp_path):
    """Critic loop: zero-member facet with stale centroid must not collapse the beam."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "ghost.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        big = taxonomy.create_facet("Topics", "Big")
        small = taxonomy.create_facet("Topics", "Small")
        ghost = taxonomy.create_facet("Topics", "Ghost")
        ids = db.bulk_ingest(
            [(f"d-{i}", f"generic filler document number {i} woolgather") for i in range(450)]
        )
        ghost_atom = db.insert_atom("ghost-doc", "ghostly woolgather filler remains")
        for aid in ids[:400]:
            engine.assign_facets(aid, [big])
        for aid in ids[400:]:
            engine.assign_facets(aid, [small])
        engine.assign_facets(ghost_atom, [ghost])
        for fid in (big, small, ghost):
            engine.refresh_centroid(fid)
        assert db.delete_doc("ghost-doc") == 1  # Ghost now: stale blob, zero members.
        router = CascadeRouter(db.conn, engine)
        router.warm()
        pool = set(router.beam.candidate_ids("woolgather filler", beam_width=3))
        assert pool & set(ids[400:]), "Small facet starved by ghost"
        assert pool & set(ids[:400]), "Big facet missing from pool"
        assert len(pool) <= 400
    finally:
        db.close()


def test_route_mode_dispatch(tmp_path):
    """Critic loop: tree→{1,2}, flat→{1,3}, unknown→hybrid behavior."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "modes.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        topics = taxonomy.create_facet("Topics", "T")
        ids = db.bulk_ingest(
            [(f"m-{i}", f"wal checkpoint governor tuning note {i}") for i in range(30)]
        )
        for aid in ids:
            engine.assign_facets(aid, [topics])
        engine.refresh_centroid(topics)
        router = CascadeRouter(db.conn, engine)
        router.warm()
        queries = ["wal checkpoint governor tuning", "config.load('/x/y.yaml')",
                   "xqzt blorpt wqkj", "governor bulk ingest folding"]
        for q in queries:
            assert router.route(q, mode="tree").tier_used in (1, 2)
            assert router.route(q, mode="flat").tier_used in (1, 3)
            assert router.route(q, mode="bogus").tier_used == router.route(q).tier_used
    finally:
        db.close()


def test_corroboration_gate_rejects_dense_only_noise(tmp_path):
    """Abstention calibration: strong dense collision without indexed-term support abstains."""
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.router import has_lexical_support

    db = Database(tmp_path / "corroboration.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "T")
        ids = db.bulk_ingest([(f"c-{i}", f"wal checkpoint governor tuning note {i}") for i in range(60)])
        for aid in ids:
            engine.assign_facets(aid, [fid])
        engine.refresh_centroid(fid)
        router = CascadeRouter(db.conn, engine)
        router.warm()
        assert has_lexical_support(db.conn, "wal checkpoint governor")
        assert not has_lexical_support(db.conn, "xqzt blorpt wqkj")
        assert not has_lexical_support(db.conn, "")
        # Off-topic natural language: no indexed term may pass as evidence.
        miss = router.route("sourdough starter hydration ratios")
        assert miss.verdict == "insufficient_evidence" and miss.anchors == []
        ok = router.route("wal checkpoint governor tuning")
        assert ok.verdict == "answerable" and ok.anchors
    finally:
        db.close()


def test_rrf_formula():
    fused = rrf_fuse([7, 8], [8, 7])
    scores = dict(fused)
    assert abs(scores[7] - (1 / 61 + 1 / 60)) < 1e-12
    assert abs(scores[8] - (1 / 60 + 1 / 61)) < 1e-12
    assert scores[7] == scores[8]  # symmetric ranks fuse equally
    assert [aid for aid, _ in rrf_fuse([1, 2, 3], [3])][0] == 3  # dual-list boost wins


def test_porter_stem_canonical():
    from trace_lite.router.lexical import porter_stem
    # Canonical Porter pairs (validated against SQLite's own porter tokenizer:
    # "inhibitor" keeps its form in both — retrieval and scoring stay consistent).
    pairs = {
        "caresses": "caress", "ponies": "poni", "ties": "ti", "cats": "cat",
        "agreed": "agre", "mating": "mate", "mottled": "mottl", "bled": "bled",
        "relational": "relat", "conditional": "condit", "digitizer": "digit",
        "electriciti": "electr", "hopeful": "hope", "goodness": "good",
        "cells": "cell", "inhibition": "inhibit", "inhibitor": "inhibitor",
        "activation": "activ", "activates": "activ", "undergoing": "undergo",
        "arterioles": "arteriol", "expression": "express",
    }
    for word, stem in pairs.items():
        assert porter_stem(word) == stem, word


def test_stem_aware_coverage_sees_index_matches():
    from trace_lite.router.lexical import _score_rows, extract_terms
    # Doc shares a stem with the query but not the exact form: substring
    # coverage misses ("activation" not in doc), stem-aware coverage hits it
    # via "activ" — the same match the porter-tokenized index itself makes.
    rows = [{"id": 1, "text": "cell activates pathways under load", "r": -1.0}]
    terms = extract_terms("cell activation")
    plain = _score_rows([dict(r) for r in rows], terms, 10)
    stemmed = _score_rows([dict(r) for r in rows], terms, 10, stem=True)
    assert plain[0]["score"] == 0.5
    assert stemmed[0]["score"] == 1.0


def test_ghost_facets_cannot_saturate_beam_window(tmp_path):
    """3 ghost facets with high centroid similarity must not fill the beam and starve rank 4/5."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "ghosts3.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        g1 = taxonomy.create_facet("Topics", "Ghost1")
        g2 = taxonomy.create_facet("Topics", "Ghost2")
        g3 = taxonomy.create_facet("Topics", "Ghost3")
        v1 = taxonomy.create_facet("Topics", "Valid1")
        v2 = taxonomy.create_facet("Topics", "Valid2")

        g1_aid = db.insert_atom("g1-doc", "antique clock restoration precision gear mechanism")
        g2_aid = db.insert_atom("g2-doc", "antique clock restoration precision spring mechanism")
        g3_aid = db.insert_atom("g3-doc", "antique clock restoration precision pendulum mechanism")
        v1_aids = [
            db.insert_atom(f"v1-{i}", f"antique clock restoration workshop guide chapter {i}")
            for i in range(5)
        ]
        v2_aids = [
            db.insert_atom(f"v2-{i}", f"antique clock restoration reference handbook section {i}")
            for i in range(5)
        ]

        engine.assign_facets(g1_aid, [g1])
        engine.assign_facets(g2_aid, [g2])
        engine.assign_facets(g3_aid, [g3])
        for aid in v1_aids:
            engine.assign_facets(aid, [v1])
        for aid in v2_aids:
            engine.assign_facets(aid, [v2])

        for fid in (g1, g2, g3, v1, v2):
            engine.refresh_centroid(fid)

        # Snapshot ghost centroids to simulate lingering stale centroids in RAM
        ghost_centroids = {fid: engine._centroids[fid] for fid in (g1, g2, g3)}

        # Delete all 3 ghost documents
        db.delete_doc("g1-doc")
        db.delete_doc("g2-doc")
        db.delete_doc("g3-doc")

        router = CascadeRouter(db.conn, engine)
        router.warm()

        # Inject stale centroids to force them to rank top-3
        engine._centroids.update(ghost_centroids)

        # Query matches ghost centroids top-3: beam must skip ghosts and select Valid1/Valid2
        res = router.route("antique clock restoration precision", mode="tree")
        assert res.verdict == "answerable"
        assert len(res.anchors) > 0
        retrieved_ids = {a["id"] for a in res.anchors}
        assert (retrieved_ids & set(v1_aids)) or (retrieved_ids & set(v2_aids))

        # Ghosts must have been evicted from RAM centroids upon detection
        for fid in (g1, g2, g3):
            assert fid not in engine._centroids
    finally:
        db.close()


def test_corroboration_gate_rejects_database_wide_leakage(tmp_path):
    """Off-topic anchor must not be corroborated by an unrelated note in the database."""
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.router import has_lexical_support

    db = Database(tmp_path / "leak.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "T")

        # Ingest an unrelated document containing "record"
        db.insert_atom("unrelated-doc", "medical record database configuration entries")

        # Ingest an Obsidian frontmatter note that has no query terms
        frontmatter_text = "tags: project, meeting notes date: 2026-09-01 agenda: weekly sync"
        fm_aid = db.insert_atom("frontmatter-doc", frontmatter_text)
        engine.assign_facets(fm_aid, [fid])
        engine.refresh_centroid(fid)

        router = CascadeRouter(db.conn, engine)
        router.warm()

        query = "record of antique clock restoration"
        # Database-wide FTS matches "record" because of the unrelated medical document
        assert has_lexical_support(db.conn, query) is True

        # Anchor-specific corroboration checks the candidate frontmatter text:
        assert has_lexical_support(db.conn, query, anchor_text=frontmatter_text) is False
        assert has_lexical_support(query, frontmatter_text) is False

        # End-to-end: frontmatter-doc cannot pass corroboration gate for this query
        res = router.route(query)
        for anchor in res.anchors:
            assert anchor["doc_id"] != "frontmatter-doc"
    finally:
        db.close()


def test_large_facet_balanced_sampling_includes_tail(tmp_path):
    """Facets with > 400 members must sample both head (early) and tail (recent) members."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "large_facet.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "BigTopic")
        docs = [(f"doc-{i}", f"document number {i} in large facet topic") for i in range(800)]
        ids = db.bulk_ingest(docs)
        engine.assign_facets_bulk([(aid, fid) for aid in ids])
        engine.refresh_centroid(fid)

        router = CascadeRouter(db.conn, engine)
        router.warm()

        pool = set(router.beam.candidate_ids("large facet topic"))
        assert len(pool) <= 400
        head_ids = set(ids[:200])
        tail_ids = set(ids[600:])
        assert pool & head_ids, "Head documents missing from balanced pool"
        assert pool & tail_ids, "Tail/recent documents missing from balanced pool"
    finally:
        db.close()


def test_has_lexical_support_signatures(tmp_path):
    """has_lexical_support must support keyword arguments and Porter stemming."""
    from trace_lite.router import has_lexical_support

    # Kwargs with query and anchor_text
    assert has_lexical_support(query="restoration", anchor_text="antique clock restore") is True
    assert has_lexical_support(query="connecting fast", anchor_text="the network was connected") is True
    assert has_lexical_support(query="quantum physics", anchor_text="classical mechanics biology") is False

    # Positional query and anchor_text
    assert has_lexical_support("precision gear", "gear assembly manual") is True

    # Empty / None edge cases
    assert has_lexical_support(query="", anchor_text="content") is False
    assert has_lexical_support(query="content", anchor_text="") is False
    assert has_lexical_support(query="content", anchor_text=None) is False

    # With conn kwargs
    db = Database(tmp_path / "lex_sig.db")
    try:
        db.insert_atom("doc1", "specialized terminology corpus")
        assert has_lexical_support(conn=db.conn, query="terminology") is True
        assert has_lexical_support(conn=db.conn, query="unrelated") is False
        assert has_lexical_support(conn=db.conn, query="terminology", anchor_text="other text") is False
        assert has_lexical_support(conn=db.conn, query="terminology", anchor_text="terminology text") is True
    finally:
        db.close()


def test_ghost_facet_unknown_facet_error_eviction(tmp_path):
    """If a facet was deleted from taxonomy, beam.candidate_ids catches UnknownFacetError and evicts it."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "pruned_facet.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        f_pruned = taxonomy.create_facet("Topics", "Pruned")
        f_valid = taxonomy.create_facet("Topics", "Valid")

        aid_pruned = db.insert_atom("doc-p", "pruned topic antique clock restoration")
        aid_valid = db.insert_atom("doc-v", "valid topic antique clock restoration")
        engine.assign_facets(aid_pruned, [f_pruned])
        engine.assign_facets(aid_valid, [f_valid])
        engine.refresh_centroid(f_pruned)
        engine.refresh_centroid(f_valid)

        # Forcefully delete the pruned facet row from the database (simulating taxonomy prune)
        db.conn.execute("DELETE FROM facets WHERE facet_id = ?", (f_pruned,))
        db.conn.commit()

        # Centroid still lingers in engine._centroids in RAM
        assert f_pruned in engine._centroids

        router = CascadeRouter(db.conn, engine)
        router.warm()

        # Query matches both centroids: beam must gracefully evict f_pruned and return doc-v
        candidates = router.beam.candidate_ids("antique clock restoration")
        assert aid_valid in candidates
        assert aid_pruned not in candidates
        assert f_pruned not in engine._centroids
    finally:
        db.close()


def test_query_facets_strategy_validation(tmp_path):
    """Invalid query_facets strategy raises ValueError."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "strat_val.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "T")
        aid = db.insert_atom("doc1", "text")
        engine.assign_facets(aid, [fid])

        with pytest.raises(ValueError, match="unknown strategy"):
            engine.query_facets([fid], limit=10, strategy="random_guess")
    finally:
        db.close()


def test_balanced_sampling_facet_between_half_and_full_cap(tmp_path):
    """A facet with 300 members under candidate_cap=400 returns all 300 members without duplicates."""
    from trace_lite.filing import FilingEngine, Taxonomy

    db = Database(tmp_path / "mid_facet.db")
    try:
        taxonomy = Taxonomy(db.conn)
        engine = FilingEngine(db.conn, taxonomy)
        fid = taxonomy.create_facet("Topics", "MidTopic")
        docs = [(f"doc-{i}", f"document number {i} in mid facet topic") for i in range(300)]
        ids = db.bulk_ingest(docs)
        engine.assign_facets_bulk([(aid, fid) for aid in ids])

        # Query with balanced strategy and cap 400
        result = engine.query_facets([fid], limit=400, strategy="balanced")
        assert len(result) == 300
        assert set(result) == set(ids)
    finally:
        db.close()
