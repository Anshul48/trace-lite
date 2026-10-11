#!/usr/bin/env python3
"""R03 evidence generator: coverage.json, ann_recall.json, abstention_test.json.

Reads frozen BEIR data read-only; writes ONLY evidence/query-native/R03/.
Index segments go to a /tmp scratch dir (receipt recorded in the JSONs).
Exit nonzero on any criterion miss.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

import numpy as np

from trace_lite.filing import FilingEngine, Taxonomy
from trace_lite.router import (
    CascadeRouter,
    HashBowEncoder,
    VectorIndex,
    has_lexical_support,
)
from trace_lite.router.projection import build_and_publish
from trace_lite.store import Database

OUT = REPO / "evidence" / "query-native" / "R03"
DATA = Path("/mnt/c/Users/anshu/AppData/Local/trace-bench-data")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def gen_coverage() -> dict:
    db = Database(tempfile.mkdtemp(prefix="r03-cov-") + "/cov.db")
    try:
        engine = FilingEngine(db.conn, Taxonomy(db.conn))
        n = 2500
        docs = []
        for i in range(n):
            if i == 2449:
                docs.append((f"doc-{i}", "late arrival note quokka marsupial playbook xenarthra"))
            else:
                docs.append((f"doc-{i}", f"wal checkpoint governor tuning note {i}"))
        ids = db.bulk_ingest(docs)
        late_aid = ids[2449]
        router = CascadeRouter(db.conn, engine)
        warm = router.warm()
        dense = router.hybrid._dense_ranked("quokka marsupial playbook xenarthra", pool=2000)
        scratch = Path(tempfile.mkdtemp(prefix="r03-idx-"))
        t0 = time.perf_counter()
        receipt = build_and_publish(db.conn, scratch / "idx", "gen-ev-cov", watermark="0001")
        build_s = round(time.perf_counter() - t0, 2)
        idx, info = VectorIndex.open(scratch / "idx")
        qv = idx.encoder.encode_weighted("quokka marsupial playbook xenarthra")
        ivf_hits, cov = idx.search(qv, 10)
        report = {
            "criterion": "C3-1 full corpus coverage",
            "corpus_docs": n,
            "faceted_docs": 0,
            "warmed_vectors": warm["vectors"],
            "exhaustive": warm["vectors"] == n,
            "late_rowid": late_aid,
            "late_rowid_gt_2000": late_aid > 2000,
            "late_unfaceted": engine.facets_of_atom(late_aid) == [],
            "late_in_dense_ranking": late_aid in dense,
            "late_in_ivf_top10": late_aid in {a for a, _ in ivf_hits},
            "dense_pool_cap": "DENSE_POOL=2000 (named quality-pool param, not silent sampling)",
            "dense_scan_cap_quality": None,
            "index_manifest_hash": receipt["manifest_hash"],
            "index_build_seconds": build_s,
            "encoder": receipt["encoder"]["label"],
            "pass": warm["vectors"] == n and late_aid in dense
            and late_aid in {a for a, _ in ivf_hits},
        }
        (OUT / "coverage.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return report
    finally:
        db.close()


def gen_ann_recall() -> dict:
    ds = DATA / "beir-scifact" / "raw" / "scifact"
    corpus = load_jsonl(ds / "corpus.jsonl")
    queries = {r["_id"]: r["text"] for r in load_jsonl(ds / "queries.jsonl")}
    qrels: dict[str, set[str]] = {}
    lines = (ds / "qrels" / "test.tsv").read_text(encoding="utf-8").splitlines()
    for ln in lines[1:]:
        qid, doc, rel = ln.split("\t")
        if int(rel) > 0:
            qrels.setdefault(qid, set()).add(doc)
    qids = sorted(qrels)[::5]  # systematic 1-in-5 sample, 60 queries, no tuning
    texts = [f"{r.get('title', '')} {r.get('text', '')}".strip() for r in corpus]
    doc_ids = [r["_id"] for r in corpus]
    enc = HashBowEncoder()
    t0 = time.perf_counter()
    enc.fit_idf(texts)
    mat = np.asarray([enc.encode_weighted(t) for t in texts], dtype=np.float32)
    # n_lists/nprobe chosen for ANN-vs-EXACT fidelity only (no qrels involved
    # in the choice — relevance tuning against test.tsv never happens).
    idx = VectorIndex(enc, nprobe=20)
    idx.build(list(range(1, len(texts) + 1)), mat, generation_id="gen-ev-ann",
              watermark="scifact-full", n_lists=24)
    scratch = Path(tempfile.mkdtemp(prefix="r03-ann-"))
    receipt = idx.publish(scratch / "idx", generation_id="gen-ev-ann",
                          watermark="scifact-full")
    qvecs = [enc.encode_weighted(queries[q]) for q in qids]
    t1 = time.perf_counter()
    recall = idx.recall_vs_exact(qvecs, k=10)
    # Task side (kept SEPARATE per EVAL §5 — ANN recall does not imply relevance).
    ndcg_note = "task nDCG reported separately in beir_eval.json, same candidate/config"
    lat = []
    for q in qvecs[:60]:
        s = time.perf_counter()
        idx.search(q, 10)
        lat.append((time.perf_counter() - s) * 1000.0)
    lat.sort()
    report = {
        "criterion": "C3-3 ANN recall fidelity (vs exact, same space/filters/budgets)",
        "corpus": "beir-scifact full (5183 docs)",
        "queries": len(qvecs),
        "query_sampling": "systematic 1-in-5 over sorted test qids (60 queries); qrels/test.tsv NEVER used for tuning",
        "k": 10,
        "n_lists": idx.n_lists,
        "nprobe": idx.nprobe,
        "ann_recall_at_10": recall["recall_at_k"],
        "required": 0.95,
        "task_ndcg_note": ndcg_note,
        "encode_build_publish_seconds": round(t1 - t0, 1),
        "ivf_search_ms_p50": round(lat[len(lat) // 2], 3),
        "ivf_search_ms_p95": round(lat[int(len(lat) * 0.95)], 3),
        "manifest_hash": receipt["manifest_hash"],
        "encoder": enc.spec.to_dict(),
        "pass": recall["recall_at_k"] >= 0.95,
    }
    (OUT / "ann_recall.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


def gen_abstention() -> dict:
    db = Database(tempfile.mkdtemp(prefix="r03-abs-") + "/abs.db")
    try:
        engine = FilingEngine(db.conn, Taxonomy(db.conn))
        fid = engine.taxonomy.create_facet("Topics", "T")
        db.insert_atom("unrelated-doc", "medical record database configuration entries")
        fm_text = "tags project meeting notes date agenda weekly sync"
        fm_aid = db.insert_atom("frontmatter-doc", fm_text)
        engine.assign_facets(fm_aid, [fid])
        engine.refresh_centroid(fid)
        router = CascadeRouter(db.conn, engine)
        router.warm()
        query = "record of antique clock restoration"
        res = router.route(query)
        report = {
            "criterion": "C3-4 targeted corroboration (query<->candidate-text support)",
            "query": query,
            "db_wide_fts_support": bool(has_lexical_support(db.conn, query)),
            "anchor_support_frontmatter": bool(
                has_lexical_support(db.conn, query, anchor_text=fm_text)),
            "verdict": res.verdict,
            "anchors": res.anchors,
            "spurious_hits": [a for a in res.anchors
                              if a.get("doc_id") in ("frontmatter-doc", "unrelated-doc")],
            "pass": res.verdict == "insufficient_evidence" and res.anchors == [],
        }
        (OUT / "abstention_test.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return report
    finally:
        db.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    results = [gen_coverage(), gen_ann_recall(), gen_abstention()]
    ok = all(r["pass"] for r in results)
    print("R03 EVIDENCE PASS" if ok else "R03 EVIDENCE FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
