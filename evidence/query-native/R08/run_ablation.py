#!/usr/bin/env python3
"""R08 PEA ablation runner: paired structural vs +surprise vs +embedding-shift.

Paired design: identical splits, identical B1-equivalent retrieval (in-memory
BM25-lite over porter stems + hashbow-IDF dense cosine, RRF-fused, mapped to
unique doc IDs before top-k), only the segmentation signal differs. Signals
use per-document adaptive thresholds — nothing is tuned on any qrels, so the
DEV runs are measurements, not selection. BEIR test.tsv is scored exactly
once, for the frozen candidates, after all DEV numbers are printed.

Writes (ONLY under evidence/query-native/R08/):
  pea_ablation.json, qualifier_preservation.log, cost_telemetry.txt
Exit nonzero on any internal-consistency failure (byte-exactness, empty
retrieval for all variants, severed structural baseline).
"""

from __future__ import annotations

import json
import math
import random
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

from trace_lite.experimental import refine_cuts, refine_text  # noqa: E402
from trace_lite.experimental import qualifiers as qual_mod  # noqa: E402
from trace_lite.experimental import repair as repair_mod  # noqa: E402
from trace_lite.filing.engine import CENTROID_DIM, text_vector, tokenize  # noqa: E402
from trace_lite.router.fusion import rrf_fuse  # noqa: E402
from trace_lite.router.lexical import porter_stem  # noqa: E402
from trace_lite.source.segmentation import verify_spans  # noqa: E402

OUT = REPO / "evidence" / "query-native" / "R08"
DATA = Path("/mnt/c/Users/anshu/AppData/Local/trace-bench-data")

METHODS = ("structural", "surprise", "shift")

CORPORA = {
    "scifact": {"dir": "beir-scifact/raw/scifact", "dev_split": "train", "dev_n": 150},
    "nfcorpus": {"dir": "beir-nfcorpus/raw/nfcorpus", "dev_split": "train", "dev_n": 100},
    "fiqa": {"dir": "beir-fiqa/raw/fiqa", "dev_split": "train", "dev_n": 60},
}

# B1-equivalent retrieval constants (frozen a priori, same for all variants).
BM25_K1 = 1.2
BM25_B = 0.75
SPARSE_POOL = 50  # R00 lexical_oversample: max(limit*5,50)
DENSE_POOL = 200  # bounded-fixture pool (quality profile uses 2000; disclosed)
FUSED_DEPTH = 100  # R00 fusion: fused[:max(limit*10,100)]
TOP_K = 10
BOOTSTRAP_RESAMPLES = 10000
BOOTSTRAP_SEED = 42

_STEM_CACHE: dict[str, str] = {}


def stem(tok: str) -> str:
    hit = _STEM_CACHE.get(tok)
    if hit is None:
        hit = porter_stem(tok)
        _STEM_CACHE[tok] = hit
    return hit


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


class SpanIndex:
    """B1-equivalent span retrieval, identical for every segmentation variant."""

    def __init__(self, docs: list[tuple[str, str]], method: str) -> None:
        self.doc_ids = [d for d, _ in docs]
        self.span_doc: list[int] = []
        self.span_texts: list[str] = []
        self.seg_seconds = 0.0
        for di, (_, text) in enumerate(docs):
            t0 = time.process_time()
            spans = refine_text(text, method=method)
            self.seg_seconds += time.process_time() - t0
            blob_ok = verify_spans(text, spans)
            if not blob_ok:
                raise RuntimeError(f"byte contract violated: method={method} doc={di}")
            for s in spans:
                self.span_doc.append(di)
                self.span_texts.append(s.text)
        n = len(self.span_texts)
        self.doc_lens = [0] * n
        postings: dict[str, dict[int, int]] = {}
        total_len = 0
        for si, st in enumerate(self.span_texts):
            counts: dict[str, int] = {}
            for tok in tokenize(st):
                key = stem(tok)
                counts[key] = counts.get(key, 0) + 1
            self.doc_lens[si] = sum(counts.values())
            total_len += self.doc_lens[si]
            for term, tf in counts.items():
                postings.setdefault(term, {})[si] = tf
        self.avgdl = total_len / max(1, n)
        self.postings = postings
        self.idf = {
            t: math.log((n - len(d) + 0.5) / (len(d) + 0.5)) + 1.0
            for t, d in postings.items()
        }
        mat = np.asarray(
            [text_vector(st, CENTROID_DIM) for st in self.span_texts],
            dtype=np.float32,
        )
        df = (mat != 0.0).sum(axis=0)
        self.dense_idf = np.log((n + 1.0) / (df + 1.0)) + 1.0
        mat *= self.dense_idf
        norms = np.sqrt((mat**2).sum(axis=1))
        norms[norms == 0.0] = 1.0
        self.matrix = mat / norms[:, None]

    def dense_query(self, query: str) -> np.ndarray:
        vec = np.asarray(text_vector(query, CENTROID_DIM), dtype=np.float32)
        vec *= self.dense_idf
        norm = float(np.sqrt((vec**2).sum())) or 1.0
        return vec / norm

    def search(self, query: str, k: int = TOP_K) -> list[str]:
        qterms = {stem(t) for t in tokenize(query)}
        if not qterms:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term in qterms:
            post = self.postings.get(term)
            if not post:
                continue
            idf = self.idf[term]
            for si, tf in post.items():
                denom = tf + BM25_K1 * (
                    1.0 - BM25_B + BM25_B * self.doc_lens[si] / max(1e-9, self.avgdl)
                )
                scores[si] += idf * tf * (BM25_K1 + 1.0) / denom
        sparse_ranked = [
            si for si, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:SPARSE_POOL]
        qvec = self.dense_query(query)
        if float((qvec != 0.0).sum()) == 0.0:
            dense_ranked: list[int] = []
        else:
            sims = self.matrix @ qvec
            depth = min(DENSE_POOL, len(sims))
            if depth <= 0:
                dense_ranked = []
            else:
                cut = np.argpartition(-sims, kth=depth - 1)[:depth]
                dense_ranked = [
                    int(i) for i in cut[np.argsort(-sims[cut], kind="stable")]
                    if float(sims[i]) > 0.0
                ]
        fused = rrf_fuse(dense_ranked, sparse_ranked)[:FUSED_DEPTH]
        best: dict[int, float] = {}
        for sid, fs in fused:
            doc = self.span_doc[sid]
            if fs > best.get(doc, -1.0):
                best[doc] = fs
        ranked = sorted(best, key=lambda d: (-best[d], d))[:k]
        return [self.doc_ids[d] for d in ranked]


def gain(rel: int) -> float:
    return float(2**rel - 1)


def dcg_at_k(ranked_rels: list[int], k: int) -> float:
    return sum(gain(r) / math.log2(i + 2) for i, r in enumerate(ranked_rels[:k]))


def per_query_scores(
    index: SpanIndex,
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    qids: list[str],
) -> tuple[list[float], list[float], int]:
    ndcgs: list[float] = []
    recalls: list[float] = []
    abstentions = 0
    for qid in qids:
        ranked = index.search(queries[qid])
        rels = [qrels[qid].get(doc_id, 0) for doc_id in ranked]
        ideal = sorted(qrels[qid].values(), reverse=True)
        denom = dcg_at_k(ideal, TOP_K)
        ndcgs.append(dcg_at_k(rels, TOP_K) / denom if denom else 1.0)
        if not ranked:
            abstentions += 1
        nrel = sum(1 for v in qrels[qid].values() if v > 0)
        recalls.append(sum(1 for r in rels if r > 0) / max(1, nrel))
    return ndcgs, recalls, abstentions


def bootstrap_ci(
    scores: list[float], n: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float, float]:
    rng = random.Random(seed)
    m = len(scores)
    means = sorted(sum(scores[rng.randrange(m)] for _ in range(m)) / m for _ in range(n))
    return sum(scores) / m, means[int(0.025 * n)], means[min(n - 1, int(0.975 * n))]


def paired_delta_ci(
    base: list[float],
    cand: list[float],
    n: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, float]:
    """Paired bootstrap CI over per-query deltas (same resample draws)."""
    assert len(base) == len(cand)
    rng = random.Random(seed)
    m = len(base)
    deltas = [c - b for b, c in zip(base, cand)]
    mean = sum(deltas) / m
    draws = sorted(
        sum(deltas[rng.randrange(m)] for _ in range(m)) / m for _ in range(n)
    )
    lo, hi = draws[int(0.025 * n)], draws[min(n - 1, int(0.975 * n))]
    # Two-sided paired permutation-style p: fraction of resampled means at or
    # beyond zero on the observed side, doubled.
    beyond = sum(1 for d in draws if (d <= 0) == (mean > 0)) / n
    return {"mean": mean, "lo": lo, "hi": hi, "p_two_sided": min(1.0, 2.0 * beyond)}


def evaluate_split(
    indexes: dict[str, SpanIndex],
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    qids: list[str],
    label: str,
) -> dict:
    per: dict[str, dict] = {}
    for method in METHODS:
        ndcgs, recalls, abst = per_query_scores(indexes[method], queries, qrels, qids)
        point, lo, hi = bootstrap_ci(ndcgs)
        per[method] = {
            "n": len(qids),
            "ndcg_at_10": round(point, 4),
            "bootstrap_95_ci": [round(lo, 4), round(hi, 4)],
            "recall_at_10": round(sum(recalls) / len(recalls), 4),
            "abstentions": abst,
            "ndcg_raw": ndcgs,
        }
    deltas = {}
    for cand in ("surprise", "shift"):
        d = paired_delta_ci(per["structural"]["ndcg_raw"], per[cand]["ndcg_raw"])
        deltas[f"{cand}_vs_structural"] = {k: round(v, 4) for k, v in d.items()}
    for method in METHODS:
        del per[method]["ndcg_raw"]
    return {"label": label, "n": len(qids), "variants": per, "deltas": deltas}


def build_synthetic() -> tuple[list[tuple[str, str]], list[dict]]:
    """DEV-only synthetic diagnostic (R00 fixtures + §3 edit classes)."""
    docs = [(f"R00-{f.fid}", f.text) for f in qual_mod.fixtures()]
    docs += [
        ("cond-a", "Batching improves throughput by 30% under low contention (fewer syscalls per item)."),
        ("cond-b", "Under high contention, the batching benefit disappears; throughput matches the unbatched path."),
        ("neg-a", "WAL mode allows one writer with concurrent readers."),
        ("neg-b", "The legacy rollback-journal mode does NOT allow concurrent readers during a write."),
        ("amb-a", "Chen (storage team) reports checkpoint latency p99 of 40ms on NVMe."),
        ("amb-b", "Chen (query team) reports checkpoint latency is not the bottleneck; parse time dominates."),
        ("fill-1", "The harbor ferry schedule changes with the tides. Commuters check the board each morning before departure."),
        ("fill-2", "Gardening guides recommend mulching in autumn. Soil temperature matters more than air temperature for bulbs."),
        ("fill-3", "The museum extended its hours for the winter exhibit. Tickets remain timed entry on weekends and holidays."),
        ("fill-4", "Sourdough starter hydration ratios vary by flour. Whole grain absorbs considerably more water than white."),
    ]
    queries = [
        {"qid": "S1", "text": "Does batching improve throughput under high contention?", "gold": ["Q04-contention", "Q11-contention-pair", "cond-b"], "kind": "unless/only+contention"},
        {"qid": "S2", "text": "Does WAL mode allow concurrent readers?", "gold": ["neg-a"], "kind": "negation"},
        {"qid": "S3", "text": "What did Chen of the query team report about latency?", "gold": ["Q05-actor", "amb-b"], "kind": "actor-reversal"},
        {"qid": "S4", "text": "Do readers block during a write without WAL mode?", "gold": ["Q01-unless", "Q10-provided"], "kind": "unless-condition"},
        {"qid": "S5", "text": "What is checkpoint latency p99 on NVMe?", "gold": ["Q06-number", "amb-a"], "kind": "number-unit"},
        {"qid": "S6", "text": "throughput gains from grouping operations when idle", "gold": ["cond-a"], "kind": "synonym-paraphrase"},
        {"qid": "S7", "text": "What are sourdough starter hydration ratios?", "gold": [], "kind": "off-topic-abstention"},
        {"qid": "S8", "text": "checkpoint latency bound for sequential writes", "gold": ["Q06-number", "Q12-only-when"], "kind": "repeated-terms"},
    ]
    return docs, queries


def evaluate_synthetic(indexes: dict[str, SpanIndex]) -> dict:
    docs, queries = build_synthetic()
    _ = docs
    out: dict[str, dict] = {}
    for method in METHODS:
        per_q = []
        hits = 0
        for q in queries:
            ranked = indexes[method].search(q["text"], k=3)
            if not q["gold"]:
                ok = not ranked  # strict: off-topic must abstain
                per_q.append({"qid": q["qid"], "ranked": ranked, "hit": ok})
                hits += ok
            else:
                rank = next(
                    (i + 1 for i, d in enumerate(ranked) if d in q["gold"]), None
                )
                ok = rank is not None
                per_q.append({"qid": q["qid"], "ranked": ranked, "rank": rank, "hit": ok})
                hits += ok
        out[method] = {
            "n": len(queries),
            "hit_at_3": round(hits / len(queries), 4),
            "per_query": per_q,
        }
    return out


def qualifier_probe() -> tuple[dict, list[str]]:
    """C8-2: count predictive cuts strictly inside qualifier paragraphs."""
    lines: list[str] = []
    summary: dict[str, dict[str, int]] = {
        m: {"severed_fixtures": 0, "total_cuts": 0, "fixtures": 0} for m in METHODS
    }
    from trace_lite.source.segmentation import segment_text as _seg

    for fix in qual_mod.fixtures():
        n_struct = len(_seg(fix.text))
        lines.append(f"{fix.fid} [{fix.kind}] structural_spans={n_struct}")
        if n_struct != 1:
            lines.append(f"  NOTE: fixture is not a single structural span; probe N/A")
        for method in ("surprise", "shift"):
            cuts = refine_cuts(fix.text, method=method)
            severed = 1 if (n_struct == 1 and cuts) else 0
            summary[method]["severed_fixtures"] += severed
            summary[method]["total_cuts"] += len(cuts)
            summary[method]["fixtures"] += 1
            lines.append(
                f"  +{method}: cuts={cuts} severed={severed}"
            )
        summary["structural"]["fixtures"] += 1
    lines.append(
        "GATE: promotion requires zero severed conditions for a promoted method."
    )
    return summary, lines


def repair_probe() -> dict:
    """EVALUATION §6: deterministic closure vs residual-threshold predictor."""
    assertions = repair_mod.base_assertions()
    comps = repair_mod.compare_all(assertions)
    summaries = repair_mod.summarize(comps, assertions)
    cases = [
        {
            "edit_id": c.edit_id,
            "kind": c.kind,
            "meaning_changed": c.meaning_changed,
            "residual": round(c.residual, 4),
            "expected": c.expected_invalidated,
            "deterministic": c.deterministic_invalidated,
            "predictive": {str(k): v for k, v in c.predictive_invalidated.items()},
        }
        for c in comps
    ]
    return {
        "cases": cases,
        "summary": {
            k: {
                "missed_meaningful": s.missed_meaningful,
                "false_invalidations": s.false_invalidations,
                "touched_fraction": round(s.touched_fraction, 4),
                "edits": s.total_edits,
            }
            for k, s in summaries.items()
        },
    }


def cost_probe(sample_docs: list[tuple[str, str]]) -> tuple[dict, list[str]]:
    """C8-3: per-source CPU ms (process_time) + RSS/tracemalloc per method."""
    rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    per: dict[str, dict] = {}
    lines = [
        f"sample_docs={len(sample_docs)} clock=time.process_time mem=tracemalloc+ru_maxrss",
        f"ru_maxrss_before_kb={rss0}",
    ]
    for method in METHODS:
        tracemalloc.start()
        t0 = time.process_time()
        times = []
        spans = 0
        for _, text in sample_docs:
            s0 = time.process_time()
            out = refine_text(text, method=method)
            times.append((time.process_time() - s0) * 1000.0)
            spans += len(out)
        cpu = time.process_time() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        times_sorted = sorted(times)
        per[method] = {
            "per_source_ms_mean": round(sum(times) / len(times), 3),
            "per_source_ms_p50": round(times_sorted[len(times) // 2], 3),
            "per_source_ms_p95": round(times_sorted[min(len(times) - 1, int(0.95 * len(times)))], 3),
            "per_source_ms_max": round(times_sorted[-1], 3),
            "total_cpu_s": round(cpu, 3),
            "spans_per_source": round(spans / len(sample_docs), 3),
            "tracemalloc_peak_kb": round(peak / 1024, 1),
            "ru_maxrss_kb": rss,
        }
        lines.append(
            f"{method}: mean={per[method]['per_source_ms_mean']}ms "
            f"p50={per[method]['per_source_ms_p50']}ms "
            f"p95={per[method]['per_source_ms_p95']}ms "
            f"max={per[method]['per_source_ms_max']}ms "
            f"spans/src={per[method]['spans_per_source']} "
            f"tracemalloc_peak_kb={per[method]['tracemalloc_peak_kb']} "
            f"ru_maxrss_kb={rss}"
        )
    return per, lines


def git_base() -> dict:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=20
            ).stdout.strip()
        except Exception:
            return "unknown"

    return {"commit": run("rev-parse", "HEAD"), "status": run("status", "--short")}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    base = git_base()
    print(f"[R08] base commit={base['commit']}", flush=True)

    corpora: dict[str, dict] = {}
    for name, spec in CORPORA.items():
        ds_dir = DATA / spec["dir"]
        corpus = load_jsonl(ds_dir / "corpus.jsonl")
        queries = {r["_id"]: r["text"] for r in load_jsonl(ds_dir / "queries.jsonl")}
        docs = [
            (r["_id"], f"{r.get('title', '')} {r.get('text', '')}".strip())
            for r in corpus
        ]
        dev_qrels = load_qrels(ds_dir / f"qrels/{spec['dev_split']}.tsv")
        test_qrels = load_qrels(ds_dir / "qrels/test.tsv")
        dev_qids = sorted(q for q in dev_qrels if q in queries)[: spec["dev_n"]]
        test_qids = sorted(q for q in test_qrels if q in queries)
        corpora[name] = {
            "docs": docs,
            "queries": queries,
            "dev_qrels": dev_qrels,
            "test_qrels": test_qrels,
            "dev_qids": dev_qids,
            "test_qids": test_qids,
            "n_docs": len(docs),
        }
        print(
            f"[R08] {name}: docs={len(docs)} dev({spec['dev_split']})={len(dev_qids)} "
            f"test={len(test_qids)}",
            flush=True,
        )

    # ---- cost telemetry first (cheap, independent) ----
    sample_docs = corpora["scifact"]["docs"][:200]
    cost, cost_lines = cost_probe(sample_docs)

    # ---- qualifier + repair probes (deterministic, no qrels) ----
    qual_summary, qual_lines = qualifier_probe()
    if qual_summary["structural"]["severed_fixtures"] != 0:  # sanity: baseline severs nothing
        raise RuntimeError("structural baseline severed a qualifier fixture")
    repair = repair_probe()

    # ---- DEV ablation (measurement only; zero thresholds touched) ----
    dev: dict[str, dict] = {}
    indexes: dict[str, dict[str, SpanIndex]] = {}
    for name, corp in corpora.items():
        idx = {}
        for method in METHODS:
            t0 = time.time()
            idx[method] = SpanIndex(corp["docs"], method)
            print(
                f"[R08] {name}/{method}: {len(idx[method].span_texts)} spans "
                f"seg={idx[method].seg_seconds:.1f}s build={time.time()-t0:.1f}s",
                flush=True,
            )
        indexes[name] = idx
        dev[name] = evaluate_split(
            idx, corp["queries"], corp["dev_qrels"], corp["dev_qids"],
            label=f"{spec_dev_label(name)}-DEV",
        )
        for cand, d in dev[name]["deltas"].items():
            v = dev[name]["variants"]
            print(
                f"[R08] DEV {name} {cand}: d_nDCG={d['mean']:+.4f} "
                f"95%CI=[{d['lo']:+.4f},{d['hi']:+.4f}] p={d['p_two_sided']:.3f} "
                f"(struct={v['structural']['ndcg_at_10']})",
                flush=True,
            )

    # ---- synthetic DEV diagnostic (same indexes? No: dedicated tiny indexes) ----
    syn_docs, _ = build_synthetic()
    syn_idx = {m: SpanIndex(syn_docs, m) for m in METHODS}
    synthetic = evaluate_synthetic(syn_idx)

    # ---- FROZEN TEST run: test.tsv scored exactly once, no iteration ----
    print("[R08] DEV complete. Running single frozen TEST scoring (no iteration).", flush=True)
    test: dict[str, dict] = {}
    for name, corp in corpora.items():
        test[name] = evaluate_split(
            indexes[name], corp["queries"], corp["test_qrels"], corp["test_qids"],
            label="test.tsv-FINAL",
        )
        for cand, d in test[name]["deltas"].items():
            v = test[name]["variants"]
            print(
                f"[R08] TEST {name} {cand}: d_nDCG={d['mean']:+.4f} "
                f"95%CI=[{d['lo']:+.4f},{d['hi']:+.4f}] p={d['p_two_sided']:.3f} "
                f"(struct={v['structural']['ndcg_at_10']})",
                flush=True,
            )

    # ---- disposition inputs ----
    promotable = {"surprise": True, "shift": True}
    reasons = []
    for cand in ("surprise", "shift"):
        if qual_summary[cand]["severed_fixtures"] > 0:
            promotable[cand] = False
            reasons.append(
                f"+{cand}: C8-2 severed "
                f"{qual_summary[cand]['severed_fixtures']}/12 qualifier fixtures"
            )
    for cand in ("surprise", "shift"):
        key = f"{cand}_vs_structural"
        test_means = [test[n]["deltas"][key]["mean"] for n in CORPORA]
        test_los = [test[n]["deltas"][key]["lo"] for n in CORPORA]
        if not any(m >= 0.03 and lo > 0 for m, lo in zip(test_means, test_los)):
            promotable[cand] = False
            reasons.append(
                f"+{cand}: no corpus clears +0.03 with CI above zero "
                f"(means={test_means})"
            )
    for cand in ("surprise", "shift"):
        p95 = cost[cand]["per_source_ms_p95"]
        if p95 > 50.0:
            promotable[cand] = False
            reasons.append(f"+{cand}: p95 {p95}ms > 50ms PARK ceiling")
    disposition = (
        "PROMOTE"
        if any(promotable.values())
        else "PARK"
    )
    if disposition == "PROMOTE":
        for cand in ("surprise", "shift"):
            if not promotable[cand]:
                reasons.append(f"+{cand}: not promoted (see above)")
    else:
        reasons.append("default expectation per packet supplement: PARK unless bar clearly cleared")

    ablation = {
        "contract": "TL-QN-2026-09-12.1",
        "packet": "R08-prediction",
        "kind": "experiment",
        "base": base,
        "design": {
            "paired": "same splits, same B1-equivalent retrieval, only segmentation signal differs",
            "variants": ["structural(R02)", "+surprise(ngram-surprise-trigram)", "+shift(hashbow-shift-cos)"],
            "combination": "separate candidates only; no combined smart variant",
            "thresholds": "per-document adaptive mean+0.5std, max 2 cuts/span; nothing tuned on any qrels",
            "ci": "10k-resample bootstrap seed 42; paired delta CIs with identical draws",
            "dev_policy": "train splits only (scifact 150, nfcorpus 100, fiqa 60 sorted qids); test.tsv scored once frozen",
            "retrieval": {
                "profile": "B1-equivalent flat hybrid (NOT the production CascadeRouter)",
                "sparse": f"BM25-lite k1={BM25_K1} b={BM25_B} porter stems, pool={SPARSE_POOL}",
                "dense": f"hashbow-128 IDF-weighted cosine (R03 space), pool={DENSE_POOL}",
                "fusion": f"RRF K=60, fused[:{FUSED_DEPTH}], max-score map spans->unique doc IDs, top-{TOP_K}",
                "metric": "nDCG@10 gain 2^rel-1 + Recall@10",
            },
        },
        "splits": {
            name: {
                "docs": c["n_docs"],
                "dev": f"{CORPORA[name]['dev_split']}.tsv",
                "dev_q": len(c["dev_qids"]),
                "test_q": len(c["test_qids"]),
            }
            for name, c in corpora.items()
        },
        "dev": dev,
        "synthetic_dev": synthetic,
        "test_final": test,
        "qualifier": qual_summary,
        "repair": repair,
        "cost": cost,
        "promotion_bar": "nDCG>=+0.03 at <=10ms CPU per source; insignificant or >50ms -> PARK",
        "disposition": disposition,
        "disposition_reasons": reasons,
        "elapsed_s": round(time.time() - t_start, 1),
    }
    (OUT / "pea_ablation.json").write_text(json.dumps(ablation, indent=2))
    (OUT / "qualifier_preservation.log").write_text("\n".join(qual_lines) + "\n")
    (OUT / "cost_telemetry.txt").write_text("\n".join(cost_lines) + "\n")
    print(f"[R08] disposition={disposition} elapsed={ablation['elapsed_s']}s", flush=True)
    for r in reasons:
        print(f"[R08]   - {r}", flush=True)
    return 0


def spec_dev_label(name: str) -> str:
    return f"{CORPORA[name]['dev_split']}.tsv"


if __name__ == "__main__":
    raise SystemExit(main())

