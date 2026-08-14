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

"""Developer-friendly CLI interface for trace-lite."""

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import click
from trace_lite.adapters.llm import redact_diagnostic
from trace_lite.db import TraceLite, QueryBlockedError
from trace_lite.projects import (
    NO_ACTIVE_PROJECT_MESSAGE,
    NoActiveProjectError,
    ProjectConfigError,
    ProjectDeletionError,
    ProjectManager,
)
from trace_lite.providers import (
    POPULAR_PROVIDERS,
    load_config_data,
    sanitize_config_data,
    save_provider_key,
    migrate_plaintext_credentials,
    set_auto_load_models,
)


def _success_message(message: str) -> str:
    """Format a success message for both Unicode and legacy consoles."""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "✓".encode(encoding)
        marker = "✓"
    except (LookupError, UnicodeEncodeError):
        marker = "OK"
    return f"{marker} {message}"


def _build_diagnostic_sink(output_json: bool):
    """Print progress without contaminating machine-readable stdout."""
    def emit(event: dict) -> None:
        stage = event.get("stage_label", event.get("stage", "build"))
        status = event.get("status", "attempt")
        attempt = event.get("attempt", 0)
        maximum = event.get("max_attempts", 1)
        code = event.get("failure_code")
        detail = event.get("message") or ""
        suffix = f" [{code}]" if code else ""
        click.echo(
            f"trace-lite: {stage} {status} ({attempt}/{maximum}){suffix}: {detail}",
            err=output_json,
        )
    return emit


def _safe_build_failure(action: str, exc: BaseException) -> str:
    detail = " ".join(redact_diagnostic(str(exc)).split())[:500]
    return (
        f"{action} failed; activation was prevented and the previous active index was preserved. "
        f"Source captures remain in Spine/pending storage. {detail or type(exc).__name__} "
        "Correct the provider or generated-output issue, then retry the build."
    )


def resolve_data_dir(ctx_dir: str | Path | None) -> Path:
    """Resolve data directory, keeping an explicit path isolated from projects."""
    pm = ProjectManager()
    if ctx_dir:
        return Path(ctx_dir).resolve()
    return pm.get_active_project_path()


def _require_data_dir(ctx) -> Path:
    """Return the selected database path or a concise empty-registry error."""
    data_dir = ctx.obj.get("data_dir")
    if data_dir is None:
        raise click.ClickException(NO_ACTIVE_PROJECT_MESSAGE)
    return Path(data_dir)


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--data-dir", default=None, help="Use this isolated storage directory")
@click.pass_context
def main(ctx, data_dir):
    """trace-lite (aliases: tracel, tl): sources, organize, and ask.

    Quickstart: run ``tl`` to see the next useful actions. The main tasks are
    ingest/watch (sources), organize/consolidate (structure), and query (ask).
    """
    ctx.ensure_object(dict)
    # Project/configuration commands are intentionally backed by the global
    # registry even when --data-dir is present.  They must remain usable after
    # the last project has been deleted, so do not resolve an active database
    # for those commands during group startup.
    registry_only = ctx.invoked_subcommand in {"project", "config", "completion"}
    if data_dir is not None:
        ctx.obj["data_dir"] = Path(data_dir).resolve()
    elif registry_only:
        ctx.obj["data_dir"] = None
    else:
        try:
            ctx.obj["data_dir"] = resolve_data_dir(data_dir)
        except NoActiveProjectError:
            ctx.obj["data_dir"] = None
    ctx.obj["data_dir_locked"] = data_dir is not None
    if ctx.invoked_subcommand is None:
        click.echo("trace-lite quickstart")
        click.echo("  Add content:     tl ingest \"your note\"")
        click.echo("  Organize pending: tl organize")
        click.echo("  Ask a question:  tl query \"what is here?\"")
        click.echo("  Open workspace:  tl ui")
        click.echo("  See all tasks:   tl --help")


# -----------------------------------------------------------------------------
# Project Subcommand Group (tl project / tl projects)
# -----------------------------------------------------------------------------

@main.group("project", invoke_without_command=True)
@click.pass_context
def project_group(ctx):
    """Manage database projects and isolated stores."""
    if ctx.invoked_subcommand is None:
        from trace_lite.tui_projects import run_projects_tui
        run_projects_tui()


@project_group.command("list")
@click.option("--json", "output_json", is_flag=True, help="Output results in JSON format")
@click.pass_context
def project_list(ctx, output_json):
    """List all registered database projects."""
    pm = ProjectManager()
    projects = pm.list_projects()

    if output_json:
        click.echo(json.dumps(projects, indent=2))
        return

    active_name = pm.get_active_project_name()
    click.echo(f"=== Registered Database Projects ({len(projects)}) ===")
    if not projects:
        click.echo("No projects registered. Create one with 'tl project create <name>'.")
        return
    for p in projects:
        status_tag = " [ACTIVE]" if p["name"] == active_name else ""
        click.echo(
            f"- {p['name']}{status_tag}\n"
            f"  Path:  {p['path']}\n"
            f"  Atoms: {p['atom_count']:,} | Trees: {p['tree_count']} | Tokens: ~{p['estimated_tokens']:,}"
        )


@project_group.command("use")
@click.argument("project_name")
@click.pass_context
def project_use(ctx, project_name):
    """Switch active global database project."""
    pm = ProjectManager()
    try:
        info = pm.switch_project(project_name)
        click.echo(_success_message(
            f"Switched active project to '{project_name}' ({info['path']})"
        ))
    except KeyError:
        click.echo(f"Error: Project '{project_name}' not found. Run 'tl project list' to view available projects.", err=True)
        sys.exit(1)


@project_group.command("create")
@click.argument("project_name")
@click.option("--path", "-p", default=None, help="Custom storage directory path")
@click.option("--description", "-d", default="", help="Project description")
@click.pass_context
def project_create(ctx, project_name, path, description):
    """Create a new database project and set it active."""
    pm = ProjectManager()
    try:
        info = pm.create_project(name=project_name, path=path, description=description)
        click.echo(_success_message(
            f"Created and switched to project '{project_name}' at {info['path']}"
        ))
    except Exception as e:
        click.echo(f"Error creating project: {e}", err=True)
        sys.exit(1)


@project_group.command("current")
@click.option("--json", "output_json", is_flag=True, help="Output results in JSON format")
@click.pass_context
def project_current(ctx, output_json):
    """Display information about the currently active project."""
    pm = ProjectManager()
    name = pm.get_active_project_name()
    if name is None:
        if output_json:
            click.echo(json.dumps({"active_project": None}, indent=2))
        else:
            click.echo(NO_ACTIVE_PROJECT_MESSAGE, err=True)
        raise click.exceptions.Exit(1)
    info = pm.get_project(name)
    if not info:
        click.echo(f"Active project '{name}' missing from registry.", err=True)
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(info, indent=2))
        return

    click.echo(f"Active Project: {info['name']}")
    click.echo(f"Storage Path:   {info['path']}")
    click.echo(f"Created At:     {info.get('created_at', 'N/A')}")


@project_group.command("delete")
@click.argument("project_name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
@click.option(
    "--purge-external",
    is_flag=True,
    help="Also recursively remove storage registered from an external folder.",
)
@click.pass_context
def project_delete(ctx, project_name, force, purge_external):
    """Unregister a database project and purge only storage it owns."""
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

    try:
        pm.delete_project(
            project_name,
            delete_files=True,
            purge_external=purge_external,
        )
    except ProjectDeletionError as exc:
        click.echo(f"Error deleting project: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc

    click.echo(_success_message(f"Project '{project_name}' deleted successfully."))
    if pm.get_active_project_name() is None:
        click.echo(NO_ACTIVE_PROJECT_MESSAGE)


# -----------------------------------------------------------------------------
# Configuration subcommand group
# -----------------------------------------------------------------------------

@main.group("config", invoke_without_command=True)
@click.pass_context
def config_group(ctx):
    """Configure LLM providers, active models, and API keys."""
    if ctx.invoked_subcommand is None:
        from trace_lite.tui import run_provider_tui
        run_provider_tui()


@config_group.command("list")
@click.option("--json", "output_json", is_flag=True, help="Output configuration in JSON format")
@click.pass_context
def config_list(ctx, output_json):
    """List configured LLM providers and active model."""
    cfg = load_config_data()
    if output_json:
        click.echo(json.dumps(sanitize_config_data(cfg), indent=2))
        return

    active_p = cfg.get("active_provider")
    active_m = cfg.get("active_model")
    saved = cfg.get("providers", {})

    click.echo("=== LLM Provider Configurations ===")
    click.echo(f"Active Provider: {active_p or 'None (Default: ollama)'}")
    click.echo(f"Active Model:    {active_m or 'ollama/llama3.1:8b'}\n")
    click.echo(
        "Embedding Warm-up: "
        f"{'enabled' if cfg.get('auto_load_models') is not False else 'disabled'}\n"
    )

    for p in POPULAR_PROVIDERS:
        info = saved.get(p.id, {})
        has_key = bool(info.get("has_api_key")) or not p.requires_api_key
        status_str = "* Active" if p.id == active_p else ("+ Configured" if has_key and p.id in saved else "- Not configured")
        click.echo(f"{p.name:<25} Status: {status_str:<15} Default Model: {info.get('model') or p.default_model}")


@config_group.command("get")
@click.option("--json", "output_json", is_flag=True, help="Output active configuration in JSON format")
@click.pass_context
def config_get(ctx, output_json):
    """Show current active LLM provider and model."""
    cfg = load_config_data()
    active_data = {
        "active_provider": cfg.get("active_provider"),
        "active_model": cfg.get("active_model"),
        "api_base": cfg.get("api_base"),
        "api_version": cfg.get("api_version"),
        "auto_load_models": cfg.get("auto_load_models") is not False,
    }
    if output_json:
        click.echo(json.dumps(active_data, indent=2))
    else:
        click.echo(f"Active Provider: {active_data['active_provider'] or 'None'}")
        click.echo(f"Active Model:    {active_data['active_model'] or 'None'}")
        click.echo(f"API Base:        {active_data['api_base'] or 'Default'}")
        click.echo(f"API Version:     {active_data['api_version'] or 'Default'}")


@config_group.command("auto-load")
@click.option("--on", "enable", is_flag=True, help="Enable embedding-model warm-up for long-running commands")
@click.option("--off", "disable", is_flag=True, help="Disable automatic embedding-model warm-up")
@click.option("--json", "output_json", is_flag=True, help="Output the setting as JSON")
@click.pass_context
def config_auto_load(ctx, enable, disable, output_json):
    """Control background loading of the local embedding model."""
    if enable == disable:
        raise click.UsageError("Choose exactly one of --on or --off.")
    try:
        config = set_auto_load_models(enable)
    except Exception as exc:
        raise click.ClickException(f"Could not persist model auto-load setting: {exc}") from exc
    enabled = config.get("auto_load_models") is not False
    payload = {"status": "ok", "auto_load_models": enabled, "enabled": enabled}
    if output_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(f"Automatic embedding-model warm-up: {'enabled' if enabled else 'disabled'}.")


@config_group.command("migrate-credentials")
@click.option("--yes", is_flag=True, help="Confirm removal of legacy plaintext credentials")
@click.option("--json", "output_json", is_flag=True, help="Output migration details as JSON")
@click.pass_context
def config_migrate_credentials(ctx, yes, output_json):
    """Explicitly migrate legacy plaintext provider keys to secure storage."""
    try:
        result = migrate_plaintext_credentials(confirmed=yes)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    if output_json:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        click.echo(f"Migrated {len(result.get('migrated', []))} provider credential(s).")


# -----------------------------------------------------------------------------
# Local model commands
# -----------------------------------------------------------------------------

@main.group("models")
def models_group():
    """Inspect and explicitly warm up local models."""


@models_group.command("warmup")
@click.option("--json", "output_json", is_flag=True, help="Output warm-up state as JSON")
@click.pass_context
def models_warmup(ctx, output_json):
    """Load and verify the local embedding model for this process."""
    db = TraceLite(_require_data_dir(ctx))
    try:
        result = db.warm_up_models()
    except Exception as exc:
        raise click.ClickException(f"Embedding model warm-up failed: {exc}") from exc
    if output_json:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        click.echo(
            f"Embedding model ready: {result.get('embedding_model') or result.get('model')} "
            "(the model is cached for this process; one-shot exit will release it)."
        )


# -----------------------------------------------------------------------------
# Core Database Commands
# -----------------------------------------------------------------------------

@main.command("chat")
@click.pass_context
def chat(ctx):
    """Interactive Chat REPL TUI to query active database and run slash commands."""
    from trace_lite.tui_chat import run_chat_tui
    run_chat_tui(data_dir=ctx.obj["data_dir"])


@main.command("ingest")
@click.argument("text", required=False)
@click.option("--file", "-f", "file_path", type=click.Path(exists=True), help="File path to ingest")
@click.option("--name", "-n", "document_name", help="Document title or label")
@click.option(
    "--chronological",
    type=click.Choice(["asc", "desc"]),
    help="Time order metadata",
)
@click.option("--json", "output_json", is_flag=True, help="Output ingestion results as JSON")
@click.pass_context
def ingest(ctx, text, file_path, document_name, chronological, output_json):
    """Ingest raw text or a file into the database."""
    db = TraceLite(_require_data_dir(ctx))

    if file_path:
        res = db.ingest_file(
            file_path, document_name=document_name, chronological_order=chronological
        )
    elif text:
        res = db.ingest(
            text, document_name=document_name, chronological_order=chronological
        )
    else:
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

    status = db.status()
    payload = {
        "status": "ok",
        "artifact_id": res.artifact_id,
        "atom_count": res.atom_count,
        "tree_ids": res.tree_ids,
        "queued": True,
        "pending_atoms": status.pending_atoms,
        "pending_trees": status.pending_trees,
        "needs_organization": status.needs_organization,
        "needs_recovery": status.needs_recovery,
    }
    if output_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(
        f"Ingested {res.atom_count} atoms from artifact {res.artifact_id[:12]}. "
        "Queued as durable source captures for organization."
    )


@main.command("organize")
@click.option("--json", "output_json", is_flag=True, help="Output organization results as JSON")
@click.pass_context
def organize(ctx, output_json):
    """Preflight the provider and safely build staged pending structure."""
    db = TraceLite(_require_data_dir(ctx))
    try:
        res = db.organize(diagnostic_sink=_build_diagnostic_sink(output_json))
    except Exception as exc:
        raise click.ClickException(_safe_build_failure("Organization", exc)) from exc
    payload = {
        "status": "ok",
        "trees_updated": res.trees_updated,
        "summaries_generated": res.summaries_generated,
        "pending_atoms": res.pending_atoms,
        "pending_trees": res.pending_trees,
        "orphaned_atoms": res.orphaned_atoms,
        "needs_organization": res.needs_organization,
        "needs_recovery": res.needs_recovery,
    }
    if output_json:
        click.echo(json.dumps(payload, indent=2))
        return
    if res.needs_recovery:
        state = f"{res.orphaned_atoms} source atom(s) still need recovery"
    elif res.needs_organization:
        state = f"{res.pending_atoms} atom(s) remain queued"
    else:
        state = "workspace is ready to ask"
    click.echo(
        f"Organized {res.trees_updated} tree(s), generated "
        f"{res.summaries_generated} summary node(s); {state}."
    )


@main.command("consolidate")
@click.option("--tree-id", help="Consolidate specific tree ID only")
@click.option("--json", "output_json", is_flag=True, help="Output rebuild results as JSON")
@click.pass_context
def consolidate(ctx, tree_id, output_json):
    """Explicitly rebuild RAPTOR summary trees (advanced/full operation)."""
    db = TraceLite(_require_data_dir(ctx))
    try:
        res = db.consolidate(
            tree_id=tree_id,
            diagnostic_sink=_build_diagnostic_sink(output_json),
        )
    except Exception as exc:
        raise click.ClickException(_safe_build_failure("Rebuild", exc)) from exc
    payload = {
        "status": "ok",
        "trees_updated": res.trees_updated,
        "summaries_generated": res.summaries_generated,
        "pending_atoms": res.pending_atoms,
        "pending_trees": res.pending_trees,
        "orphaned_atoms": res.orphaned_atoms,
        "needs_organization": res.needs_organization,
        "needs_recovery": res.needs_recovery,
    }
    if output_json:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(
        f"Rebuilt {res.trees_updated} tree(s), "
        f"generated {res.summaries_generated} summary node(s)."
    )


@main.command("reindex")
@click.option("--all", "rebuild_all", is_flag=True, help="Rebuild all derived state from Spine")
@click.option("--json", "output_json", is_flag=True, help="Output rebuild results as JSON")
@click.pass_context
def reindex(ctx, rebuild_all, output_json):
    """Validated full derived rebuild from immutable Spine atoms."""
    if not rebuild_all:
        raise click.UsageError("Specify --all; reindex never performs an implicit partial rebuild.")
    db = TraceLite(_require_data_dir(ctx))
    try:
        result = db.reindex_all(
            diagnostic_sink=_build_diagnostic_sink(output_json)
        )
    except Exception as exc:
        raise click.ClickException(_safe_build_failure("Reindex", exc)) from exc
    payload = {"status": "ok", **result.to_dict()}
    if output_json:
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(
            f"Activated build {result.build_id}: {result.trees_updated} tree(s), "
            f"{result.summaries_generated} summary node(s); validation healthy."
        )


@main.command("validate")
@click.option("--json", "output_json", is_flag=True, help="Output validation as JSON")
@click.pass_context
def validate(ctx, output_json):
    """Validate active Cortex/vector state against Spine."""
    db = TraceLite(_require_data_dir(ctx))
    result = db.validate_index()
    if output_json:
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        state = "healthy" if result["healthy"] else "unhealthy"
        click.echo(
            f"Index: {state} | atoms {result.get('indexed_atoms', 0)}/"
            f"{result.get('source_atoms', 0)} | nodes {result.get('node_count', 0)} | "
            f"vectors {result.get('vector_count', 0)}"
        )
        for error in result.get("errors", []):
            click.echo(f"Error: {error}", err=True)
        for warning in result.get("warnings", []):
            click.echo(f"Warning: {warning}")
    if not result["healthy"]:
        raise click.exceptions.Exit(1)


@main.command("reset")
@click.option("--derived", is_flag=True, help="Remove only Cortex/vector derived state")
@click.option("--all", "wipe_all", is_flag=True, help="Remove Spine and all derived state")
@click.option("--yes", is_flag=True, help="Acknowledge the destructive operation")
@click.option("--confirm", default=None, help="Project name required for --all")
@click.option("--archive", type=click.Path(), default=None, help="Archive path created before reset")
@click.pass_context
def reset(ctx, derived, wipe_all, yes, confirm, archive):
    """Safely reset derived state, or explicitly wipe a named project."""
    if derived == wipe_all or not (derived or wipe_all):
        raise click.UsageError("Choose exactly one of --derived or --all.")
    if not yes:
        raise click.UsageError("Reset requires --yes.")
    data_dir = _require_data_dir(ctx).resolve()
    project_name = ProjectManager().get_active_project_name()
    if project_name is None:
        raise click.ClickException(NO_ACTIVE_PROJECT_MESSAGE)
    if wipe_all and (not confirm or confirm != project_name):
        raise click.UsageError(
            f"--all requires --confirm {project_name!r} for the active project."
        )
    db = TraceLite(data_dir)

    archive_path = Path(archive).resolve() if archive else (
        data_dir.parent / f"{data_dir.name}-archive-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
    )
    db.export(archive_path)
    click.echo(f"Created archive: {archive_path}")

    if derived:
        db.reset_derived()
        click.echo("Reset Cortex and vector state; Spine source data was preserved.")
        return

    if data_dir.exists():
        for child in list(data_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    click.echo(f"Wiped project '{project_name}' after archiving source and derived state.")


@main.command("query")
@click.argument("query_text")
@click.option("--top-k", "-k", default=5, help="Maximum number of results to return")
@click.option(
    "--mode",
    type=click.Choice(["hybrid", "tree", "flat", "lexical"]),
    default="hybrid",
    help="Search mode",
)
@click.option("--json", "output_json", is_flag=True, help="Output query results in JSON format")
@click.option("--force", is_flag=True, help="Search only the last verified index; exclude pending captures and make no LLM call")
@click.option("--allow-hot-inbox", is_flag=True, help="Allow querying pending unorganized atoms")
@click.pass_context
def query(ctx, query_text, top_k, mode, output_json, force, allow_hot_inbox):
    """Query the database using LATTICE traversal + flat search + lexical search."""
    db = TraceLite(_require_data_dir(ctx))
    needed_organization = db.status().needs_organization
    try:
        res = db.query(query_text, top_k=top_k, mode=mode, force=force, allow_hot_inbox=allow_hot_inbox)
    except QueryBlockedError as exc:
        raise click.ClickException(str(exc)) from exc

    if output_json:
        items_payload = []
        for item in res.items:
            doc_name = item.source_artifact.document_name if item.source_artifact else "Unknown"
            items_payload.append({
                "atom_id": item.atom.atom_id,
                "content": item.atom.content,
                "score": item.score,
                "source": doc_name,
                "traversal_path": item.traversal_path,
                "source_location": getattr(item, "source_location", {}),
                "channel_scores": getattr(item, "channel_scores", {}),
            })
        payload = {
            "query_text": res.query_text,
            "mode": res.mode,
            "results_count": len(items_payload),
            "items": items_payload,
            "organized_before_query": needed_organization,
            "needs_organization": db.status().needs_organization,
            "warnings": res.warnings,
            "sufficiency_state": getattr(res, "sufficiency_state", "answerable"),
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"\nQuery: '{res.query_text}' (Mode: {res.mode})\n")
    for warning in res.warnings:
        click.echo(f"Warning: {warning}")
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


@main.command("status")
@click.option("--json", "output_json", is_flag=True, help="Output status metrics in JSON format")
@click.pass_context
def status(ctx, output_json):
    """Display database stats and capacity metrics."""
    data_dir = _require_data_dir(ctx)
    db = TraceLite(data_dir)
    s = db.status()

    if output_json:
        payload = {
            "data_dir": str(data_dir),
            "total_atoms": s.total_atoms,
            "total_trees": s.total_trees,
            "active_nodes": s.active_nodes,
            "total_nodes": s.total_nodes,
            "estimated_tokens": s.estimated_tokens,
            "pending_atoms": s.pending_atoms,
            "pending_trees": s.pending_trees,
            "orphaned_atoms": s.orphaned_atoms,
            "needs_organization": s.needs_organization,
            "needs_recovery": s.needs_recovery,
            "indexed_atoms": s.indexed_atoms,
            "valid_summaries": s.valid_summaries,
            "fallback_summaries": s.fallback_summaries,
            "vector_count": s.vector_count,
            "active_build_id": s.active_build_id,
            "validation_state": s.validation_state,
            "validation_errors": s.validation_errors or [],
            "validation_warnings": s.validation_warnings or [],
            "index_trusted": s.index_trusted,
            "structure_present": s.structure_present,
            "quality_verified": s.quality_verified,
            "credential_state": s.credential_state,
            "active_provider": s.active_provider,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo("=== trace-lite Status ===")
    click.echo(f"Data Dir:          {data_dir}")
    click.echo(f"Total Atoms:       {s.total_atoms:,}")
    click.echo(f"Total Trees:       {s.total_trees}")
    click.echo(f"Active Nodes:      {s.active_nodes:,} / {s.total_nodes:,}")
    click.echo(f"Estimated Tokens: ~{s.estimated_tokens:,}")
    click.echo(f"Indexed Atoms:     {s.indexed_atoms:,} | Vectors: {s.vector_count:,}")
    click.echo(
        f"Summaries:         {s.valid_summaries:,} valid | "
        f"{s.fallback_summaries:,} legacy fallback (untrusted)"
    )
    click.echo(f"Index Health:      {s.validation_state}")
    click.echo(f"Index Trust:       {'verified' if s.index_trusted else 'untrusted'}")
    click.echo(
        f"Structure/Quality: {'present' if s.structure_present else 'absent'} / "
        f"{'verified' if s.quality_verified else 'unverified'}"
    )
    click.echo(f"Credential State:  {s.credential_state}")
    if s.needs_recovery:
        click.echo(f"Needs Recovery:    {s.orphaned_atoms:,} orphaned atom(s)")
    elif s.needs_organization:
        click.echo(f"Ready to Organize: {s.pending_atoms:,} atom(s) in {s.pending_trees} tree(s)")
    else:
        click.echo("Workspace:          Ready to Ask")


@main.command("trees")
@click.option("--json", "output_json", is_flag=True, help="Output tree forest summary in JSON format")
@click.pass_context
def trees(ctx, output_json):
    """List all summary trees in the forest."""
    db = TraceLite(_require_data_dir(ctx))
    tree_list = db.trees()

    if output_json:
        payload = [
            {
                "tree_id": t.tree_id,
                "name": t.name,
                "description": t.description,
                "leaf_count": t.leaf_count,
                "depth": t.depth,
                "root_node_id": t.root_node_id,
            }
            for t in tree_list
        ]
        click.echo(json.dumps(payload, indent=2))
        return

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


@main.command("export")
@click.argument("output_path", type=click.Path())
@click.pass_context
def export(ctx, output_path):
    """Export database spine and cortex to a zip archive."""
    db = TraceLite(_require_data_dir(ctx))
    db.export(output_path)
    click.echo(_success_message(f"Exported archive to {output_path}"))


@main.command("visualize")
@click.option("--web", is_flag=True, help="Launch interactive web UI dashboard in browser")
@click.option("--port", default=8420, type=int, help="Web server port number")
@click.option("--no-browser", is_flag=True, help="Do not open a browser")
@click.pass_context
def visualize(ctx, web, port, no_browser):
    """Visualize RAPTOR summary forest and database state."""
    data_dir = _require_data_dir(ctx)
    db = TraceLite(data_dir)
    if web:
        from trace_lite.ui import launch_web_ui
        launch_web_ui(
            data_dir=data_dir,
            host="127.0.0.1",
            port=port,
            no_browser=no_browser,
            data_dir_locked=ctx.obj["data_dir_locked"],
        )
    else:
        from trace_lite.visualizer import render_terminal_visualizer
        render_terminal_visualizer(db)


@main.command("ui")
@click.option("--port", default=8420, type=int, help="Web server port number")
@click.option("--no-browser", is_flag=True, help="Do not open a browser")
@click.pass_context
def ui(ctx, port, no_browser):
    """Open the loopback workspace (use --no-browser for server mode)."""
    from trace_lite.ui import launch_web_ui
    launch_web_ui(
        data_dir=ctx.obj["data_dir"],
        host="127.0.0.1",
        port=port,
        no_browser=no_browser,
        data_dir_locked=ctx.obj["data_dir_locked"],
    )


@main.command("watch")
@click.argument("directory", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option("--poll-interval", default=2.0, type=float, help="Polling interval in seconds")
@click.option("--consolidate/--no-consolidate", default=False, help="Organize summary trees after changes")
@click.pass_context
def watch(ctx, directory, poll_interval, consolidate):
    """Watch a directory (e.g. Obsidian Vault) for markdown file changes and auto-ingest notes."""
    import time
    from trace_lite.watcher import VaultWatcher

    vault_path = Path(directory).resolve()
    data_dir = _require_data_dir(ctx)
    db = TraceLite(data_dir)
    db.start_model_warmup()

    click.echo(f"Watching directory '{vault_path}' for markdown changes...")
    click.echo(f"Data directory: {data_dir}")
    click.echo("Press Ctrl+C to stop watching.\n")

    watcher = VaultWatcher(
        vault_path=vault_path,
        db=db,
        poll_interval=poll_interval,
        auto_consolidate=consolidate,
    )

    res = watcher.scan_once()
    click.echo(_success_message(
        f"Initial scan complete: {res['files_scanned']} file(s) scanned, "
        f"{res['files_ingested']} note(s) ingested ({res['atoms_ingested']} atoms)."
    ))

    watcher.start()

    try:
        while watcher.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("\nStopping watcher...")
    finally:
        watcher.stop()
        click.echo(_success_message("Watcher stopped."))


@main.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell"]))
def completion(shell):
    """Generate shell autocompletion configuration instructions."""
    click.echo(f"=== Shell Completion Setup for {shell} ===")
    if shell == "bash":
        click.echo('Add the following line to your ~/.bashrc file:\n')
        click.echo('eval "$(_TL_COMPLETE=bash_source tl)"')
    elif shell == "zsh":
        click.echo('Add the following line to your ~/.zshrc file:\n')
        click.echo('eval "$(_TL_COMPLETE=zsh_source tl)"')
    elif shell == "fish":
        click.echo('Save the following output to ~/.config/fish/completions/tl.fish:\n')
        click.echo('_TL_COMPLETE=fish_source tl | source')
    elif shell == "powershell":
        click.echo('Add the following to your PowerShell $PROFILE:\n')
        click.echo('Register-ArgumentCompleter -Native -CommandName tl -ScriptBlock { ... }')


@main.command("benchmark")
@click.option("--fixture", "-f", required=True, type=click.Path(exists=True, dir_okay=False), help="Path to benchmark JSON fixture/manifest.")
@click.option("--report-output", "-o", default=None, type=click.Path(dir_okay=False), help="Path to save output JSON benchmark report.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output machine-readable JSON report.")
@click.option("--mode", "-m", default="hybrid", type=click.Choice(["hybrid", "tree", "flat", "lexical"]), help="Retrieval mode.")
@click.option("--top-k", "-k", default=30, type=int, help="Top-K retrieval limit.")
def benchmark(fixture: str, report_output: str | None, output_json: bool, mode: str, top_k: int):
    """Run benchmark evaluation suite over a benchmark fixture."""
    from trace_lite.engines.benchmark import BenchmarkRunner
    from rich.console import Console

    console = Console(stderr=output_json)
    if not output_json:
        console.print(f"[bold cyan]Starting Trace-Lite Benchmark Runner[/bold cyan] (mode: {mode})...")

    runner = BenchmarkRunner()
    try:
        report = runner.run(
            fixture=fixture,
            mode=mode,
            top_k=top_k,
        )
    except Exception as exc:
        if output_json:
            click.echo(json.dumps({"error": str(exc)}), err=True)
        else:
            console.print(f"[bold red]Benchmark run failed:[/bold red] {exc}")
        sys.exit(1)

    if report_output:
        saved_path = report.save_json(report_output)
        if not output_json:
            console.print(f"[bold green][OK] Saved benchmark report to:[/bold green] {saved_path}")

    if output_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        report.render_console(console)


if __name__ == "__main__":
    main()

