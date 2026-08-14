"""Command-line interface and batch execution engine for the trace-lite benchmark suite."""

import concurrent.futures
import csv
import json
import os
import platform
import sys
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
from benchmarks.baselines.hipporag_retriever import HippoRagPPRRetriever
from benchmarks.metrics.retrieval import evaluate_case_retrieval, CaseRetrievalMetrics
from benchmarks.metrics.organization import OrganizationMetrics, source_atom_coverage
from benchmarks.metrics.operations import LatencyTracker, MemoryTracker, StorageTracker, OpsProfile
from benchmarks.metrics.aggregator import (
    BenchmarkAggregator,
    BenchmarkRunResult,
    generate_comparison_markdown,
    check_release_gates,
)
from benchmarks.generators.scale_corpus import generate_scale_benchmark, ScaleTier

console = Console()

AVAILABLE_BASELINES: dict[str, type[BaseRetriever] | tuple[type[BaseRetriever], dict]] = {
    "bm25": BM25Retriever,
    "dense": (DenseRetriever, {"model_name": "all-MiniLM-L6-v2"}),
    "dense_bge": (DenseRetriever, {"model_name": "BAAI/bge-large-en-v1.5"}),
    "hybrid_rrf": (HybridRRFRetriever, {"dense_model": "all-MiniLM-L6-v2"}),
    "flat_hierarchy": (FlatHierarchyRetriever, {"dense_model": "all-MiniLM-L6-v2"}),
    "trace_flat": (TraceLiteRetriever, {"mode": "flat", "mock_llm": True}),
    "trace_tree": (TraceLiteRetriever, {"mode": "tree", "mock_llm": True}),
    "trace_hybrid": (TraceLiteRetriever, {"mode": "hybrid", "mock_llm": True}),
    "hipporag_ppr": HippoRagPPRRetriever,
}


def instantiate_baseline(name: str, mock_llm: bool = True) -> BaseRetriever:
    if name not in AVAILABLE_BASELINES:
        raise ValueError(f"Unknown baseline '{name}'. Available: {list(AVAILABLE_BASELINES.keys())}")
    spec = AVAILABLE_BASELINES[name]
    if isinstance(spec, tuple):
        cls, kwargs = spec
        kwargs_copy = dict(kwargs)
        if issubclass(cls, TraceLiteRetriever):
            kwargs_copy["mock_llm"] = mock_llm
        return cls(**kwargs_copy)
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


def execute_single_case(retriever: BaseRetriever, case: BenchmarkCase, top_k: int) -> tuple[CaseRetrievalMetrics, float]:
    t0 = time.perf_counter()
    candidates = retriever.retrieve(case.query, top_k=top_k)
    duration = time.perf_counter() - t0

    retrieved_ids = [c.doc_id for c in candidates]
    is_abstaining = (len(candidates) == 0)

    metric = evaluate_case_retrieval(
        case_id=case.id,
        category=case.category,
        retrieved_ids=retrieved_ids,
        gold_ids=case.gold_atom_ids,
        is_abstaining=is_abstaining,
        expected_abstention=case.expected_abstention,
    )
    return metric, duration


def execute_benchmark(
    manifest: BenchmarkManifest,
    corpus_text: str,
    baseline_names: list[str],
    top_k: int = 10,
    concurrency: int = 1,
    mock_llm: bool = True,
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
        release_authority=manifest.release_authority,
        hardware_info=get_hardware_info(),
    )

    for b_name in baseline_names:
        console.print(f"[bold cyan]Evaluating baseline:[/bold cyan] {b_name} (concurrency={concurrency}) ...")
        retriever = instantiate_baseline(b_name, mock_llm=mock_llm)

        # 1. Indexing & Ingestion
        t0 = time.perf_counter()
        mem_before = MemoryTracker.get_current_rss_mb()
        retriever.index(documents)
        index_duration = time.perf_counter() - t0
        mem_after = MemoryTracker.get_current_rss_mb()

        # 2. Query Evaluation & Latency Tracking
        latency_tracker = LatencyTracker()
        case_metrics: list[CaseRetrievalMetrics] = []

        q_wall_start = time.perf_counter()
        if concurrency > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [
                    executor.submit(execute_single_case, retriever, case, top_k)
                    for case in manifest.cases
                ]
                for fut in concurrent.futures.as_completed(futures):
                    metric, duration = fut.result()
                    latency_tracker.record(duration)
                    case_metrics.append(metric)
        else:
            for case in manifest.cases:
                metric, duration = execute_single_case(retriever, case, top_k)
                latency_tracker.record(duration)
                case_metrics.append(metric)

        q_wall_total = time.perf_counter() - q_wall_start
        concurrent_qps = len(manifest.cases) / q_wall_total if q_wall_total > 0 else 0.0

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
        ops_profile_dict = ops_profile.__dict__
        ops_profile_dict["concurrent_qps"] = concurrent_qps

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
        base_res.ops["concurrent_qps"] = concurrent_qps
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
    table.add_column("QPS", justify="right")

    for b_name, res in run_result.baselines.items():
        p95_str = f"{res.ops.get('latency_p95_ms', 0.0):.1f}ms"
        qps_str = f"{res.ops.get('concurrent_qps', 0.0):.1f}"
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
            qps_str,
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
@click.option("--concurrency", "-c", default=1, type=int, help="Concurrent worker threads.")
@click.option("--mock-llm/--real-llm", default=True, help="Use deterministic mock LLM for offline tree traversal.")
@click.option("--output-dir", "-o", default="benchmarks/results", help="Directory to store JSON/CSV run logs.")
@click.option("--format", "-f", "out_format", default="all", type=click.Choice(["json", "csv", "all"]), help="Output artifact format.")
@click.option("--compare-to", default=None, help="Optional baseline run JSON to compare against.")
@click.option("--assert-gate", is_flag=True, default=False, help="Fail with code 1 if release gate standards are not met.")
def run_command(
    dataset: str,
    baselines: str,
    top_k: int,
    concurrency: int,
    mock_llm: bool,
    output_dir: str,
    out_format: str,
    compare_to: str | None,
    assert_gate: bool,
):
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
        concurrency=concurrency,
        mock_llm=mock_llm,
    )

    render_summary_table(run_result)

    out_json = Path(output_dir) / f"{run_result.run_id}.json"
    out_csv = Path(output_dir) / f"{run_result.run_id}.csv"

    if out_format in ("json", "all"):
        run_result.save_json(out_json)
        console.print(f"[green]Saved JSON artifact to:[/green] {out_json}")

    if out_format in ("csv", "all"):
        run_result.save_csv(out_csv)
        console.print(f"[green]Saved CSV artifact to:[/green] {out_csv}")

    if compare_to:
        comp_path = Path(compare_to)
        if comp_path.exists():
            with open(comp_path, "r", encoding="utf-8") as f:
                b_data = json.load(f)
            base_run = BenchmarkRunResult.from_dict(b_data)
            diff_md = generate_comparison_markdown(run_result, base_run)
            console.print("\n" + diff_md)

    if assert_gate:
        console.print("\n[bold yellow]Asserting Production Release Gates...[/bold yellow]")
        all_passed = True
        for b_name, b_res in run_result.baselines.items():
            passed, failures = check_release_gates(b_res, release_authority=manifest.release_authority)
            if passed:
                console.print(f"[bold green][PASS] {b_name}: PASSED ALL RELEASE GATES[/bold green]")
            else:
                console.print(f"[bold red][FAIL] {b_name}: FAILED RELEASE GATES[/bold red]")
                for fail_msg in failures:
                    console.print(f"  - [red]{fail_msg}[/red]")
                all_passed = False

        if not all_passed:
            console.print("[bold red]Release Gate Assertion Failed![/bold red]")
            sys.exit(1)


@cli.command("scale")
@click.option("--tier", "-t", default="1k", type=click.Choice(["1k", "10k", "100k", "1m"]), help="Scale ladder tier.")
@click.option(
    "--baselines",
    "-b",
    default="bm25,dense,hybrid_rrf",
    help="Comma-separated baselines to evaluate.",
)
@click.option("--concurrency", "-c", default=1, type=int, help="Concurrent worker threads.")
@click.option("--seed", "-s", default=42, type=int, help="Deterministic generator random seed.")
@click.option("--output-dir", "-o", default="benchmarks/results", help="Directory to store JSON logs.")
def scale_command(tier: ScaleTier, baselines: str, concurrency: int, seed: int, output_dir: str):
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
        concurrency=concurrency,
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

    c_run = BenchmarkRunResult.from_dict(c_data)
    b_run = BenchmarkRunResult.from_dict(b_data)

    diff_md = generate_comparison_markdown(c_run, b_run)
    console.print(diff_md)

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(diff_md, encoding="utf-8")
        console.print(f"[green]Saved comparison report to:[/green] {output}")


@cli.command("setup")
@click.option(
    "--dataset",
    "-d",
    required=True,
    help="Dataset to prepare (e.g. scifact, nfcorpus, fiqa, bright-coding, multihop, hichunk, longmemeval, hipporag-sample, or all).",
)
@click.option("--cache-dir", default="benchmarks/cache", help="Local directory for dataset cache.")
def setup_command(dataset: str, cache_dir: str):
    """Download and prepare public benchmark datasets in local offline cache."""
    from benchmarks.adapters.beir_adapter import BeirAdapter
    from benchmarks.adapters.bright_adapter import BrightAdapter
    from benchmarks.adapters.multihop_adapter import MultiHopAdapter
    from benchmarks.adapters.hichunk_adapter import HiChunkAdapter
    from benchmarks.adapters.longmemeval_adapter import LongMemEvalAdapter
    from benchmarks.adapters.hipporag_adapter import HippoRagAdapter
    from benchmarks.adapters.trec_rag_adapter import TrecRagAdapter
    from benchmarks.adapters.mteb_adapter import MtebAdapter
    from benchmarks.adapters.ann_scale_adapter import AnnScaleAdapter

    targets = []
    d_clean = dataset.strip().lower()
    if d_clean == "all":
        targets = ["scifact", "nfcorpus", "fiqa", "multihop", "bright-coding", "hichunk", "longmemeval", "hipporag-sample", "trec-rag"]
    else:
        targets = [d_clean]

    for tgt in targets:
        console.print(f"[bold cyan]Setting up curated benchmark dataset:[/bold cyan] {tgt} ...")
        adapter = None
        if tgt in ("scifact", "nfcorpus", "fiqa", "arguana", "beir-scifact", "beir-nfcorpus", "beir-fiqa"):
            name = tgt.replace("beir-", "")
            adapter = BeirAdapter(dataset=name, cache_dir=cache_dir)
        elif "bright" in tgt:
            task = tgt.replace("bright-", "").replace("bright_", "")
            adapter = BrightAdapter(task=task, cache_dir=cache_dir)
        elif "multihop" in tgt:
            adapter = MultiHopAdapter(dataset_name="multihop_rag", cache_dir=cache_dir)
        elif "hichunk" in tgt:
            adapter = HiChunkAdapter(cache_dir=cache_dir)
        elif "longmem" in tgt:
            adapter = LongMemEvalAdapter(cache_dir=cache_dir)
        elif "hipporag" in tgt:
            task = tgt.replace("hipporag-", "")
            adapter = HippoRagAdapter(task=task, cache_dir=cache_dir)
        elif "trec" in tgt:
            adapter = TrecRagAdapter(cache_dir=cache_dir)
        elif "mteb" in tgt:
            adapter = MtebAdapter(cache_dir=cache_dir)
        elif "ann" in tgt:
            adapter = AnnScaleAdapter(cache_dir=cache_dir)
        else:
            adapter = BeirAdapter(dataset=tgt, cache_dir=cache_dir)

        adapter.download_or_prepare()
        manifest, corpus_text = adapter.export_manifest()
        console.print(
            f"[bold green][OK] Successfully prepared {adapter.dataset_name()}:[/bold green] "
            f"{len(manifest.cases)} queries, {manifest.metadata.get('total_docs', 0)} documents."
        )



if __name__ == "__main__":
    cli()
