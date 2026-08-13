"""Interactive Chat REPL for source capture and explicitly verified retrieval."""

import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text

from trace_lite.db import TraceLite
from trace_lite.projects import (
    NO_ACTIVE_PROJECT_MESSAGE,
    ProjectDeletionError,
    ProjectManager,
)


def print_help_panel(console: Console) -> None:
    """Render help menu for chat REPL slash commands."""
    table = Table(
        title="Interactive Chat Slash Commands",
        header_style="bold magenta",
        show_header=True,
        expand=False,
    )
    table.add_column("Command", style="bold cyan")
    table.add_column("Parameters", style="yellow")
    table.add_column("Description", style="dim white")

    table.add_row("/projects, /list", "", "List all registered database projects and stats")
    table.add_row("/use, /switch", "<db_name>", "Switch active database instantly")
    table.add_row("/create, /new", "<db_name>", "Create a new isolated database and switch to it")
    table.add_row("/delete", "<db_name>", "Delete a database (with safety confirmation)")
    table.add_row("/ingest", "<file_path | text>", "Add a source; it is queued durably for organization")
    table.add_row("/organize", "", "Preflight provider and build staged structure safely")
    table.add_row("/consolidate", "[tree_id]", "Explicitly rebuild RAPTOR summary trees")
    table.add_row("/status", "", "View source, organization, and tree state")
    table.add_row("/mode", "<hybrid | tree | flat>", "Change retrieval search mode")
    table.add_row("/help", "", "Display this help panel")
    table.add_row("/exit, /quit", "", "Exit chat REPL session")

    console.print(table)
    console.print()


def print_chat_error(console: Console, operation: str, error: Exception, db: TraceLite) -> None:
    """Show an operation error with bounded, redacted provider diagnostics."""
    console.print(Text(f"{operation} Error: {error}", style="bold red"))
    snapshot = getattr(db.llm, "debug_snapshot", None)
    if callable(snapshot):
        console.print(
            Panel(
                Text(snapshot(error)),
                title="Provider failure diagnostics",
                border_style="red",
            )
        )


def run_chat_tui(data_dir: Path | str | None = None, config_path: Path | str | None = None) -> None:
    """Run interactive CLI chat session connected to active trace-lite database."""
    console = Console()
    pm = ProjectManager(config_path=config_path)

    active_name = pm.get_active_project_name()
    target_dir = Path(data_dir) if data_dir else (
        pm.get_active_project_path() if active_name is not None else None
    )

    db = TraceLite(target_dir) if target_dir is not None else None
    if db is not None:
        db.start_model_warmup()
    current_mode = "hybrid"

    def require_db() -> TraceLite | None:
        if db is None:
            console.print(f"[bold yellow]{NO_ACTIVE_PROJECT_MESSAGE}[/bold yellow]")
            return None
        return db

    # Header
    console.clear()
    header = Text()
    header.append("trace-lite ", style="bold cyan")
    header.append("Interactive Chat REPL\n", style="bold white")
    header.append(
        f"Active DB: [bold yellow]{active_name or 'none'}[/bold yellow]  |  "
        f"Model: [bold green]{db.config.llm_model if db else 'create a project first'}[/bold green]  |  "
        f"Mode: [bold cyan]{current_mode}[/bold cyan]\n"
        "Type your question to query the database, or use slash commands (e.g. /help, /switch, /create).",
        style="dim white",
    )
    console.print(Panel(header, border_style="cyan", expand=False))

    while True:
        try:
            user_input = Prompt.ask(f"\n[bold green]You ({active_name or 'no project'})[/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Exiting chat REPL.[/dim]")
            break

        if not user_input:
            continue

        # Handle Slash Commands
        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ["/exit", "/quit"]:
                console.print("[dim]Exiting chat session.[/dim]")
                break

            elif cmd in ["/help"]:
                print_help_panel(console)

            elif cmd in ["/projects", "/project", "/list"]:
                projects = pm.list_projects()
                table = Table(title="Available Projects", header_style="bold magenta")
                table.add_column("Status", justify="center")
                table.add_column("Name", style="bold white")
                table.add_column("Atoms", justify="right", style="cyan")
                table.add_column("Trees", justify="right", style="green")
                table.add_column("Path", style="dim white")

                for p in projects:
                    status = "[bold green]★ Active[/bold green]" if p["name"] == active_name else "-"
                    table.add_row(status, p["name"], f"{p['atom_count']:,}", str(p["tree_count"]), p["path"])
                console.print(table)
                if not projects:
                    console.print(f"[yellow]{NO_ACTIVE_PROJECT_MESSAGE}[/yellow]")

            elif cmd in ["/use", "/switch"]:
                if not arg:
                    console.print("[bold red]Usage: /switch <db_name>[/bold red]")
                    continue
                try:
                    pm.switch_project(arg)
                    active_name = arg
                    target_dir = pm.get_active_project_path()
                    db = TraceLite(target_dir)
                    db.start_model_warmup()
                    console.print(f"[bold green]✓ Switched active database to '{active_name}' ({target_dir})[/bold green]")
                except KeyError:
                    console.print(f"[bold red]Database project '{arg}' not found. Use /projects to list.[/bold red]")

            elif cmd in ["/create", "/new"]:
                if not arg:
                    console.print("[bold red]Usage: /create <db_name>[/bold red]")
                    continue
                try:
                    info = pm.create_project(name=arg)
                    active_name = arg
                    target_dir = Path(info["path"])
                    db = TraceLite(target_dir)
                    db.start_model_warmup()
                    console.print(f"[bold green]✓ Created and switched to new database '{active_name}' at {target_dir}[/bold green]")
                except Exception as e:
                    console.print(f"[bold red]Error creating database: {e}[/bold red]")

            elif cmd in ["/delete"]:
                if not arg:
                    console.print("[bold red]Usage: /delete <db_name>[/bold red]")
                    continue
                proj_info = pm.get_project(arg)
                if not proj_info:
                    console.print(f"[bold red]Database project '{arg}' not found.[/bold red]")
                    continue

                if Confirm.ask(f"Are you sure you want to delete project '{arg}' and its data?"):
                    purge_external = (
                        proj_info.get("ownership") == "external"
                        and Confirm.ask("Also permanently remove the external folder?", default=False)
                    )
                    try:
                        pm.delete_project(
                            arg,
                            delete_files=True,
                            purge_external=purge_external,
                        )
                    except ProjectDeletionError as exc:
                        console.print(f"[bold red]Error deleting project: {exc}[/bold red]")
                        continue
                    console.print(f"[bold red]✓ Deleted project '{arg}'.[/bold red]")
                    if arg == active_name:
                        active_name = pm.get_active_project_name()
                        if active_name is None:
                            target_dir = None
                            db = None
                            console.print(f"[bold yellow]{NO_ACTIVE_PROJECT_MESSAGE}[/bold yellow]")
                        else:
                            target_dir = pm.get_active_project_path()
                            db = TraceLite(target_dir)
                            db.start_model_warmup()
                            console.print(f"[bold yellow]Switched context to '{active_name}'.[/bold yellow]")

            elif cmd in ["/ingest"]:
                if not arg:
                    console.print("[bold red]Usage: /ingest <file_path | raw text>[/bold red]")
                    continue

                input_path = Path(arg)
                db_for_command = require_db()
                if db_for_command is None:
                    continue
                if input_path.exists() and input_path.is_file():
                    res = db_for_command.ingest_file(input_path)
                    console.print(f"[bold green]Added '{input_path.name}' ({res.atom_count} atoms queued for organization).[/bold green]")
                else:
                    res = db_for_command.ingest(arg, document_name="Chat Input")
                    console.print(f"[bold green]Added raw text ({res.atom_count} atoms queued for organization).[/bold green]")

            elif cmd in ["/organize"]:
                db_for_command = require_db()
                if db_for_command is None:
                    continue
                try:
                    with console.status("[bold yellow]Organizing pending source content...[/bold yellow]"):
                        res = db_for_command.organize()
                except Exception as exc:
                    print_chat_error(console, "Organization", exc, db_for_command)
                    continue
                console.print(f"[bold green]Organized {res.trees_updated} tree(s); {res.pending_atoms} atom(s) remain queued.[/bold green]")

            elif cmd in ["/consolidate"]:
                db_for_command = require_db()
                if db_for_command is None:
                    continue
                try:
                    with console.status("[bold yellow]Consolidating RAPTOR summary trees...[/bold yellow]"):
                        res = db_for_command.consolidate(tree_id=arg if arg else None)
                except Exception as exc:
                    print_chat_error(console, "Consolidation", exc, db_for_command)
                    continue
                console.print(f"[bold green]Rebuilt {res.trees_updated} tree(s), generated {res.summaries_generated} summary node(s).[/bold green]")

            elif cmd in ["/status"]:
                db_for_command = require_db()
                if db_for_command is None:
                    continue
                s = db_for_command.status()
                console.print(Panel(
                    f"Database: [bold]{active_name}[/bold]\n"
                    f"Path:     [dim]{target_dir}[/dim]\n"
                    f"Atoms:    [bold cyan]{s.total_atoms:,}[/bold cyan]\n"
                    f"Pending:  [bold yellow]{s.pending_atoms:,}[/bold yellow] in {s.pending_trees} tree(s)\n"
                    f"Trees:    [bold green]{s.total_trees}[/bold green]\n"
                    f"Tokens:   ~[bold yellow]{s.estimated_tokens:,}[/bold yellow]\n"
                    f"Index:    [bold]{'verified' if s.index_trusted else 'untrusted'}[/bold]\n"
                    f"Credential:[bold] {s.credential_state}[/bold]",
                    title="Database Status",
                    border_style="cyan",
                ))

            elif cmd in ["/mode"]:
                if arg.lower() in ["hybrid", "tree", "flat"]:
                    current_mode = arg.lower()
                    console.print(f"[bold green]✓ Search mode set to '{current_mode}'.[/bold green]")
                else:
                    console.print("[bold red]Mode must be one of: hybrid, tree, flat[/bold red]")

            else:
                console.print(f"[bold red]Unknown command '{cmd}'. Type /help for assistance.[/bold red]")

            continue

        # Normal Query Input -> Execute LATTICE search & generation
        db_for_command = require_db()
        if db_for_command is None:
            continue
        with console.status(f"[bold cyan]Querying {active_name} (Mode: {current_mode})...[/bold cyan]"):
            try:
                res = db_for_command.query(user_input, top_k=5, mode=current_mode)
            except Exception as exc:
                print_chat_error(console, "Query", exc, db_for_command)
                continue
        console.print()
        console.print(f"[bold cyan]trace-lite ({active_name}) >[/bold cyan]")

        if not res.items:
            console.print("[dim yellow]No relevant evidence or matching content found in active database.[/dim yellow]")
            continue

        # Print retrieved evidence & sources nicely formatted
        console.print(f"Based on {len(res.items)} evidence atom(s) from [bold yellow]{active_name}[/bold yellow]:\n")

        for idx, item in enumerate(res.items[:3], 1):
            doc_name = item.source_artifact.document_name if item.source_artifact else "Ingested Text"
            content_snippet = item.atom.content.strip().replace("\n", " ")
            if len(content_snippet) > 200:
                content_snippet = content_snippet[:200] + "..."

            console.print(f"  📄 [{idx}] [bold white]{doc_name}[/bold white] (Score: [bold green]{item.score:.2f}[/bold green])")
            console.print(f"     \"{content_snippet}\"")
            if item.traversal_path:
                path_str = " -> ".join(item.traversal_path[:3])
                console.print(f"     [dim]Path: {path_str}[/dim]")
            console.print()
