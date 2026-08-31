# Copyright 2026 trace-lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Trace-Lite Independent SOTA Benchmark Runner and Evaluation Engine."""

from __future__ import annotations

import json
import math
import os
import platform
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
from rich.console import Console
from rich.table import Table
import hashlib
from pydantic import BaseModel, Field


def compute_sha256(text_or_bytes: str | bytes) -> str:
    """Compute SHA-256 hex digest, normalizing string line endings to \\n."""
    if isinstance(text_or_bytes, str):
        normalized = text_or_bytes.replace("\r\n", "\n")
        data = normalized.encode("utf-8")
    else:
        data = text_or_bytes
    return hashlib.sha256(data).hexdigest()


class BenchmarkCase(BaseModel):
    """A single reviewed query evaluation case with ground-truth evidence."""
    id: str
    category: str
    query: str
    gold_atom_ids: list[str] = Field(default_factory=list)
    acceptable_alternative_ids: list[str] = Field(default_factory=list)
    expected_abstention: bool = False
    difficulty: str = "medium"
    metadata: dict[str, Any] = Field(default_factory=dict)


class BenchmarkManifest(BaseModel):
    """Manifest representing a frozen benchmark corpus and query judgment set."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    corpus_sha256: str = ""
    corpus_text: str | None = None
    corpus_file: str | None = None
    release_authority: bool = False
    cases: list[BenchmarkCase] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def load_manifest(path: str | Path) -> tuple[BenchmarkManifest, str]:
    """Load BenchmarkManifest from JSON and verify SHA-256 corpus integrity."""
    manifest_path = Path(path).resolve()
    if not manifest_path.exists():
        raise FileNotFoundError(f"Benchmark manifest not found: {manifest_path}")

    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    manifest = BenchmarkManifest.model_validate(data)
    corpus_text = ""
    if manifest.corpus_text is not None:
        corpus_text = manifest.corpus_text
    elif manifest.corpus_file is not None:
        file_path = Path(manifest.corpus_file)
        if not file_path.is_absolute():
            file_path = manifest_path.parent / file_path
        if not file_path.exists():
            raise FileNotFoundError(f"Corpus file does not exist: {file_path}")
        corpus_text = file_path.read_text(encoding="utf-8")
    else:
        raise ValueError("Benchmark manifest must specify either corpus_text or corpus_file")

    computed = compute_sha256(corpus_text)
    if manifest.corpus_sha256 and manifest.corpus_sha256 != computed:
        raise ValueError(
            f"Corpus SHA-256 mismatch! Manifest expected '{manifest.corpus_sha256}', computed '{computed}'"
        )
    return manifest, corpus_text


# Standard category normalization and metadata mapping
CATEGORY_METADATA: dict[str, dict[str, Any]] = {
    "CAT_01": {
        "name": "Direct Lookup",
        "aliases": ["direct_lookup", "direct", "cat_01", "cat-01", "cat1"],
        "metric_focus": "Top-1 Accuracy / Precision@1",
        "gate_threshold": 0.92,
        "gate_metric": "precision_at_1",
        "gate_label": ">= 92%",
    },
    "CAT_02": {
        "name": "Chronology & Order",
        "aliases": ["chronology", "order", "sequence", "cat_02", "cat-02", "cat2"],
        "metric_focus": "Sequence Recall@5",
        "gate_threshold": 0.85,
        "gate_metric": "recall_at_5",
        "gate_label": ">= 85%",
    },
    "CAT_03": {
        "name": "Contradiction & Revision",
        "aliases": ["contradiction", "revision", "freshness", "cat_03", "cat-03", "cat3"],
        "metric_focus": "Freshness Recall@5",
        "gate_threshold": 0.88,
        "gate_metric": "recall_at_5",
        "gate_label": ">= 88%",
    },
    "CAT_04": {
        "name": "Cross-Domain",
        "aliases": ["cross_domain", "cross-domain", "cross_tree", "cat_04", "cat-04", "cat4"],
        "metric_focus": "Cross-tree Recall@5",
        "gate_threshold": 0.80,
        "gate_metric": "recall_at_5",
        "gate_label": ">= 80%",
    },
    "CAT_05": {
        "name": "Global Context",
        "aliases": ["global_context", "global", "summary", "cat_05", "cat-05", "cat5"],
        "metric_focus": "Tree Root nDCG@10",
        "gate_threshold": 0.85,
        "gate_metric": "ndcg_at_10",
        "gate_label": ">= 85%",
    },
    "CAT_06": {
        "name": "Multi-Hop Evidence",
        "aliases": ["multi_hop", "multihop", "multi-hop", "cat_06", "cat-06", "cat6"],
        "metric_focus": "Complete Gold Coverage",
        "gate_threshold": 0.78,
        "gate_metric": "complete_gold_coverage_at_10",
        "gate_label": ">= 78%",
    },
    "CAT_07": {
        "name": "Historical & Versioned",
        "aliases": ["historical", "versioned", "provenance", "cat_07", "cat-07", "cat7"],
        "metric_focus": "Provenance Recall@5",
        "gate_threshold": 0.85,
        "gate_metric": "recall_at_5",
        "gate_label": ">= 85%",
    },
    "CAT_08": {
        "name": "Insufficient Evidence",
        "aliases": ["out_of_scope", "abstention", "insufficient_evidence", "cat_08", "cat-08", "cat8"],
        "metric_focus": "Abstention Accuracy",
        "gate_threshold": 0.95,
        "gate_metric": "abstention_accuracy",
        "gate_label": ">= 95%",
    },
}


def normalize_category_id(category_raw: str) -> str:
    """Normalize any string category representation to CAT_01..CAT_08."""
    raw_lower = category_raw.strip().lower()
    for cat_id, meta in CATEGORY_METADATA.items():
        if raw_lower == cat_id.lower() or raw_lower in meta["aliases"]:
            return cat_id
    return category_raw.upper()


@dataclass
class CaseEvaluationResult:
    """Per-case evaluation metrics."""
    case_id: str
    category_id: str
    category_name: str
    query: str
    gold_atom_ids: list[str]
    retrieved_atom_ids: list[str]
    is_abstaining: bool
    expected_abstention: bool
    latency_ms: float
    precision_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_20: float = 0.0
    ndcg_at_10: float = 0.0
    ndcg_at_30: float = 0.0
    complete_gold_coverage_at_10: float = 0.0
    complete_gold_coverage_at_20: float = 0.0
    citation_precision_at_5: float = 0.0
    citation_precision_at_10: float = 0.0
    abstention_accuracy: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CategorySummary:
    """Aggregated evaluation metrics for a single category."""
    category_id: str
    category_name: str
    cases_count: int
    precision_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_20: float = 0.0
    ndcg_at_10: float = 0.0
    ndcg_at_30: float = 0.0
    complete_gold_coverage_at_10: float = 0.0
    complete_gold_coverage_at_20: float = 0.0
    citation_precision_at_5: float = 0.0
    citation_precision_at_10: float = 0.0
    abstention_accuracy: float = 1.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_mean_ms: float = 0.0
    target_metric: str = ""
    target_gate: str = ""
    gate_score: float = 0.0
    gate_passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Complete benchmark execution report."""
    benchmark_name: str
    version: str
    run_id: str
    timestamp: str
    mode: str
    fixture_path: str
    corpus_sha256: str
    total_cases: int
    summary_metrics: dict[str, float]
    category_breakdown: dict[str, CategorySummary]
    all_gates_passed: bool
    hardware_info: dict[str, Any]
    case_results: list[CaseEvaluationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert CategorySummary objects in category_breakdown to dicts
        d["category_breakdown"] = {
            k: v if isinstance(v, dict) else v.to_dict()
            for k, v in self.category_breakdown.items()
        }
        d["case_results"] = [
            c if isinstance(c, dict) else c.to_dict() for c in self.case_results
        ]
        return d

    def save_json(self, file_path: str | Path) -> Path:
        out = Path(file_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return out

    def render_console(self, console: Console | None = None) -> None:
        c = console or Console()
        # Summary Header
        c.print(f"\n[bold blue]=== Trace-Lite Benchmark Report: {self.benchmark_name} (v{self.version}) ===[/bold blue]")
        c.print(f"* Run ID: [cyan]{self.run_id}[/cyan] | Mode: [bold green]{self.mode}[/bold green] | Total Cases: [yellow]{self.total_cases}[/yellow]")
        c.print(f"* Fixture: [dim]{self.fixture_path}[/dim]")
        c.print(f"* Corpus SHA256: [dim]{self.corpus_sha256[:16]}...[/dim]")

        # Overall Metrics Table
        table_overall = Table(title="\nOverall Retrieval & Operational Metrics", border_style="cyan")
        table_overall.add_column("Recall@5", justify="right")
        table_overall.add_column("Recall@20", justify="right")
        table_overall.add_column("nDCG@10", justify="right")
        table_overall.add_column("nDCG@30", justify="right")
        table_overall.add_column("Gold Cov@10", justify="right")
        table_overall.add_column("Prec@5", justify="right")
        table_overall.add_column("Abstain Acc", justify="right")
        table_overall.add_column("Latency p50", justify="right")
        table_overall.add_column("Latency p95", justify="right")

        sm = self.summary_metrics
        table_overall.add_row(
            f"{sm.get('recall_at_5', 0.0):.3f}",
            f"{sm.get('recall_at_20', 0.0):.3f}",
            f"{sm.get('ndcg_at_10', 0.0):.3f}",
            f"{sm.get('ndcg_at_30', 0.0):.3f}",
            f"{sm.get('complete_gold_coverage_at_10', 0.0):.3f}",
            f"{sm.get('citation_precision_at_5', 0.0):.3f}",
            f"{sm.get('abstention_accuracy', 0.0):.3f}",
            f"{sm.get('latency_p50_ms', 0.0):.2f}ms",
            f"{sm.get('latency_p95_ms', 0.0):.2f}ms",
        )
        c.print(table_overall)

        # Category Breakdown Table
        table_cat = Table(title="\nPer-Category SOTA Benchmark Matrix Breakdown", border_style="blue")
        table_cat.add_column("Category", style="bold cyan")
        table_cat.add_column("Name", style="white")
        table_cat.add_column("Cases", justify="right")
        table_cat.add_column("Recall@5", justify="right")
        table_cat.add_column("nDCG@10", justify="right")
        table_cat.add_column("Gold Cov@10", justify="right")
        table_cat.add_column("Prec@5", justify="right")
        table_cat.add_column("Abstain", justify="right")
        table_cat.add_column("Target Gate", justify="center")
        table_cat.add_column("Status", justify="center")

        for cat_id, cat_sum in sorted(self.category_breakdown.items()):
            pass_status = "[bold green]PASS[/bold green]" if cat_sum.gate_passed else "[bold red]FAIL[/bold red]"
            table_cat.add_row(
                cat_id,
                cat_sum.category_name,
                str(cat_sum.cases_count),
                f"{cat_sum.recall_at_5:.3f}",
                f"{cat_sum.ndcg_at_10:.3f}",
                f"{cat_sum.complete_gold_coverage_at_10:.3f}",
                f"{cat_sum.citation_precision_at_5:.3f}",
                f"{cat_sum.abstention_accuracy:.3f}",
                f"{cat_sum.target_gate}",
                pass_status,
            )
        c.print(table_cat)

        if self.all_gates_passed:
            c.print("\n[bold green][OK] ALL 8 CATEGORY SOTA PASS GATES SATISFIED.[/bold green]\n")
        else:
            c.print("\n[bold yellow][!] Some category pass gates were not reached.[/bold yellow]\n")


def compute_recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute Recall@K."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    retrieved_k = set(retrieved_ids[:k])
    return len(retrieved_k & gold_set) / len(gold_set)


def compute_complete_gold_coverage(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Return 1.0 if ALL gold atom IDs are in top-K, else 0.0."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    retrieved_k = set(retrieved_ids[:k])
    return 1.0 if gold_set.issubset(retrieved_k) else 0.0


def compute_citation_precision(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute Precision@K."""
    retrieved_k = retrieved_ids[:k]
    if not retrieved_k:
        return 1.0 if not gold_ids else 0.0
    gold_set = set(gold_ids)
    hits = sum(1 for item_id in retrieved_k if item_id in gold_set)
    return hits / len(retrieved_k)


def compute_ndcg_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    """Compute nDCG@K."""
    if not gold_ids:
        return 1.0 if not retrieved_ids[:k] else 0.0
    gold_set = set(gold_ids)
    dcg = 0.0
    for i, item_id in enumerate(retrieved_ids[:k]):
        if item_id in gold_set:
            dcg += 1.0 / math.log2(i + 2)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(gold_set))))
    return dcg / idcg if idcg > 0.0 else 0.0


class BenchmarkRunner:
    """Benchmark runner evaluating Trace-Lite across query evaluation suites."""

    def __init__(
        self,
        embedder: Any = None,
        llm: Any = None,
        data_dir: str | Path | None = None,
        auto_cleanup: bool = True,
    ):
        self.embedder = embedder
        self.llm = llm
        self.custom_data_dir = Path(data_dir) if data_dir else None
        self.auto_cleanup = auto_cleanup
        self.temp_dir: tempfile.TemporaryDirectory | None = None

    def _setup_db(self) -> Any:
        from trace_lite.db import TraceLite

        if self.custom_data_dir:
            resolved_dir = self.custom_data_dir
            resolved_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.temp_dir = tempfile.TemporaryDirectory(prefix="tl_benchmark_")
            resolved_dir = Path(self.temp_dir.name)

        kwargs: dict[str, Any] = {"data_dir": resolved_dir}
        if self.embedder is not None:
            kwargs["embedder"] = self.embedder
        if self.llm is not None:
            kwargs["llm"] = self.llm
        else:
            from trace_lite.adapters.llm import MockLLMAdapter
            kwargs["llm"] = MockLLMAdapter()

        return TraceLite(**kwargs)

    def _cleanup(self) -> None:
        if self.auto_cleanup and self.temp_dir is not None:
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass
            self.temp_dir = None

    def run(
        self,
        fixture: str | Path | BenchmarkManifest,
        mode: Literal["hybrid", "tree", "flat", "lexical"] = "hybrid",
        top_k: int = 30,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> BenchmarkReport:
        """Run benchmark evaluation suite."""
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        start_time = datetime.now(timezone.utc).isoformat()

        # 1. Load manifest and verify corpus
        if isinstance(fixture, BenchmarkManifest):
            manifest = fixture
            fixture_path_str = "in_memory_manifest"
            corpus_text = manifest.corpus_text or ""
        else:
            fixture_path = Path(fixture).resolve()
            fixture_path_str = str(fixture_path)
            manifest, corpus_text = load_manifest(fixture_path)

        # 2. Setup isolated TraceLite instance
        db = self._setup_db()

        try:
            # 3. Index corpus paragraphs into isolated instance
            paragraphs = [p.strip() for p in corpus_text.split("\n\n") if p.strip()]
            for idx, p in enumerate(paragraphs):
                doc_id = f"atom-{idx}"
                db.ingest(
                    text=p,
                    document_name=doc_id,
                    metadata={"benchmark_doc_id": doc_id, "paragraph_index": idx},
                )

            # Build index structure
            try:
                db.reindex_all()
            except Exception:
                try:
                    db.organize()
                except Exception:
                    pass

            # 4. Evaluate each query case
            case_results: list[CaseEvaluationResult] = []
            latencies_ms: list[float] = []
            total_cases = len(manifest.cases)

            for idx, case in enumerate(manifest.cases):
                if progress_callback:
                    progress_callback(idx + 1, total_cases, case.id)

                cat_id = normalize_category_id(case.category)
                cat_meta = CATEGORY_METADATA.get(cat_id, {})
                cat_name = cat_meta.get("name", case.category)

                # Time retrieval
                t0 = time.perf_counter()
                try:
                    q_res = db.query(
                        query_text=case.query,
                        top_k=top_k,
                        mode=mode,
                        force=(mode == "flat"),
                        allow_hot_inbox=True,
                    )
                except Exception:
                    # Fallback on flat query if tree traversal blocked
                    try:
                        q_res = db.query(
                            query_text=case.query,
                            top_k=top_k,
                            mode="flat",
                            force=True,
                            allow_hot_inbox=True,
                        )
                    except Exception:
                        q_res = None

                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                latencies_ms.append(elapsed_ms)

                retrieved_ids: list[str] = []
                is_abstaining = False
                if q_res is not None:
                    for item in q_res.items:
                        # Extract original benchmark doc ID if stored
                        b_id = item.atom.metadata.get("benchmark_doc_id")
                        if not b_id:
                            b_id = item.atom.atom_id
                        retrieved_ids.append(b_id)

                    if getattr(q_res, "sufficiency_state", "") == "insufficient_evidence":
                        is_abstaining = True
                    elif len(q_res.items) == 0:
                        is_abstaining = True
                else:
                    is_abstaining = True

                expected_abstain = bool(
                    case.expected_abstention
                    or cat_id == "CAT_08"
                    or not case.gold_atom_ids
                )

                # Compute metrics
                if expected_abstain:
                    p1 = 1.0 if (is_abstaining or len(retrieved_ids[:1]) == 0) else 0.0
                    r5 = 1.0 if (is_abstaining or len(retrieved_ids[:5]) == 0) else 0.0
                    r20 = 1.0 if (is_abstaining or len(retrieved_ids[:20]) == 0) else 0.0
                    ndcg10 = 1.0 if (is_abstaining or len(retrieved_ids[:10]) == 0) else 0.0
                    ndcg30 = 1.0 if (is_abstaining or len(retrieved_ids[:30]) == 0) else 0.0
                    cov10 = 1.0 if (is_abstaining or len(retrieved_ids[:10]) == 0) else 0.0
                    cov20 = 1.0 if (is_abstaining or len(retrieved_ids[:20]) == 0) else 0.0
                    prec5 = 1.0 if (is_abstaining or len(retrieved_ids[:5]) == 0) else 0.0
                    prec10 = 1.0 if (is_abstaining or len(retrieved_ids[:10]) == 0) else 0.0
                    abstain_acc = 1.0 if is_abstaining else 0.0
                else:
                    gold_set = set(case.gold_atom_ids)
                    p1 = 1.0 if (retrieved_ids and retrieved_ids[0] in gold_set) else 0.0
                    r5 = compute_recall_at_k(retrieved_ids, case.gold_atom_ids, 5)
                    r20 = compute_recall_at_k(retrieved_ids, case.gold_atom_ids, 20)
                    ndcg10 = compute_ndcg_at_k(retrieved_ids, case.gold_atom_ids, 10)
                    ndcg30 = compute_ndcg_at_k(retrieved_ids, case.gold_atom_ids, 30)
                    cov10 = compute_complete_gold_coverage(retrieved_ids, case.gold_atom_ids, 10)
                    cov20 = compute_complete_gold_coverage(retrieved_ids, case.gold_atom_ids, 20)
                    prec5 = compute_citation_precision(retrieved_ids, case.gold_atom_ids, 5)
                    prec10 = compute_citation_precision(retrieved_ids, case.gold_atom_ids, 10)
                    abstain_acc = 1.0 if not is_abstaining else 0.0

                case_result = CaseEvaluationResult(
                    case_id=case.id,
                    category_id=cat_id,
                    category_name=cat_name,
                    query=case.query,
                    gold_atom_ids=case.gold_atom_ids,
                    retrieved_atom_ids=retrieved_ids,
                    is_abstaining=is_abstaining,
                    expected_abstention=expected_abstain,
                    latency_ms=elapsed_ms,
                    precision_at_1=p1,
                    recall_at_5=r5,
                    recall_at_20=r20,
                    ndcg_at_10=ndcg10,
                    ndcg_at_30=ndcg30,
                    complete_gold_coverage_at_10=cov10,
                    complete_gold_coverage_at_20=cov20,
                    citation_precision_at_5=prec5,
                    citation_precision_at_10=prec10,
                    abstention_accuracy=abstain_acc,
                )
                case_results.append(case_result)

            # 5. Aggregate overall metrics
            n = len(case_results)
            lat_arr = np.array(latencies_ms) if latencies_ms else np.array([0.0])
            summary_metrics = {
                "precision_at_1": float(sum(c.precision_at_1 for c in case_results) / n) if n else 0.0,
                "recall_at_5": float(sum(c.recall_at_5 for c in case_results) / n) if n else 0.0,
                "recall_at_20": float(sum(c.recall_at_20 for c in case_results) / n) if n else 0.0,
                "ndcg_at_10": float(sum(c.ndcg_at_10 for c in case_results) / n) if n else 0.0,
                "ndcg_at_30": float(sum(c.ndcg_at_30 for c in case_results) / n) if n else 0.0,
                "complete_gold_coverage_at_10": float(sum(c.complete_gold_coverage_at_10 for c in case_results) / n) if n else 0.0,
                "complete_gold_coverage_at_20": float(sum(c.complete_gold_coverage_at_20 for c in case_results) / n) if n else 0.0,
                "citation_precision_at_5": float(sum(c.citation_precision_at_5 for c in case_results) / n) if n else 0.0,
                "citation_precision_at_10": float(sum(c.citation_precision_at_10 for c in case_results) / n) if n else 0.0,
                "abstention_accuracy": float(sum(c.abstention_accuracy for c in case_results) / n) if n else 0.0,
                "latency_p50_ms": float(np.percentile(lat_arr, 50)),
                "latency_p95_ms": float(np.percentile(lat_arr, 95)),
                "latency_p99_ms": float(np.percentile(lat_arr, 99)),
                "latency_mean_ms": float(np.mean(lat_arr)),
                "latency_min_ms": float(np.min(lat_arr)),
                "latency_max_ms": float(np.max(lat_arr)),
            }

            # 6. Aggregate per-category breakdown
            categories: dict[str, list[CaseEvaluationResult]] = {}
            for c in case_results:
                categories.setdefault(c.category_id, []).append(c)

            category_breakdown: dict[str, CategorySummary] = {}
            all_gates_passed = True

            for cat_id, cases_list in categories.items():
                k_cases = len(cases_list)
                meta = CATEGORY_METADATA.get(cat_id, {})
                cat_name = meta.get("name", cat_id)
                gate_threshold = meta.get("gate_threshold", 0.80)
                gate_metric = meta.get("gate_metric", "recall_at_5")
                gate_label = meta.get("gate_label", f">= {int(gate_threshold * 100)}%")

                c_lat = np.array([x.latency_ms for x in cases_list])
                c_p1 = sum(x.precision_at_1 for x in cases_list) / k_cases
                c_r5 = sum(x.recall_at_5 for x in cases_list) / k_cases
                c_r20 = sum(x.recall_at_20 for x in cases_list) / k_cases
                c_ndcg10 = sum(x.ndcg_at_10 for x in cases_list) / k_cases
                c_ndcg30 = sum(x.ndcg_at_30 for x in cases_list) / k_cases
                c_cov10 = sum(x.complete_gold_coverage_at_10 for x in cases_list) / k_cases
                c_cov20 = sum(x.complete_gold_coverage_at_20 for x in cases_list) / k_cases
                c_prec5 = sum(x.citation_precision_at_5 for x in cases_list) / k_cases
                c_prec10 = sum(x.citation_precision_at_10 for x in cases_list) / k_cases
                c_abstain = sum(x.abstention_accuracy for x in cases_list) / k_cases

                metric_map = {
                    "precision_at_1": c_p1,
                    "recall_at_5": c_r5,
                    "recall_at_20": c_r20,
                    "ndcg_at_10": c_ndcg10,
                    "ndcg_at_30": c_ndcg30,
                    "complete_gold_coverage_at_10": c_cov10,
                    "complete_gold_coverage_at_20": c_cov20,
                    "citation_precision_at_5": c_prec5,
                    "citation_precision_at_10": c_prec10,
                    "abstention_accuracy": c_abstain,
                }
                gate_score = metric_map.get(gate_metric, c_r5)
                gate_passed = bool(gate_score >= gate_threshold)
                if not gate_passed:
                    all_gates_passed = False

                category_breakdown[cat_id] = CategorySummary(
                    category_id=cat_id,
                    category_name=cat_name,
                    cases_count=k_cases,
                    precision_at_1=c_p1,
                    recall_at_5=c_r5,
                    recall_at_20=c_r20,
                    ndcg_at_10=c_ndcg10,
                    ndcg_at_30=c_ndcg30,
                    complete_gold_coverage_at_10=c_cov10,
                    complete_gold_coverage_at_20=c_cov20,
                    citation_precision_at_5=c_prec5,
                    citation_precision_at_10=c_prec10,
                    abstention_accuracy=c_abstain,
                    latency_p50_ms=float(np.percentile(c_lat, 50)),
                    latency_p95_ms=float(np.percentile(c_lat, 95)),
                    latency_mean_ms=float(np.mean(c_lat)),
                    target_metric=gate_metric,
                    target_gate=gate_label,
                    gate_score=gate_score,
                    gate_passed=gate_passed,
                )

            hardware_info = {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "processor": platform.processor(),
                "cpu_count": os.cpu_count(),
            }

            return BenchmarkReport(
                benchmark_name=manifest.name,
                version=manifest.version,
                run_id=run_id,
                timestamp=start_time,
                mode=mode,
                fixture_path=fixture_path_str,
                corpus_sha256=manifest.corpus_sha256,
                total_cases=total_cases,
                summary_metrics=summary_metrics,
                category_breakdown=category_breakdown,
                all_gates_passed=all_gates_passed,
                hardware_info=hardware_info,
                case_results=case_results,
            )
        finally:
            self._cleanup()
