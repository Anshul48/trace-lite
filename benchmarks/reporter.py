"""Standalone Interactive HTML Benchmark Report Generator with inline SVG charts."""

import html
import json
import math
from pathlib import Path
from benchmarks.metrics.aggregator import BenchmarkRunResult, BaselineEvaluationResult, compute_paired_statistics


def _svg_radar_chart(categories: list[str], baseline_scores: dict[str, list[float]], size: int = 400) -> str:
    """Generate an inline SVG radar / spider chart for category recall/coverage."""
    if not categories:
        return ""

    cx, cy = size / 2, size / 2
    radius = size * 0.38
    num_axes = len(categories)
    angle_slice = (math.pi * 2) / num_axes

    # Colors for baselines
    colors = ["#38bdf8", "#4ade80", "#f43f5e", "#fbbf24", "#a855f7", "#ec4899", "#64748b"]

    svg_elements = []

    # Background grid circles (0.2, 0.4, 0.6, 0.8, 1.0)
    for level in [0.2, 0.4, 0.6, 0.8, 1.0]:
        r = radius * level
        pts = []
        for i in range(num_axes):
            angle = i * angle_slice - math.pi / 2
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            pts.append(f"{x:.1f},{y:.1f}")
        svg_elements.append(
            f'<polygon points="{" ".join(pts)}" fill="none" stroke="rgba(255,255,255,0.08)" stroke-width="1"/>'
        )

    # Axis spokes & labels
    for i, cat in enumerate(categories):
        angle = i * angle_slice - math.pi / 2
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        svg_elements.append(
            f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>'
        )

        label_r = radius + 22
        lx = cx + label_r * math.cos(angle)
        ly = cy + label_r * math.sin(angle)
        anchor = "middle"
        if math.cos(angle) > 0.3:
            anchor = "start"
        elif math.cos(angle) < -0.3:
            anchor = "end"

        short_cat = cat.replace("_", " ").title()
        svg_elements.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#94a3b8" font-size="11" font-family="system-ui" text-anchor="{anchor}" alignment-baseline="central">{html.escape(short_cat)}</text>'
        )

    # Polygons for each baseline
    for b_idx, (b_name, scores) in enumerate(baseline_scores.items()):
        color = colors[b_idx % len(colors)]
        pts = []
        for i in range(num_axes):
            val = max(0.0, min(1.0, scores[i] if i < len(scores) else 0.0))
            r = radius * val
            angle = i * angle_slice - math.pi / 2
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            pts.append(f"{x:.1f},{y:.1f}")

        poly_pts = " ".join(pts)
        svg_elements.append(
            f'<polygon points="{poly_pts}" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2"/>'
        )
        # Dots
        for pt in pts:
            px, py = pt.split(",")
            svg_elements.append(
                f'<circle cx="{px}" cy="{py}" r="3.5" fill="{color}"/>'
            )

    return f'''
    <svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" class="radar-svg">
        {"".join(svg_elements)}
    </svg>
    '''


def _svg_latency_bars(baselines: dict[str, BaselineEvaluationResult], width: int = 500, height: int = 220) -> str:
    """Generate an inline SVG horizontal bar chart for p95 Latency & QPS."""
    items = []
    for b_name, res in baselines.items():
        p95 = float(res.ops.get("latency_p95_ms", 0.0))
        qps = float(res.ops.get("concurrent_qps", 0.0))
        items.append((b_name, p95, qps))

    if not items:
        return ""

    max_lat = max([x[1] for x in items] + [1.0])
    bar_height = 24
    gap = 14
    margin_left = 130
    margin_right = 60
    chart_w = width - margin_left - margin_right

    svg_elements = []
    for idx, (name, lat, qps) in enumerate(items):
        y = 20 + idx * (bar_height + gap)
        w = max(4.0, (lat / max_lat) * chart_w)

        # Label
        svg_elements.append(
            f'<text x="{margin_left - 10}" y="{y + bar_height/2}" fill="#cbd5e1" font-size="12" font-family="system-ui" text-anchor="end" alignment-baseline="central">{html.escape(name)}</text>'
        )
        # Bar background
        svg_elements.append(
            f'<rect x="{margin_left}" y="{y}" width="{chart_w}" height="{bar_height}" rx="4" fill="rgba(255,255,255,0.04)"/>'
        )
        # Bar fill
        color = "#38bdf8" if "trace" in name or "hybrid" in name else ("#a855f7" if "hippo" in name else "#64748b")
        svg_elements.append(
            f'<rect x="{margin_left}" y="{y}" width="{w:.1f}" height="{bar_height}" rx="4" fill="{color}"/>'
        )
        # Value text
        svg_elements.append(
            f'<text x="{margin_left + w + 8:.1f}" y="{y + bar_height/2}" fill="#94a3b8" font-size="11" font-family="system-ui" alignment-baseline="central">{lat:.1f}ms</text>'
        )

    total_h = 40 + len(items) * (bar_height + gap)
    return f'''
    <svg width="{width}" height="{total_h}" viewBox="0 0 {width} {total_h}" class="bar-svg">
        {"".join(svg_elements)}
    </svg>
    '''


def generate_html_report(run_result: BenchmarkRunResult, baseline_run: BenchmarkRunResult | None = None) -> str:
    """Generate a modern, responsive, self-contained HTML benchmark report."""

    # 1. Compute Category Matrix for Radar Chart
    all_categories = sorted(list({
        cat
        for b in run_result.baselines.values()
        for cat in b.category_breakdown.keys()
    }))

    radar_data: dict[str, list[float]] = {}
    for b_name, b_res in run_result.baselines.items():
        scores = []
        for cat in all_categories:
            c_info = b_res.category_breakdown.get(cat, {})
            # Use complete coverage or recall
            scores.append(float(c_info.get("recall_at_5", c_info.get("complete_coverage_at_10", 0.0))))
        radar_data[b_name] = scores

    radar_svg = _svg_radar_chart(all_categories, radar_data, size=420)
    latency_svg = _svg_latency_bars(run_result.baselines, width=480)

    # 2. Table Rows
    table_rows = []
    for b_name, res in run_result.baselines.items():
        table_rows.append(f"""
        <tr>
            <td class="baseline-name">{html.escape(b_name)}</td>
            <td>{res.overall_recall_at_5:.3f}</td>
            <td>{res.overall_recall_at_20:.3f}</td>
            <td>{res.overall_ndcg_at_10:.3f}</td>
            <td class="highlight">{res.overall_complete_coverage_at_10:.3f}</td>
            <td>{res.overall_citation_precision_at_5:.3f}</td>
            <td>{res.overall_mrr:.3f}</td>
            <td>{res.overall_abstention_accuracy:.3f}</td>
            <td>{res.ops.get('latency_p95_ms', 0.0):.1f}ms</td>
            <td>{res.ops.get('concurrent_qps', 0.0):.1f}</td>
        </tr>
        """)

    # 3. Significance Rows if baseline_run provided
    sig_section = ""
    if baseline_run:
        sig_rows = []
        all_keys = sorted(list(set(run_result.baselines.keys()) | set(baseline_run.baselines.keys())))
        for b_key in all_keys:
            c_base = run_result.baselines.get(b_key)
            b_base = baseline_run.baselines.get(b_key)
            if not c_base or not b_base:
                continue

            c_cases = c_base.case_results if isinstance(c_base, BaselineEvaluationResult) else c_base.get("case_results", [])
            b_cases = b_base.case_results if isinstance(b_base, BaselineEvaluationResult) else b_base.get("case_results", [])

            metrics_to_test = [
                ("Recall@5", "recall_at_5", getattr(b_base, "overall_recall_at_5", 0.0), getattr(c_base, "overall_recall_at_5", 0.0)),
                ("Complete Coverage@10", "complete_gold_coverage_at_10", getattr(b_base, "overall_complete_coverage_at_10", 0.0), getattr(c_base, "overall_complete_coverage_at_10", 0.0)),
                ("nDCG@10", "ndcg_at_10", getattr(b_base, "overall_ndcg_at_10", 0.0), getattr(c_base, "overall_ndcg_at_10", 0.0)),
            ]

            for m_label, m_field, b_val, c_val in metrics_to_test:
                c_scores = [float(item.get(m_field, 0.0)) for item in c_cases]
                b_scores = [float(item.get(m_field, 0.0)) for item in b_cases]
                stats = compute_paired_statistics(c_scores, b_scores)
                delta = stats["delta"]
                delta_str = f"+{delta:.3f}" if delta > 0 else f"{delta:.3f}"
                sig_badge = '<span class="badge badge-success">p &lt; 0.05 (Significant)</span>' if stats["stat_significant"] else '<span class="badge badge-muted">Not Significant</span>'

                sig_rows.append(f"""
                <tr>
                    <td class="baseline-name">{html.escape(b_key)}</td>
                    <td>{m_label}</td>
                    <td>{b_val:.3f}</td>
                    <td>{c_val:.3f}</td>
                    <td class="{'delta-pos' if delta > 0 else ('delta-neg' if delta < 0 else '')}">{delta_str}</td>
                    <td>[{stats['ci_lower']:.3f}, {stats['ci_upper']:.3f}]</td>
                    <td>{stats['p_value']:.4f}</td>
                    <td>{sig_badge}</td>
                </tr>
                """)

        sig_section = f"""
        <div class="card full-width">
            <h2>Paired Statistical Significance vs. Reference Run ({baseline_run.run_id})</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Baseline</th>
                            <th>Metric</th>
                            <th>Reference</th>
                            <th>Current</th>
                            <th>Delta</th>
                            <th>95% Confidence Interval</th>
                            <th>p-value (Student-t)</th>
                            <th>Significance</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(sig_rows)}
                    </tbody>
                </table>
            </div>
        </div>
        """

    # 4. Legend items for radar chart
    colors = ["#38bdf8", "#4ade80", "#f43f5e", "#fbbf24", "#a855f7", "#ec4899", "#64748b"]
    legend_items = []
    for idx, b_name in enumerate(run_result.baselines.keys()):
        c = colors[idx % len(colors)]
        legend_items.append(f'<div class="legend-item"><span class="legend-color" style="background:{c};"></span><span>{html.escape(b_name)}</span></div>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trace-Lite SOTA Benchmark Report - {html.escape(run_result.dataset_name)}</title>
    <style>
        :root {{
            --bg-primary: #0a0f1d;
            --bg-card: #131b2e;
            --bg-card-hover: #1a243d;
            --border: #1e293b;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-blue: #38bdf8;
            --accent-green: #4ade80;
            --accent-purple: #a855f7;
            --accent-rose: #f43f5e;
            --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            background-color: var(--bg-primary);
            color: var(--text-primary);
            font-family: var(--font-sans);
            line-height: 1.5;
            padding: 2rem 1.5rem;
        }}
        .container {{
            max-width: 1280px;
            margin: 0 auto;
        }}
        header {{
            margin-bottom: 2rem;
            border-bottom: 1px solid var(--border);
            padding-bottom: 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        h1 {{
            font-size: 1.875rem;
            font-weight: 700;
            letter-spacing: -0.025em;
            color: var(--text-primary);
        }}
        .meta-badges {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin-top: 0.5rem;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            padding: 0.25rem 0.625rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            background: rgba(255, 255, 255, 0.06);
            color: var(--text-secondary);
            border: 1px solid var(--border);
        }}
        .badge-success {{
            background: rgba(74, 222, 128, 0.1);
            color: var(--accent-green);
            border-color: rgba(74, 222, 128, 0.2);
        }}
        .badge-muted {{
            background: rgba(148, 163, 184, 0.08);
            color: var(--text-muted);
        }}
        .grid-2 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.5rem;
            transition: border-color 0.2s ease;
        }}
        .card:hover {{
            border-color: rgba(56, 189, 248, 0.3);
        }}
        .card h2 {{
            font-size: 1.125rem;
            font-weight: 600;
            margin-bottom: 1rem;
            color: var(--text-primary);
        }}
        .full-width {{
            grid-column: 1 / -1;
            margin-bottom: 2rem;
        }}
        .table-container {{
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
            text-align: left;
        }}
        th {{
            color: var(--text-secondary);
            font-weight: 600;
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border);
            background: rgba(0, 0, 0, 0.2);
        }}
        td {{
            padding: 0.875rem 1rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            color: var(--text-secondary);
        }}
        tr:hover td {{
            background: rgba(255, 255, 255, 0.02);
            color: var(--text-primary);
        }}
        .baseline-name {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-weight: 600;
            color: var(--accent-blue);
        }}
        .highlight {{
            color: var(--accent-green);
            font-weight: 600;
        }}
        .delta-pos {{
            color: var(--accent-green);
            font-weight: 600;
        }}
        .delta-neg {{
            color: var(--accent-rose);
            font-weight: 600;
        }}
        .radar-container {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}
        .legend {{
            display: flex;
            gap: 1rem;
            flex-wrap: wrap;
            justify-content: center;
            margin-top: 1rem;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 0.375rem;
            font-size: 0.75rem;
            color: var(--text-secondary);
        }}
        .legend-color {{
            width: 10px;
            height: 10px;
            border-radius: 2px;
        }}
        footer {{
            margin-top: 3rem;
            padding-top: 1.5rem;
            border-top: 1px solid var(--border);
            text-align: center;
            font-size: 0.75rem;
            color: var(--text-muted);
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Trace-Lite SOTA Benchmark Report</h1>
                <div class="meta-badges">
                    <span class="badge">Dataset: {html.escape(run_result.dataset_name)}</span>
                    <span class="badge">Queries: {run_result.total_cases}</span>
                    <span class="badge">SHA-256: {run_result.corpus_sha256[:12]}...</span>
                    <span class="badge">{html.escape(run_result.timestamp)}</span>
                    {'<span class="badge badge-success">Release Authority: Verified</span>' if run_result.release_authority else '<span class="badge badge-muted">Dev Fixture</span>'}
                </div>
            </div>
            <div>
                <span class="badge">Run ID: {html.escape(run_result.run_id)}</span>
            </div>
        </header>

        <div class="grid-2">
            <div class="card radar-container">
                <h2>8-Category Retrieval Coverage Radar</h2>
                {radar_svg}
                <div class="legend">
                    {"".join(legend_items)}
                </div>
            </div>

            <div class="card">
                <h2>p95 Query Latency & Throughput</h2>
                {latency_svg}
                <p style="margin-top: 1.5rem; font-size: 0.8125rem; color: var(--text-muted);">
                    Evaluated under concurrent multi-threaded worker pools with memory RSS and storage footprint monitoring.
                </p>
            </div>
        </div>

        <div class="card full-width">
            <h2>Overall Baseline Evaluation Matrix</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>Baseline Engine</th>
                            <th>Recall@5</th>
                            <th>Recall@20</th>
                            <th>nDCG@10</th>
                            <th>Complete Coverage@10</th>
                            <th>Precision@5</th>
                            <th>MRR</th>
                            <th>Abstention Acc</th>
                            <th>Latency p95</th>
                            <th>QPS</th>
                        </tr>
                    </thead>
                    <tbody>
                        {"".join(table_rows)}
                    </tbody>
                </table>
            </div>
        </div>

        {sig_section}

        <footer>
            Trace Memory Substrate &bull; SOTA Benchmark Harness &bull; Generated automatically by tl-benchmark
        </footer>
    </div>
</body>
</html>
"""
