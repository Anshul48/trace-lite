"""Comparative A/B Benchmark Analysis and Pareto Frontier Evaluation for Trace-Lite."""

import json
import sys
from pathlib import Path


def load_report(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compare_reports(alpha_path: str, beta_path: str) -> dict:
    alpha = load_report(alpha_path)
    beta = load_report(beta_path)

    alpha_summary = alpha.get("summary_metrics", {})
    beta_summary = beta.get("summary_metrics", {})

    metrics_to_compare = [
        ("Precision@1", "precision_at_1", "%"),
        ("Recall@5", "recall_at_5", "%"),
        ("Recall@20", "recall_at_20", "%"),
        ("nDCG@10", "ndcg_at_10", "score"),
        ("nDCG@30", "ndcg_at_30", "score"),
        ("Complete Gold Coverage@10", "complete_gold_coverage_at_10", "%"),
        ("Complete Gold Coverage@20", "complete_gold_coverage_at_20", "%"),
        ("Citation Precision@5", "citation_precision_at_5", "%"),
        ("Citation Precision@10", "citation_precision_at_10", "%"),
        ("Abstention Accuracy", "abstention_accuracy", "%"),
        ("Latency p50", "latency_p50_ms", "ms"),
        ("Latency p95", "latency_p95_ms", "ms"),
        ("Latency mean", "latency_mean_ms", "ms"),
    ]

    print("=" * 88)
    print("           TRACE-LITE A/B BENCHMARK COMPARATIVE EVALUATION (ALPHA vs BETA)")
    print("=" * 88)
    print(f"{'Metric':<30} | {'Alpha (Baseline)':<16} | {'Beta (HippoRAG PPR)':<18} | {'Delta':<12}")
    print("-" * 88)

    summary_diffs = {}
    for label, key, unit in metrics_to_compare:
        a_val = alpha_summary.get(key, 0.0)
        b_val = beta_summary.get(key, 0.0)
        delta = b_val - a_val
        summary_diffs[key] = {"alpha": a_val, "beta": b_val, "delta": delta}

        if unit == "%":
            a_str = f"{a_val * 100:.2f}%"
            b_str = f"{b_val * 100:.2f}%"
            d_str = f"{'+' if delta >= 0 else ''}{delta * 100:.2f}%"
        elif unit == "ms":
            a_str = f"{a_val:.2f} ms"
            b_str = f"{b_val:.2f} ms"
            d_str = f"{'+' if delta >= 0 else ''}{delta:.2f} ms"
        else:
            a_str = f"{a_val:.4f}"
            b_str = f"{b_val:.4f}"
            d_str = f"{'+' if delta >= 0 else ''}{delta:.4f}"

        print(f"{label:<30} | {a_str:<16} | {b_str:<18} | {d_str:<12}")

    print("=" * 88)
    print("\n" + "=" * 88)
    print("                      PER-CATEGORY SOTA BREAKDOWN (RECALL@5 / nDCG@10)")
    print("=" * 88)
    print(f"{'Category ID':<10} | {'Name':<24} | {'Alpha R@5':<10} | {'Beta R@5':<10} | {'Delta R@5':<10} | {'Beta nDCG@10':<12}")
    print("-" * 88)

    alpha_cats = alpha.get("category_breakdown", {})
    beta_cats = beta.get("category_breakdown", {})

    for cat_id in sorted(alpha_cats.keys()):
        a_c = alpha_cats[cat_id]
        b_c = beta_cats.get(cat_id, {})
        name = b_c.get("category_name", a_c.get("category_name", "Unknown"))
        a_r5 = a_c.get("recall_at_5", 0.0)
        b_r5 = b_c.get("recall_at_5", 0.0)
        d_r5 = b_r5 - a_r5
        b_ndcg = b_c.get("ndcg_at_10", 0.0)

        print(f"{cat_id:<10} | {name:<24} | {a_r5 * 100:6.2f}%    | {b_r5 * 100:6.2f}%    | {'+' if d_r5 >= 0 else ''}{d_r5 * 100:6.2f}%    | {b_ndcg:6.4f}")

    print("=" * 88)
    return summary_diffs


if __name__ == "__main__":
    def resolve_report_path(arg_path: str, fallback_filename: str) -> str:
        candidates = [
            Path(arg_path),
            Path("benchmarks/results") / fallback_filename,
            Path(fallback_filename),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return arg_path

    alpha_arg = sys.argv[1] if len(sys.argv) > 1 else "report_alpha.json"
    beta_arg = sys.argv[2] if len(sys.argv) > 2 else "report_beta.json"

    alpha = resolve_report_path(alpha_arg, "report_alpha.json")
    beta = resolve_report_path(beta_arg, "report_beta.json")
    compare_reports(alpha, beta)
