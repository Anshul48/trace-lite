#!/usr/bin/env python3
"""Unified frozen-BEIR scoring for trace-lite (no downloads; local frozen data only).

Indexes the identical frozen corpus each system is judged on and scores the same
test queries with nDCG@10 (gain 2^rel-1), recall@10, and 10k-resample bootstrap
CIs (seed 42, mirroring the trace bench methodology). Compares against trace's
recorded baselines — same datasets, same metric — without running trace's stack.

Data root: /mnt/c/Users/anshu/AppData/Local/trace-bench-data/
  beir-scifact | beir-nfcorpus | beir-fiqa, each with raw/<ds>/{corpus,queries}.jsonl
  and raw/<ds>/qrels/test.tsv, plus a checksums.json manifest (SHA-256, verified
  before indexing).

Configuration note: trace-lite runs FLAT here (no facet assignment — BEIR ships
no facet labels). Tier 1 lexical + Tier 3 hybrid carry retrieval; the beam has no
centroids and yields nothing. Abstentions score 0 (never filtered).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
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

DATA_ROOT = Path("/mnt/c/Users/anshu/AppData/Local/trace-bench-data")

DATASETS = {
    "scifact": {"dir": "beir-scifact/raw/scifact", "floor": 0.65, "recorded": 0.6650593457199848},
    "nfcorpus": {"dir": "beir-nfcorpus/raw/nfcorpus", "floor": 0.32, "recorded": 0.3523728976713569},
    "fiqa": {"dir": "beir-fiqa/raw/fiqa", "floor": 0.38, "recorded": 0.4028043038021869},
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksums(ds_dir: Path, files: list[str]) -> dict[str, bool]:
    manifest = json.loads((ds_dir.parent.parent / "checksums.json").read_text())
    expected = {k: v["sha256"] for k, v in manifest["files"].items()}
    # Manifest keys are prefixed, e.g. 'raw/scifact/corpus.jsonl'.
    return {
        f: sha256_file(ds_dir / f) == expected.get(f"raw/{ds_dir.name}/{f}")
        for f in files
    }


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        header = True
        for line in fh:
            parts = line.strip().split("\t")
            if header and parts[0] == "query-id":
                header = False
                continue
            header = False
            if len(parts) != 3:
                continue
            qrels.setdefault(parts[0], {})[parts[1]] = int(parts[2])
    return qrels


def gain(rel: int) -> float:
    return float(2**rel - 1)


def dcg_at_k(ranked_rels: list[int], k: int) -> float:
    return sum(gain(r) / math.log2(i + 2) for i, r in enumerate(ranked_rels[:k]))


def bootstrap_ci(scores: list[float], n_resamples: int = 10000, seed: int = 42):
    rng = random.Random(seed)
    n = len(scores)
    means = sorted(sum(scores[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_resamples))
    return sum(scores) / n, means[int(0.025 * n_resamples)], means[min(n_resamples - 1, int(0.975 * n_resamples))]


def run_dataset(name: str, root: Path) -> dict:
    spec = DATASETS[name]
    ds_dir = root / spec["dir"]
    files = {"corpus": "corpus.jsonl", "queries": "queries.jsonl", "qrels": "qrels/test.tsv"}
    checks = verify_checksums(ds_dir, list(files.values()))
    if not all(checks.values()):
        raise SystemExit(f"{name}: checksum mismatch: {checks}")

    corpus = load_jsonl(ds_dir / files["corpus"])
    queries = {r["_id"]: r["text"] for r in load_jsonl(ds_dir / files["queries"])}
    qrels = load_qrels(ds_dir / files["qrels"])
    qids = [qid for qid in qrels if qid in queries]

    tmp = Path(tempfile.mkdtemp(prefix=f"tl-beir-{name}-"))
    db = Database(tmp / "beir.db")
    taxonomy = Taxonomy(db.conn)
    engine = FilingEngine(db.conn, taxonomy)
    start = time.perf_counter()
    docs = [(r["_id"], f"{r.get('title', '')} {r.get('text', '')}".strip()) for r in corpus]
    for i in range(0, len(docs), 2000):
        db.bulk_ingest(docs[i:i + 2000])
    ingest_s = time.perf_counter() - start
    router = CascadeRouter(db.conn, engine)
    router.warm()

    ndcgs, recalls, tiers, abstentions = [], [], {}, 0
    for qid in qids:
        result = router.route(queries[qid], limit=10)
        tiers[result.tier_used] = tiers.get(result.tier_used, 0) + 1
        if not result.anchors:
            abstentions += 1
            ndcgs.append(0.0)
            recalls.append(0.0)
            continue
        rels = [qrels[qid].get(str(a.get("doc_id", "")), 0) for a in result.anchors]
        ideal = sorted((qrels[qid].values()), reverse=True)
        denom = dcg_at_k(ideal, 10)
        ndcgs.append(dcg_at_k(rels, 10) / denom if denom else 1.0)
        recalls.append(sum(1 for d in rels if d > 0) / max(1, sum(1 for v in qrels[qid].values() if v > 0)))
    point, lo, hi = bootstrap_ci(ndcgs)
    report = {
        "dataset": name,
        "checksums_ok": checks,
        "corpus_docs": len(corpus),
        "queries_scored": len(qids),
        "ingest_seconds": round(ingest_s, 1),
        "ndcg_at_10": round(point, 4),
        "bootstrap_95_ci": [round(lo, 4), round(hi, 4)],
        "recall_at_10": round(sum(recalls) / len(recalls), 4),
        "abstentions": abstentions,
        "tiers": tiers,
        "trace_floor": spec["floor"],
        "trace_recorded": round(spec["recorded"], 4),
        "clears_floor": point >= spec["floor"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    db.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=[*DATASETS, "all"], default="all")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--out-dir", type=Path, default=REPO / "evidence" / "beir")
    args = parser.parse_args()
    names = list(DATASETS) if args.dataset == "all" else [args.dataset]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name in names:
        report = run_dataset(name, args.data_root)
        (args.out_dir / f"beir-{name}.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        ok &= report["clears_floor"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
