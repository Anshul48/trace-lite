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

"""CLI interface for trace-lite."""

import sys
from pathlib import Path
import click
from trace_lite.db import TraceLite
from trace_lite.projects import ProjectManager


def resolve_data_dir(ctx_dir: str | Path | None) -> Path:
    """Resolve data directory: explicit --data-dir overrides central active project path."""
    pm = ProjectManager()
    # Check if --data-dir was explicitly passed in command line arguments
    is_explicit = any(arg.startswith("--data-dir") for arg in sys.argv)
    if is_explicit and ctx_dir:
        return Path(ctx_dir).resolve()
    return pm.get_active_project_path()


@click.group()
@click.option("--data-dir", default=None, help="Directory for storage files (overrides active project)")
@click.pass_context
def main(ctx, data_dir):
    """trace-lite (aliases: tracel, tl): Self-organizing headless database."""
    ctx.ensure_object(dict)
    ctx.obj["data_dir"] = resolve_data_dir(data_dir)


@main.command("projects")
@click.pass_context
def projects(ctx):
    """Interactive TUI dashboard to view, switch, create, or delete database projects."""
    from trace_lite.tui_projects import run_projects_tui
    run_projects_tui()


@main.command("chat")
@click.pass_context
def chat(ctx):
    """Interactive Chat REPL TUI to query active database and run slash commands."""
    from trace_lite.tui_chat import run_chat_tui
    run_chat_tui(data_dir=ctx.obj["data_dir"])


@main.command("use")
@click.argument("project_name")
@click.pass_context
def use(ctx, project_name):
    """Switch active global database project."""
    pm = ProjectManager()
    try:
        info = pm.switch_project(project_name)
        click.echo(f"✓ Switched active project to '{project_name}' ({info['path']})")
    except KeyError:
        click.echo(f"Error: Project '{project_name}' not found. Run 'trace-lite projects' to list projects.", err=True)
        sys.exit(1)


@main.command("delete")
@click.argument("project_name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.pass_context
def delete_project_cli(ctx, project_name, force):
    """Delete a database project and purge its directory."""
    pm = ProjectManager()
    info = pm.get_project(project_name)
    if not info:
        click.echo(f"Error: Project '{project_name}' does not exist.", err=True)
        sys.exit(1)

    if not force:
        confirm = click.prompt(
            f"Are you sure you want to delete project '{project_name}' at {info['path']}? Type project name to confirm"
        )
        if confirm.strip() != project_name:
            click.echo("Deletion aborted.")
            return

    pm.delete_project(project_name, delete_files=True)
    click.echo(f"✓ Project '{project_name}' deleted successfully.")


@main.command()
@click.argument("text", required=False)
@click.option("--file", "-f", "file_path", type=click.Path(exists=True), help="File path to ingest")
@click.option("--name", "-n", "document_name", help="Document title or label")
@click.option(
    "--chronological",
    type=click.Choice(["asc", "desc"]),
    help="Time order metadata",
)
@click.pass_context
def ingest(ctx, text, file_path, document_name, chronological):
    """Ingest raw text or a file into the database."""
    db = TraceLite(ctx.obj["data_dir"])

    if file_path:
        res = db.ingest_file(
            file_path, document_name=document_name, chronological_order=chronological
        )
    elif text:
        res = db.ingest(
            text, document_name=document_name, chronological_order=chronological
        )
    else:
        # Read from standard input if piped
        if not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
            if stdin_text.strip():
                res = db.ingest(
                    stdin_text, document_name=document_name, chronological_order=chronological
                )
            else:
                click.echo("Error: Empty input provided.", err=True)
                sys.exit(1)
        else:
            click.echo("Error: Provide text argument, --file, or pipe stdin.", err=True)
            sys.exit(1)

    click.echo(
        f"✓ Ingested {res.atom_count} atoms from artifact {res.artifact_id[:12]} "
        f"into {len(res.tree_ids)} tree(s)."
    )


@main.command()
@click.option("--tree-id", help="Consolidate specific tree ID only")
@click.pass_context
def consolidate(ctx, tree_id):
    """Build/rebuild RAPTOR summary trees."""
    db = TraceLite(ctx.obj["data_dir"])
    res = db.consolidate(tree_id=tree_id)
    click.echo(
        f"✓ Consolidated {res.trees_updated} tree(s), "
        f"generated {res.summaries_generated} summary node(s)."
    )


@main.command()
@click.argument("query_text")
@click.option("--top-k", "-k", default=5, help="Maximum number of results to return")
@click.option(
    "--mode",
    type=click.Choice(["hybrid", "tree", "flat"]),
    default="hybrid",
    help="Search mode",
)
@click.pass_context
def query(ctx, query_text, top_k, mode):
    """Query the database using LATTICE traversal + flat search."""
    db = TraceLite(ctx.obj["data_dir"])
    res = db.query(query_text, top_k=top_k, mode=mode)

    click.echo(f"\nQuery: '{res.query_text}' (Mode: {res.mode})\n")
    if not res.items:
        click.echo("No matching evidence found.")
        return

    for idx, item in enumerate(res.items, 1):
        doc_name = item.source_artifact.document_name if item.source_artifact else "Unknown"
        click.echo(f"--- [{idx}] Score: {item.score:.3f} | Source: {doc_name} ---")
        click.echo(item.atom.content)
        if item.traversal_path:
            click.echo(f"Path: {' -> '.join(item.traversal_path[:2])}...")
        click.echo()


@main.command()
@click.pass_context
def status(ctx):
    """Display database stats and capacity metrics."""
    db = TraceLite(ctx.obj["data_dir"])
    s = db.status()
    click.echo("=== trace-lite Status ===")
    click.echo(f"Data Dir:          {ctx.obj['data_dir']}")
    click.echo(f"Total Atoms:       {s.total_atoms:,}")
    click.echo(f"Total Trees:       {s.total_trees}")
    click.echo(f"Active Nodes:      {s.active_nodes:,} / {s.total_nodes:,}")
    click.echo(f"Estimated Tokens: ~{s.estimated_tokens:,}")


@main.command()
@click.pass_context
def trees(ctx):
    """List all summary trees in the forest."""
    db = TraceLite(ctx.obj["data_dir"])
    tree_list = db.trees()
    if not tree_list:
        click.echo("No trees exist yet.")
        return

    click.echo(f"=== Forest ({len(tree_list)} trees) ===")
    for t in tree_list:
        click.echo(
            f"- ID: {t.tree_id[:8]} | Name: '{t.name}' | "
            f"Leaves: {t.leaf_count} | Depth: {t.depth}"
        )
        if t.description:
            click.echo(f"  Description: {t.description[:100]}...")


@main.command()
@click.argument("output_path", type=click.Path())
@click.pass_context
def export(ctx, output_path):
    """Export database spine and cortex to a zip archive."""
    db = TraceLite(ctx.obj["data_dir"])
    db.export(output_path)
    click.echo(f"✓ Exported archive to {output_path}")


@main.command("providers")
@click.option("--list", "-l", "list_only", is_flag=True, help="List configured LLM providers without launching TUI")
@click.pass_context
def providers(ctx, list_only):
    """Configure popular LLM providers and API keys via interactive TUI."""
    from trace_lite.tui import run_provider_tui
    from trace_lite.providers import load_config_data, POPULAR_PROVIDERS

    if list_only:
        cfg = load_config_data()
        active_p = cfg.get("active_provider")
        active_m = cfg.get("active_model")
        saved = cfg.get("providers", {})

        click.echo("=== LLM Provider Configurations ===")
        click.echo(f"Active Provider: {active_p or 'None (Default: ollama)'}")
        click.echo(f"Active Model:    {active_m or 'ollama/llama3.1:8b'}\n")

        for p in POPULAR_PROVIDERS:
            info = saved.get(p.id, {})
            has_key = bool(info.get("api_key")) or not p.requires_api_key
            status_str = "★ Active" if p.id == active_p else ("✓ Configured" if has_key and p.id in saved else "- Not configured")
            click.echo(f"{p.name:<25} Status: {status_str:<15} Default Model: {info.get('model') or p.default_model}")
        return

    run_provider_tui()


@main.command("configure")
@click.pass_context
def configure(ctx):
    """Interactive TUI wizard to set up LLM providers and API keys."""
    ctx.invoke(providers)


@main.command("visualize")
@click.option("--web", is_flag=True, help="Launch interactive web UI dashboard in browser")
@click.option("--host", default="127.0.0.1", help="Web server host interface")
@click.option("--port", default=8420, type=int, help="Web server port number")
@click.pass_context
def visualize(ctx, web, host, port):
    """Visualize RAPTOR summary forest and database state."""
    db = TraceLite(ctx.obj["data_dir"])
    if web:
        from trace_lite.ui import launch_web_ui
        launch_web_ui(data_dir=ctx.obj["data_dir"], host=host, port=port)
    else:
        from trace_lite.visualizer import render_terminal_visualizer
        render_terminal_visualizer(db)


@main.command("ui")
@click.option("--host", default="127.0.0.1", help="Web server host interface")
@click.option("--port", default=8420, type=int, help="Web server port number")
@click.pass_context
def ui(ctx, host, port):
    """Launch interactive Web UI visualizer dashboard."""
    from trace_lite.ui import launch_web_ui
    launch_web_ui(data_dir=ctx.obj["data_dir"], host=host, port=port)


@main.command("watch")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option("--poll-interval", default=2.0, type=float, help="Polling interval in seconds")
@click.option("--consolidate/--no-consolidate", default=True, help="Auto-consolidate summary trees after changes")
@click.pass_context
def watch(ctx, directory, poll_interval, consolidate):
    """Watch a directory (e.g. Obsidian Vault) for markdown file changes and auto-ingest notes."""
    import time
    from trace_lite.watcher import VaultWatcher

    vault_path = Path(directory).resolve()
    db = TraceLite(ctx.obj["data_dir"])

    click.echo(f"Watching directory '{vault_path}' for markdown changes...")
    click.echo(f"Data directory: {ctx.obj['data_dir']}")
    click.echo("Press Ctrl+C to stop watching.\n")

    watcher = VaultWatcher(
        vault_path=vault_path,
        db=db,
        poll_interval=poll_interval,
        auto_consolidate=consolidate,
    )

    # Initial scan
    res = watcher.scan_once()
    click.echo(
        f"✓ Initial scan complete: {res['files_scanned']} file(s) scanned, "
        f"{res['files_ingested']} note(s) ingested ({res['atoms_ingested']} atoms)."
    )

    watcher.start()

    try:
        while watcher.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("\nStopping watcher...")
    finally:
        watcher.stop()
        click.echo("✓ Watcher stopped.")


if __name__ == "__main__":
    main()

