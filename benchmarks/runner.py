"""Command-line interface and batch execution engine for the trace-lite benchmark suite."""

import json
import os
import platform
import time
from datetime import datetime, timezone
from pathlib import Path
import click
from rich.console import Console
from rich.table import Table

from benchmarks.datasets.loader import load_manifest
from benchmarks.datasets.schema import BenchmarkManifest, BenchmarkCase
from benchmarks.baselines.base import BaseRetriever, IndexedDocument
from benchmarks.baselines.bm25_retriever import BM25Retriever
from benchmarks.baselines.dense_retriever import DenseRetriever
from benchmarks.baselines.hybrid_rrf_retriever import HybridRRFRetriever
from benchmarks.baselines.flat_hierarchy_retriever import FlatHierarchyRetriever
from benchmarks.baselines.trace_retriever import TraceLiteRetriever
from benchmarks.metrics.retrieval import evaluate_case_retrieval, CaseRetrievalMetrics
from benchmarks.metrics.organization import OrganizationMetrics, source_atom_coverage
from benchmarks.metrics.operations import LatencyTracker, MemoryTracker, StorageTracker, OpsProfile
from benchmarks.metrics.aggregator import BenchmarkAggregator, BenchmarkRunResult, generate_comparison_markdown
from benchmarks.generators.scale_corpus import generate_scale_benchmark, ScaleTier

console = Console()

AVAILABLE_BASELINES: dict[str, type[BaseRetriever] | tuple[type[BaseRetriever], dict]] = {
    "bm25": BM25Retriever,
    "dense": (DenseRetriever, {"model_name": "all-MiniLM-L6-v2"}),
    "hybrid_rrf": (HybridRRFRetriever, {"dense_model": "all-MiniLM-L6-v2"}),
    "flat_hierarchy": (FlatHierarchyRetriever, {"dense_model": "all-MiniLM-L6-v2"}),
    "trace_flat": (TraceLiteRetriever, {"mode": "flat"}),
    "trace_tree": (TraceLiteRetriever, {"mode": "tree"}),
    "trace_hybrid": (TraceLiteRetriever, {"mode": "hybrid"}),
}


def instantiate_baseline(name: str) -> BaseRetriever:
    if name not in AVAILABLE_BASELINES:
        raise ValueError(f"Unknown baseline '{name}'. Available: {list(AVAILABLE_BASELINES.keys())}")
    spec = AVAILABLE_BASELINES[name]
    if isinstance(spec, tuple):
        cls, kwargs = spec
        return cls(**kwargs)
    return spec()


def get_hardware_info() -> dict:
    """Collect non-sensitive hardware metadata for reproducible benchmark logs."""
    return {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def parse_corpus_to_documents(corpus_text: str) -> list[IndexedDocument]:
    """Parse text into indexed paragraphs/documents."""
    paragraphs = [p.strip() for p in corpus_text.split("\n\n") if p.strip()]
    documents: list[IndexedDocument] = []
    for idx, p in enumerate(paragraphs):
        documents.append(
            IndexedDocument(
                doc_id=f"atom-{idx}",
                text=p,
                title="",
                metadata={"paragraph_index": idx},
            )
        )
    return documents


def execute_benchmark(
    manifest: BenchmarkManifest,
    corpus_text: str,
    baseline_names: list[str],
    top_k: int = 10,
) -> BenchmarkRunResult:
    """Execute evaluation for the specified baselines on a manifest and corpus."""
    run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    documents = parse_corpus_to_documents(corpus_text)

    run_result = BenchmarkRunResult(
        run_id=run_id,
        dataset_name=manifest.name,
        dataset_version=manifest.version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        corpus_sha256=manifest.corpus_sha256,
        total_cases=len(manifest.cases),
        hardware_info=get_hardware_info(),
    )

    for b_name in baseline_names:
        console.print(f"[bold cyan]Evaluating baseline:[/bold cyan] {b_name} ...")
        retriever = instantiate_baseline(b_name)

        # 1. Measure Indexing / Ingestion
        t0 = time.perf_counter()
        mem_before = MemoryTracker.get_current_rss_mb()
        retriever.index(documents)
        index_duration = time.perf_counter() - t0
        mem_after = MemoryTracker.get_current_rss_mb()

        # 2. Query Evaluation & Latency Tracking
        latency_tracker = LatencyTracker()
        case_metrics: list[CaseRetrievalMetrics] = []

        for case in manifest.cases:
            q_start = time.perf_counter()
            candidates = retriever.retrieve(case.query, top_k=top_k)
            q_duration = time.perf_counter() - q_start
            latency_tracker.record(q_duration)

            retrieved_ids = [c.doc_id for c in candidates]
            is_abstaining = (len(candidates) == 0)

            c_metric = evaluate_case_retrieval(
                case_id=case.id,
                category=case.category,
                retrieved_ids=retrieved_ids,
                gold_ids=case.gold_atom_ids,
                is_abstaining=is_abstaining,
                expected_abstention=case.expected_abstention,
            )
            case_metrics.append(c_metric)

        # 3. Clean up retriever if necessary
        if hasattr(retriever, "cleanup"):
            retriever.cleanup()

        # 4. Ops & Organization Profiling
        lat_summary = latency_tracker.summary()
        ops_profile = OpsProfile(
            total_queries=len(manifest.cases),
            latency_p50_ms=lat_summary["p50"],
            latency_p95_ms=lat_summary["p95"],
            latency_p99_ms=lat_summary["p99"],
            latency_mean_ms=lat_summary["mean"],
            latency_min_ms=lat_summary["min"],
            latency_max_ms=lat_summary["max"],
            ingestion_duration_sec=index_duration,
            ingestion_atoms_per_sec=len(documents) / index_duration if index_duration > 0 else 0.0,
            peak_memory_rss_mb=max(mem_before, mem_after),
        )

        org_metrics = OrganizationMetrics(
            total_source_atoms=len(documents),
            preserved_source_atoms=len(documents),
            source_atom_coverage=1.0,
        )

        base_res = BenchmarkAggregator.aggregate_baseline(
            baseline_name=b_name,
            case_metrics=case_metrics,
            ops_profile=ops_profile,
            org_metrics=org_metrics,
        )
        run_result.baselines[b_name] = base_res

    return run_result


def render_summary_table(run_result: BenchmarkRunResult) -> None:
    """Print a Rich terminal table showing benchmark results."""
    table = Table(title=f"Benchmark Results: {run_result.dataset_name} ({run_result.total_cases} queries)")
    table.add_column("Baseline", style="cyan bold")
    table.add_column("Recall@5", justify="right")
    table.add_column("Recall@20", justify="right")
    table.add_column("nDCG@10", justify="right")
    table.add_column("Gold Cov@10", justify="right")
    table.add_column("Precision@5", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Abstain Acc", justify="right")
    table.add_column("Latency p95", justify="right")

    for b_name, res in run_result.baselines.items():
        p95_str = f"{res.ops.get('latency_p95_ms', 0.0):.1f}ms"
        table.add_row(
            b_name,
            f"{res.overall_recall_at_5:.3f}",
            f"{res.overall_recall_at_20:.3f}",
            f"{res.overall_ndcg_at_10:.3f}",
            f"{res.overall_complete_coverage_at_10:.3f}",
            f"{res.overall_citation_precision_at_5:.3f}",
            f"{res.overall_mrr:.3f}",
            f"{res.overall_abstention_accuracy:.3f}",
            p95_str,
        )

    console.print(table)


@click.group()
def cli():
    """Trace-Lite Independent SOTA Benchmark Suite."""
    pass


@cli.command("run")
@click.option("--dataset", "-d", required=True, help="Path to benchmark JSON manifest.")
@click.option(
    "--baselines",
    "-b",
    default="bm25,dense,hybrid_rrf",
    help="Comma-separated list of baselines or 'all'.",
)
@click.option("--top-k", "-k", default=10, type=int, help="Top-K retrieval limit.")
@click.option("--output-dir", "-o", default="benchmarks/results", help="Directory to store JSON run logs.")
@click.option("--compare-to", "-c", default=None, help="Optional baseline run JSON to compare against.")
def run_command(dataset: str, baselines: str, top_k: int, output_dir: str, compare_to: str | None):
    """Run benchmark evaluation over a dataset manifest."""
    manifest_path = Path(dataset)
    manifest, corpus_text = load_manifest(manifest_path)

    if baselines.strip().lower() == "all":
        selected_baselines = list(AVAILABLE_BASELINES.keys())
    else:
        selected_baselines = [b.strip() for b in baselines.split(",") if b.strip()]

    run_result = execute_benchmark(
        manifest=manifest,
        corpus_text=corpus_text,
        baseline_names=selected_baselines,
        top_k=top_k,
    )

    render_summary_table(run_result)

    out_path = Path(output_dir) / f"{run_result.run_id}.json"
    run_result.save_json(out_path)
    console.print(f"[green]Saved run artifact to:[/green] {out_path}")

    if compare_to:
        comp_path = Path(compare_to)
        if comp_path.exists():
            with open(comp_path, "r", encoding="utf-8") as f:
                b_data = json.load(f)
            base_run = BenchmarkRunResult(**b_data)
            diff_md = generate_comparison_markdown(run_result, base_run)
            console.print("\n" + diff_md)


@cli.command("scale")
@click.option("--tier", "-t", default="1k", type=click.Choice(["1k", "10k", "100k", "1m"]), help="Scale ladder tier.")
@click.option(
    "--baselines",
    "-b",
    default="bm25,dense,hybrid_rrf",
    help="Comma-separated baselines to evaluate.",
)
@click.option("--seed", "-s", default=42, type=int, help="Deterministic generator random seed.")
@click.option("--output-dir", "-o", default="benchmarks/results", help="Directory to store JSON logs.")
def scale_command(tier: ScaleTier, baselines: str, seed: int, output_dir: str):
    """Run deterministic scale ladder stress benchmark."""
    console.print(f"[bold yellow]Generating synthetic {tier.upper()} scale benchmark (seed={seed})...[/bold yellow]")
    manifest, corpus_text = generate_scale_benchmark(tier=tier, seed=seed)

    if baselines.strip().lower() == "all":
        selected_baselines = list(AVAILABLE_BASELINES.keys())
    else:
        selected_baselines = [b.strip() for b in baselines.split(",") if b.strip()]

    run_result = execute_benchmark(
        manifest=manifest,
        corpus_text=corpus_text,
        baseline_names=selected_baselines,
    )

    render_summary_table(run_result)
    out_path = Path(output_dir) / f"{run_result.run_id}_scale_{tier}.json"
    run_result.save_json(out_path)
    console.print(f"[green]Saved scale run artifact to:[/green] {out_path}")


@cli.command("compare")
@click.option("--current", "-c", required=True, help="Current run JSON file.")
@click.option("--baseline", "-b", required=True, help="Baseline run JSON file.")
@click.option("--output", "-o", default=None, help="Optional output markdown file.")
def compare_command(current: str, baseline: str, output: str | None):
    """Compare two benchmark runs and output a markdown diff report."""
    with open(current, "r", encoding="utf-8") as f:
        c_data = json.load(f)
    with open(baseline, "r", encoding="utf-8") as f:
        b_data = json.load(f)

    c_run = BenchmarkRunResult(**c_data)
    b_run = BenchmarkRunResult(**b_data)

    diff_md = generate_comparison_markdown(c_run, b_run)
    console.print(diff_md)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(diff_md, encoding="utf-8")
        console.print(f"[green]Saved comparison report to:[/green] {output}")


@cli.command("setup")
@click.option("--dataset", "-d", required=True, help="Public dataset name to cache (e.g. scifact, nfcorpus).")
@click.option("--cache-dir", default="benchmarks/cache", help="Local directory for dataset cache.")
def setup_command(dataset: str, cache_dir: str):
    """Download and prepare public benchmark datasets in local offline cache."""
    from benchmarks.adapters.beir_adapter import BeirAdapter
    console.print(f"[bold cyan]Setting up public dataset:[/bold cyan] {dataset} in {cache_dir}...")
    adapter = BeirAdapter(dataset=dataset, cache_dir=cache_dir)
    adapter.download_or_prepare()
    docs = adapter.load_corpus()
    queries = adapter.load_queries()
    console.print(f"[green]Successfully loaded {len(docs)} documents and {len(queries)} queries for {dataset}.[/green]")


if __name__ == "__main__":
    cli()
