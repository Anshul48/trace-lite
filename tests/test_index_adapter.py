"""R03 C3-3 + publication: numpy-IVF recall vs exact + crash-safe publish (durable)."""

import json

import numpy as np
import pytest

from trace_lite.router import HashBowEncoder, VectorIndex
from trace_lite.router.projection import build_and_publish
from trace_lite.store import Database, generations

TOPICS = [
    "wal checkpoint tuning for sqlite filers",
    "faceted classification with hearst patterns",
    "obsidian vault synchronization strategies",
    "context compilation under token budgets",
    "reciprocal rank fusion for hybrid search",
    "truth maintenance across contract boundaries",
]


POOL = ("amber beacon cinder drift ember flint grove harbor inlet jetty "
        "kraal lagoon meadow narrows opal prairie quince ridge sedge tundra "
        "umbra vale wold xenon yarrow zephyr").split()


def _corpus(n_per=100):
    # Docs within a topic share the topic core but differ in rotating filler
    # words: near-duplicate vectors would make exact top-10 an arbitrary tie
    # set and recall-vs-exact meaningless. Digits carry no vector weight
    # (serial/year guard), so variation must come from word choice.
    docs, queries = [], []
    for t, topic in enumerate(TOPICS):
        for i in range(n_per):
            w = " ".join(POOL[(i * 7 + t * 13 + j * 11) % len(POOL)] for j in range(4))
            docs.append(f"note t{t} i{i}: {topic} with {w} measurements and analysis")
        queries.append(f"how does {topic} behave under load")
        queries.append(f"{topic} measurements analysis")
    return docs, queries


def _trained_index(n_per=100, nprobe=8):
    docs, queries = _corpus(n_per)
    enc = HashBowEncoder()
    enc.fit_idf(docs)
    mat = np.asarray([enc.encode_weighted(d) for d in docs], dtype=np.float32)
    idx = VectorIndex(enc, nprobe=nprobe)
    idx.build(list(range(1, len(docs) + 1)), mat,
              generation_id="gen-test", watermark="0001")
    qvecs = [enc.encode_weighted(q) for q in queries]
    return idx, enc, qvecs


def test_ivf_recall_at_10_meets_095():
    """C3-3: same space, same filters/budgets — ANN vs exact, reported separately."""
    idx, _, qvecs = _trained_index()
    report = idx.recall_vs_exact(qvecs, k=10)
    assert report["queries"] == len(qvecs)
    assert report["recall_at_k"] >= 0.95, report


def test_recall_with_tombstone_and_revision_filter():
    idx, _, qvecs = _trained_index(n_per=60)
    doomed = idx.ids[0]
    assert idx.delete(doomed) is True
    assert doomed not in idx.live_ids()
    for qv in qvecs[:4]:
        for hits, _ in (idx.search_exact(qv, 10), idx.search(qv, 10)):
            assert doomed not in {aid for aid, _ in hits}
    # Realistic current-revision filter: scattered superseded docs removed
    # (not a whole-topic amputation — that adversarial shape needs a higher
    # nprobe, disclosed in delivery.md).
    allowed = set(idx.ids) - {aid for i, aid in enumerate(idx.ids) if i % 7 == 0}
    allowed.discard(idx.ids[0])
    hits, cov = idx.search(qvecs[0], 10, allowed=allowed)
    assert all(aid in allowed for aid, _ in hits)
    assert isinstance(cov["adequate"], bool)
    report = idx.recall_vs_exact(qvecs, k=10, allowed=allowed)
    assert report["recall_at_k"] >= 0.95, report
    # Adversarial filter (allowed set far from the query): recall may dip and
    # the coverage flag — never a silent "no evidence" — reports the outcome.
    narrow = set(idx.ids[1:200])
    _, cov2 = idx.search(qvecs[8], 10, allowed=narrow)
    assert isinstance(cov2["adequate"], bool)
    assert all(a in narrow for a, _ in idx.search(qvecs[8], 10, allowed=narrow)[0])


def test_publish_then_restart_recovery(tmp_path):
    """Save/flush-before-publish; reopened index serves identical results."""
    idx, enc, qvecs = _trained_index(n_per=60)
    idx.delete(idx.ids[5])
    receipt = idx.publish(tmp_path / "idx", generation_id="gen-r1", watermark="0007")
    assert (tmp_path / "idx" / "CURRENT").read_text().strip() == receipt["manifest"]
    reopened, info = VectorIndex.open(tmp_path / "idx",
                                      expected_config_hash=enc.config_hash())
    assert info["manifest_hash"] == receipt["manifest_hash"]
    assert info["count"] == idx.count
    assert reopened.tombstones == idx.tombstones
    for qv in qvecs[:6]:
        assert [a for a, _ in reopened.search(qv, 10)[0]] == \
            [a for a, _ in idx.search(qv, 10)[0]]
        assert [a for a, _ in reopened.search_exact(qv, 10)[0]] == \
            [a for a, _ in idx.search_exact(qv, 10)[0]]


def test_publish_records_active_generation_row(tmp_path):
    db = Database(tmp_path / "gen.db")
    try:
        db.bulk_ingest([(f"d-{i}", f"wal checkpoint governor note {i}") for i in range(50)])
        receipt = build_and_publish(db.conn, tmp_path / "idx", "gen-g1", watermark="0003")
        assert receipt["published"] is True
        assert receipt["encoder"]["degraded"] is True
        active = generations.get_active(db.conn, "vector_index")
        assert active is not None
        assert active["manifest_hash"] == receipt["manifest_hash"]
        assert active["config_hash"] == receipt["encoder"]["config_hash"]
        assert active["watermark"] == "0003"
    finally:
        db.close()


def test_stale_encoder_generation_rejected(tmp_path):
    idx, _, _ = _trained_index(n_per=40)
    idx.publish(tmp_path / "idx", generation_id="gen-old", watermark="0001")
    with pytest.raises(ValueError, match="config hash"):
        VectorIndex.open(tmp_path / "idx", expected_config_hash="0" * 32)


def test_segment_tamper_detected_on_open(tmp_path):
    idx, _, _ = _trained_index(n_per=40)
    idx.publish(tmp_path / "idx", generation_id="gen-t1", watermark="0001")
    seg = next((tmp_path / "idx" / "segments").glob("*.vec.npy"))
    with open(seg, "r+b") as fh:
        fh.seek(128)
        fh.write(b"\x00\x01\x02\x03")
    with pytest.raises(ValueError, match="segment hash"):
        VectorIndex.open(tmp_path / "idx")


def test_orphans_reported_and_collectable(tmp_path):
    idx, _, _ = _trained_index(n_per=40)
    idx.publish(tmp_path / "idx", generation_id="gen-o1", watermark="0001")
    stray = tmp_path / "idx" / "segments" / "stray.npy"
    stray.write_bytes(b"orphan")
    _, info = VectorIndex.open(tmp_path / "idx")
    assert "segments/stray.npy" in info["orphans"]
    removed = VectorIndex.collect_orphans(tmp_path / "idx")
    assert "segments/stray.npy" in removed
    assert not stray.exists()
    # Live generation still opens after collection.
    _, info2 = VectorIndex.open(tmp_path / "idx")
    assert info2["generation_id"] == "gen-o1"


def test_crash_before_publish_leaves_previous_generation(tmp_path):
    """Segments flushed but CURRENT never moved: previous manifest still active."""
    idx, _, qvecs = _trained_index(n_per=40)
    idx.publish(tmp_path / "idx", generation_id="gen-c1", watermark="0001")
    before, _ = VectorIndex.open(tmp_path / "idx")
    # Simulate a crashed second publish: new segments saved, manifest written,
    # but CURRENT pointer never replaced.
    idx2, _, _ = _trained_index(n_per=40)
    idx2.generation_id = "gen-c2"
    files = idx2.save(tmp_path / "idx", generation_id="gen-c2", watermark="0002")
    idx2.manifest(files)  # built but never written/published
    recovered, info = VectorIndex.open(tmp_path / "idx")
    assert info["generation_id"] == "gen-c1"
    for qv in qvecs[:4]:
        assert [a for a, _ in recovered.search(qv, 5)[0]] == \
            [a for a, _ in before.search(qv, 5)[0]]
    # The unreferenced gen-c2 segments are orphans, collectable and harmless.
    assert any("gen-c2" in o for o in info["orphans"])
    VectorIndex.collect_orphans(tmp_path / "idx")
    _, info2 = VectorIndex.open(tmp_path / "idx")
    assert info2["generation_id"] == "gen-c1"


def test_encoder_config_hash_stable_and_pinned():
    enc = HashBowEncoder()
    assert enc.config_hash() == HashBowEncoder().config_hash()
    d = enc.spec.to_dict()
    assert d["dim"] == 128 and d["degraded"] is True
    assert d["tokenizer"] and d["weights"] and d["normalization"] and d["pooling"]
    with pytest.raises(ValueError, match="IDF dim"):
        enc.set_idf([1.0] * 16)


def test_manifest_json_shape(tmp_path):
    idx, _, _ = _trained_index(n_per=40)
    receipt = idx.publish(tmp_path / "idx", generation_id="gen-m1", watermark="0009")
    body = json.loads((tmp_path / "idx" / receipt["manifest"]).read_text())
    assert body["kind"] == "vector_index" and body["watermark"] == "0009"
    assert body["encoder"]["degraded"] is True
    assert len(body["manifest_hash"]) == 64
