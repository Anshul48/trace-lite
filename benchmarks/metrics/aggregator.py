"""Benchmark result aggregation, category slicing, and comparison reporting."""

import json
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


def generate_comparison_markdown(current: BenchmarkRunResult, baseline: BenchmarkRunResult) -> str:
    """Generate markdown report comparing two benchmark runs."""
    lines = [
        f"# Benchmark Comparison: {current.run_id} vs {baseline.run_id}",
        "",
        f"- **Dataset**: {current.dataset_name} (v{current.dataset_version})",
        f"- **Cases**: {current.total_cases}",
        f"- **Current Date**: {current.timestamp}",
        f"- **Baseline Date**: {baseline.timestamp}",
        "",
        "## Overall Metrics Comparison",
        "",
        "| Baseline | Metric | Baseline Run | Current Run | Delta |",
        "|---|---|:---:|:---:|:---:|",
    ]

    all_keys = set(current.baselines.keys()) | set(baseline.baselines.keys())
    for b_key in sorted(all_keys):
        c_base = current.baselines.get(b_key)
        b_base = baseline.baselines.get(b_key)
        if not c_base or not b_base:
            continue

        metrics = [
            ("Recall@5", _get_val(b_base, "overall_recall_at_5"), _get_val(c_base, "overall_recall_at_5")),
            ("Recall@20", _get_val(b_base, "overall_recall_at_20"), _get_val(c_base, "overall_recall_at_20")),
            ("nDCG@10", _get_val(b_base, "overall_ndcg_at_10"), _get_val(c_base, "overall_ndcg_at_10")),
            ("Complete Coverage@10", _get_val(b_base, "overall_complete_coverage_at_10"), _get_val(c_base, "overall_complete_coverage_at_10")),
            ("Citation Precision@5", _get_val(b_base, "overall_citation_precision_at_5"), _get_val(c_base, "overall_citation_precision_at_5")),
            ("MRR", _get_val(b_base, "overall_mrr"), _get_val(c_base, "overall_mrr")),
            ("Abstention Accuracy", _get_val(b_base, "overall_abstention_accuracy"), _get_val(c_base, "overall_abstention_accuracy")),
        ]

        for m_name, b_val, c_val in metrics:
            delta = c_val - b_val
            delta_str = f"+{delta:.3f}" if delta > 0 else f"{delta:.3f}"
            lines.append(f"| `{b_key}` | {m_name} | {b_val:.3f} | {c_val:.3f} | {delta_str} |")

    return "\n".join(lines)

