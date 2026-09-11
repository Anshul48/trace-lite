#!/usr/bin/env python3
"""Retrieval quality eval: nDCG@10, recall@10, MRR, abstention calibration.

Labeled synthetic corpus: TOPICS distinct-vocab clusters; each query has graded
judgments (2 = exact-phrase doc, 1 = same topic, 0 = other) plus an unanswerable
set (gibberish + off-corpus topics) for abstention scoring.
Writes evidence/quality/quality-<tag>.json and exits nonzero if gates fail.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from trace_lite.filing import FilingEngine, Taxonomy  # noqa: E402
from trace_lite.router import CascadeRouter  # noqa: E402
from trace_lite.store import Database  # noqa: E402

TOPICS = {
    "wal": ["wal", "checkpoint", "sqlite", "frames", "folding", "durability", "truncate"],
    "facets": ["facet", "hearst", "taxonomy", "membership", "subtree", "hierarchy"],
    "vault": ["obsidian", "vault", "markdown", "wikilink", "frontmatter", "sync"],
    "compiler": ["compiler", "budget", "tokens", "salience", "distractor", "packing"],
    "fusion": ["fusion", "reciprocal", "rank", "bm25", "hybrid", "rerank"],
    "tms": ["invariant", "authority", "lattice", "revocation", "cascade", "contract"],
}

QUERIES_PER_TOPIC = 12
UNANSWERABLE = (
    [f"xqzt{i} blorpt{i} wqkj{i}" for i in range(30)]
    + ["quantum knitting patterns for cats", "sourdough starter hydration ratios",
       "offshore sailing knots guide", "antique clock restoration manual"]
)


def build_topic_doc(topic: str, i: int, exact: str) -> str:
    vocab = TOPICS[topic]
    filler = " ".join(vocab[(i + k) % len(vocab)] for k in range(4))
    return f"{exact} {filler} record {i}"


def dcg(relevances: list[int], k: int) -> float:
    return sum(rel / math.log2(idx + 2) for idx, rel in enumerate(relevances[:k]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-per-topic", type=int, default=400)
    parser.add_argument("--tag", default="10k")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or (REPO / "evidence" / "quality" / f"quality-{args.tag}.json")

    tmp = Path(tempfile.mkdtemp(prefix="trace-lite-quality-"))
    db = Database(tmp / "q.db")
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    facet_of_topic: dict[str, str] = {}
    doc_topic: dict[str, str] = {}
    exact_doc: dict[str, str] = {}  # query -> doc_id with the exact phrase
    queries: list[tuple[str, str]] = []
    for topic, vocab in TOPICS.items():
        fid = taxonomy.create_facet("Topics", topic.title())
        facet_of_topic[topic] = fid
        exact = f"how does {' '.join(vocab[:2])} behave under load"
        for i in range(args.docs_per_topic):
            text = build_topic_doc(topic, i, " ".join(vocab[:2]) if i == 0 else "")
            doc_id = f"{topic}-{i:05d}"
            aid = db.insert_atom(doc_id=doc_id, text=text)
            engine.assign_facets(aid, [fid])
            doc_topic[doc_id] = topic
            if i == 0:
                exact_doc[exact] = doc_id
        for q in range(QUERIES_PER_TOPIC):
            queries.append((f"how does {' '.join(vocab[:2])} behave under load", topic))
            queries.append((f"{vocab[2]} {vocab[3]} tuning guide", topic))
    for fid in facet_of_topic.values():
        engine.refresh_centroid(fid)
    router = CascadeRouter(db.conn, engine)
    router.warm()

    # -- answerable: ranking quality --------------------------------------
    # recall@k is uninformative here (400 relevant/topic dwarfs k=10), so we
    # report success@10 (>=1 relevant retrieved) alongside nDCG@10 and MRR.
    ndcgs, successes, rrs, tier_of = [], [], [], {}
    for query, topic in queries:
        result = router.route(query, limit=10)
        tier_of[result.tier_used] = tier_of.get(result.tier_used, 0) + 1
        rels = []
        for a in result.anchors:
            doc = a.get("doc_id", "")
            if doc == exact_doc.get(query):
                rels.append(2)
            elif doc_topic.get(doc) == topic:
                rels.append(1)
            else:
                rels.append(0)
        ideal = sorted(rels, reverse=True)
        denom = dcg(ideal, 10)
        ndcgs.append(dcg(rels, 10) / denom if denom else 1.0)
        successes.append(1.0 if any(r > 0 for r in rels) else 0.0)
        first = next((i for i, r in enumerate(rels) if r > 0), None)
        rrs.append(1.0 / (first + 1) if first is not None else 0.0)

    # -- unanswerable: abstention ------------------------------------------
    abstained = sum(
        1 for q in UNANSWERABLE if router.route(q).verdict == "insufficient_evidence"
    )
    # selective risk: answered answerable queries with zero relevant in top-10
    answered = sum(1 for query, _ in queries
                   if router.route(query).verdict == "answerable")
    wrong = 0
    for query, topic in queries:
        r = router.route(query)
        if r.verdict == "answerable" and not any(
                doc_topic.get(a.get("doc_id", "")) == topic for a in r.anchors):
            wrong += 1

    report = {
        "tag": args.tag,
        "docs": args.docs_per_topic * len(TOPICS),
        "answerable_queries": len(queries),
        "unanswerable_queries": len(UNANSWERABLE),
        "ndcg_at_10": round(sum(ndcgs) / len(ndcgs), 4),
        "success_at_10": round(sum(successes) / len(successes), 4),
        "mrr": round(sum(rrs) / len(rrs), 4),
        "tiers": tier_of,
        "abstention_recall": round(abstained / len(UNANSWERABLE), 4),
        "coverage": round(answered / len(queries), 4),
        "selective_error": round(wrong / max(1, answered), 4),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    report["gates"] = {
        "ndcg_at_10_gte_0.70": report["ndcg_at_10"] >= 0.70,
        "abstention_recall_gte_0.90": report["abstention_recall"] >= 0.90,
        "selective_error_lte_0.10": report["selective_error"] <= 0.10,
    }
    report["pass"] = all(report["gates"].values())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    db.close()
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
