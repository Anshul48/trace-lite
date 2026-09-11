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


def test_rrf_formula():
    fused = rrf_fuse([7, 8], [8, 7])
    scores = dict(fused)
    assert abs(scores[7] - (1 / 61 + 1 / 60)) < 1e-12
    assert abs(scores[8] - (1 / 60 + 1 / 61)) < 1e-12
    assert scores[7] == scores[8]  # symmetric ranks fuse equally
    assert [aid for aid, _ in rrf_fuse([1, 2, 3], [3])][0] == 3  # dual-list boost wins
