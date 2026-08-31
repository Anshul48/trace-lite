"""Benchmark result aggregation, category slicing, statistical significance, and comparison reporting."""

import csv
import json
import math
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from benchmarks.metrics.retrieval import CaseRetrievalMetrics
from benchmarks.metrics.organization import OrganizationMetrics
from benchmarks.metrics.operations import OpsProfile


@dataclass
class BaselineEvaluationResult:
    """Metrics for a single baseline engine across all evaluated cases."""
    baseline_name: str
    overall_recall_at_5: float = 0.0
    overall_recall_at_20: float = 0.0
    overall_ndcg_at_10: float = 0.0
    overall_ndcg_at_30: float = 0.0
    overall_complete_coverage_at_10: float = 0.0
    overall_complete_coverage_at_20: float = 0.0
    overall_citation_precision_at_5: float = 0.0
    overall_mrr: float = 0.0
    overall_multihop_coverage: float = 0.0
    overall_abstention_accuracy: float = 1.0
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)
    case_results: list[dict] = field(default_factory=list)
    ops: dict = field(default_factory=dict)
    organization: dict = field(default_factory=dict)


@dataclass
class BenchmarkRunResult:
    """Complete benchmark execution report across all evaluated baselines."""
    run_id: str
    dataset_name: str
    dataset_version: str
    timestamp: str
    corpus_sha256: str
    total_cases: int
    release_authority: bool = False
    hardware_info: dict = field(default_factory=dict)
    baselines: dict[str, BaselineEvaluationResult] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BenchmarkRunResult":
        baselines_raw = data.get("baselines", {})
        parsed_baselines: dict[str, BaselineEvaluationResult] = {}
        for k, v in baselines_raw.items():
            if isinstance(v, BaselineEvaluationResult):
                parsed_baselines[k] = v
            elif isinstance(v, dict):
                parsed_baselines[k] = BaselineEvaluationResult(**v)
        data_copy = dict(data)
        data_copy["baselines"] = parsed_baselines
        return cls(**data_copy)

    def save_json(self, file_path: Path | str) -> None:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def save_csv(self, file_path: Path | str) -> None:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for b_name, res in self.baselines.items():
            rows.append({
                "run_id": self.run_id,
                "dataset": self.dataset_name,
                "baseline": b_name,
                "recall_at_5": f"{res.overall_recall_at_5:.4f}",
                "recall_at_20": f"{res.overall_recall_at_20:.4f}",
                "ndcg_at_10": f"{res.overall_ndcg_at_10:.4f}",
                "complete_coverage_at_10": f"{res.overall_complete_coverage_at_10:.4f}",
                "citation_precision_at_5": f"{res.overall_citation_precision_at_5:.4f}",
                "mrr": f"{res.overall_mrr:.4f}",
                "abstention_accuracy": f"{res.overall_abstention_accuracy:.4f}",
                "latency_p95_ms": f"{res.ops.get('latency_p95_ms', 0.0):.2f}",
                "qps": f"{res.ops.get('concurrent_qps', 0.0):.2f}",
            })
        if rows:
            with open(p, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)


class BenchmarkAggregator:
    """Aggregates individual case metrics into overall and per-category statistics."""

    @staticmethod
    def aggregate_baseline(
        baseline_name: str,
        case_metrics: list[CaseRetrievalMetrics],
        ops_profile: OpsProfile | None = None,
        org_metrics: OrganizationMetrics | None = None,
    ) -> BaselineEvaluationResult:
        if not case_metrics:
            return BaselineEvaluationResult(baseline_name=baseline_name)

        n = len(case_metrics)
        r5 = sum(m.recall_at_5 for m in case_metrics) / n
        r20 = sum(m.recall_at_20 for m in case_metrics) / n
        ndcg10 = sum(m.ndcg_at_10 for m in case_metrics) / n
        ndcg30 = sum(m.ndcg_at_30 for m in case_metrics) / n
        cov10 = sum(m.complete_gold_coverage_at_10 for m in case_metrics) / n
        cov20 = sum(m.complete_gold_coverage_at_20 for m in case_metrics) / n
        prec5 = sum(m.citation_precision_at_5 for m in case_metrics) / n
        mrr = sum(m.mrr for m in case_metrics) / n
        mhop = sum(m.multihop_coverage for m in case_metrics) / n
        abstain = sum(m.abstention_accuracy for m in case_metrics) / n

        # Category breakdown
        categories: dict[str, list[CaseRetrievalMetrics]] = {}
        for m in case_metrics:
            categories.setdefault(m.category, []).append(m)

        breakdown: dict[str, dict[str, float]] = {}
        for cat, items in categories.items():
            k = len(items)
            breakdown[cat] = {
                "count": k,
                "recall_at_5": sum(x.recall_at_5 for x in items) / k,
                "ndcg_at_10": sum(x.ndcg_at_10 for x in items) / k,
                "complete_coverage_at_10": sum(x.complete_gold_coverage_at_10 for x in items) / k,
                "citation_precision_at_5": sum(x.citation_precision_at_5 for x in items) / k,
                "abstention_accuracy": sum(x.abstention_accuracy for x in items) / k,
            }

        return BaselineEvaluationResult(
            baseline_name=baseline_name,
            overall_recall_at_5=r5,
            overall_recall_at_20=r20,
            overall_ndcg_at_10=ndcg10,
            overall_ndcg_at_30=ndcg30,
            overall_complete_coverage_at_10=cov10,
            overall_complete_coverage_at_20=cov20,
            overall_citation_precision_at_5=prec5,
            overall_mrr=mrr,
            overall_multihop_coverage=mhop,
            overall_abstention_accuracy=abstain,
            category_breakdown=breakdown,
            case_results=[asdict(m) for m in case_metrics],
            ops=asdict(ops_profile) if ops_profile else {},
            organization=asdict(org_metrics) if org_metrics else {},
        )


def _get_val(obj, attr: str, default: float = 0.0) -> float:
    if isinstance(obj, dict):
        return float(obj.get(attr, default))
    return float(getattr(obj, attr, default))


def compute_paired_statistics(current_scores: list[float], baseline_scores: list[float]) -> dict:
    """
    Compute paired delta, standard error, t-statistic, p-value (approximate Student-t), and 95% CI.
    """
    if not current_scores or not baseline_scores or len(current_scores) != len(baseline_scores):
        return {"delta": 0.0, "p_value": 1.0, "ci_lower": 0.0, "ci_upper": 0.0, "stat_significant": False}

    diffs = [c - b for c, b in zip(current_scores, baseline_scores)]
    n = len(diffs)
    mean_diff = sum(diffs) / n

    if n < 2:
        return {"delta": mean_diff, "p_value": 1.0, "ci_lower": mean_diff, "ci_upper": mean_diff, "stat_significant": False}

    var = sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)
    std_err = math.sqrt(var / n) if var > 0 else 0.0

    # 95% CI margin
    margin = 1.96 * std_err
    ci_lower = mean_diff - margin
    ci_upper = mean_diff + margin

    # t-statistic and two-tailed normal approximation p-value
    if std_err > 0:
        t_stat = mean_diff / std_err
        # Gaussian approximation for CDF
        p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t_stat) / math.sqrt(2.0))))
    else:
        p_val = 1.0 if mean_diff == 0 else 0.0

    return {
        "delta": mean_diff,
        "std_err": std_err,
        "p_value": float(max(0.0, min(1.0, p_val))),
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "stat_significant": (p_val < 0.05),
    }


def check_release_gates(
    result: BaselineEvaluationResult,
    release_authority: bool = True,
) -> tuple[bool, list[str]]:
    """
    Assert production release gate criteria:
    - Release authority authority flag is True
    - Complete Gold Coverage >= 80% (0.80)
    - Citation Precision >= 90% (0.90)
    - Source Atom Coverage == 100% (1.00)
    - Abstention Accuracy >= 95% (0.95)
    """
    failures = []

    if not release_authority:
        failures.append("Corpus is marked release_authority: false (development fixture).")

    if result.overall_complete_coverage_at_10 < 0.80:
        failures.append(f"Complete Gold Coverage@10 ({result.overall_complete_coverage_at_10:.1%}) is below gate threshold of 80.0%.")

    if result.overall_citation_precision_at_5 < 0.90:
        failures.append(f"Citation Precision@5 ({result.overall_citation_precision_at_5:.1%}) is below gate threshold of 90.0%.")

    org = result.organization or {}
    source_cov = float(org.get("source_atom_coverage", 1.0))
    if source_cov < 1.0:
        failures.append(f"Source Atom Coverage ({source_cov:.1%}) indicates dropped source atoms (must be 100.0%).")

    if result.overall_abstention_accuracy < 0.95:
        failures.append(f"Abstention Accuracy ({result.overall_abstention_accuracy:.1%}) is below gate threshold of 95.0%.")

    passed = (len(failures) == 0)
    return passed, failures


def generate_comparison_markdown(current: BenchmarkRunResult, baseline: BenchmarkRunResult) -> str:
    """Generate markdown report comparing two benchmark runs with paired statistical p-values."""
    lines = [
        f"# Benchmark Comparison: {current.run_id} vs {baseline.run_id}",
        "",
        f"- **Dataset**: {current.dataset_name} (v{current.dataset_version})",
        f"- **Cases**: {current.total_cases}",
        f"- **Current Date**: {current.timestamp}",
        f"- **Baseline Date**: {baseline.timestamp}",
        "",
        "## Overall Metrics Comparison & Statistical Significance",
        "",
        "| Baseline | Metric | Baseline Run | Current Run | Delta | 95% CI | p-value | Significant (p<0.05)? |",
        "|---|---|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]

    all_keys = set(current.baselines.keys()) | set(baseline.baselines.keys())
    for b_key in sorted(all_keys):
        c_base = current.baselines.get(b_key)
        b_base = baseline.baselines.get(b_key)
        if not c_base or not b_base:
            continue

        c_cases = c_base.case_results if isinstance(c_base, BaselineEvaluationResult) else c_base.get("case_results", [])
        b_cases = b_base.case_results if isinstance(b_base, BaselineEvaluationResult) else b_base.get("case_results", [])

        metric_pairs = [
            ("Recall@5", "recall_at_5", _get_val(b_base, "overall_recall_at_5"), _get_val(c_base, "overall_recall_at_5")),
            ("Recall@20", "recall_at_20", _get_val(b_base, "overall_recall_at_20"), _get_val(c_base, "overall_recall_at_20")),
            ("nDCG@10", "ndcg_at_10", _get_val(b_base, "overall_ndcg_at_10"), _get_val(c_base, "overall_ndcg_at_10")),
            ("Complete Coverage@10", "complete_gold_coverage_at_10", _get_val(b_base, "overall_complete_coverage_at_10"), _get_val(c_base, "overall_complete_coverage_at_10")),
            ("Citation Precision@5", "citation_precision_at_5", _get_val(b_base, "overall_citation_precision_at_5"), _get_val(c_base, "overall_citation_precision_at_5")),
            ("MRR", "mrr", _get_val(b_base, "overall_mrr"), _get_val(c_base, "overall_mrr")),
            ("Abstention Accuracy", "abstention_accuracy", _get_val(b_base, "overall_abstention_accuracy"), _get_val(c_base, "overall_abstention_accuracy")),
        ]

        for m_label, m_field, b_val, c_val in metric_pairs:
            c_scores = [float(item.get(m_field, 0.0)) for item in c_cases]
            b_scores = [float(item.get(m_field, 0.0)) for item in b_cases]

            stats = compute_paired_statistics(c_scores, b_scores)
            delta = stats["delta"]
            delta_str = f"+{delta:.3f}" if delta > 0 else f"{delta:.3f}"
            ci_str = f"[{stats['ci_lower']:.3f}, {stats['ci_upper']:.3f}]"
            p_val_str = f"{stats['p_value']:.4f}"
            sig_str = "**YES**" if stats["stat_significant"] else "No"

            lines.append(f"| `{b_key}` | {m_label} | {b_val:.3f} | {c_val:.3f} | {delta_str} | {ci_str} | {p_val_str} | {sig_str} |")

    return "\n".join(lines)
