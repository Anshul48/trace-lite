"""Interactive Rich TUI for trace-lite Project & Database Management Hub."""

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from trace_lite.projects import (
    NO_ACTIVE_PROJECT_MESSAGE,
    ProjectDeletionError,
    ProjectManager,
)
from trace_lite.tui_selectors import SELECT_CANCEL, SELECT_QUIT, select_option


_ACTION_OPEN = "open"
_ACTION_CREATE = "create"
_ACTION_DELETE = "delete"
_ACTION_REGISTER = "register"


def _select_project(projects: list[dict], active_name: str | None, message: str) -> str:
    """Select a project with the same arrow-key control as the action menu."""
    options = [
        (
            f"{project['name']}{' (active)' if project['name'] == active_name else ''}",
            project["name"],
        )
        for project in projects
    ]
    options.append(("Cancel", SELECT_CANCEL))
    selected = select_option(message, options, default=active_name)
    return selected if isinstance(selected, str) else SELECT_CANCEL


def run_projects_tui(config_path: Path | str | None = None) -> None:
    """Run interactive TUI dashboard for managing trace-lite projects/databases."""
    console = Console()
    pm = ProjectManager(config_path=config_path)

    while True:
        console.clear()

        header = Text()
        header.append("trace-lite ", style="bold cyan")
        header.append("Project & Database Hub\n", style="bold white")
        header.append(
            "Manage isolated databases, create new project stores, switch context, or register directories.",
            style="dim white",
        )
        console.print(Panel(header, border_style="cyan", expand=False))

        projects = pm.list_projects()
        active_name = pm.get_active_project_name()

        table = Table(
            title="Registered Database Projects",
            header_style="bold magenta",
            show_header=True,
            expand=False,
        )
        table.add_column("#", style="bold yellow", justify="right")
        table.add_column("Status", justify="center")
        table.add_column("Project Name", style="bold white")
        table.add_column("Atoms", justify="right", style="cyan")
        table.add_column("Trees", justify="right", style="green")
        table.add_column("Path", style="dim white")

        for index, project in enumerate(projects, 1):
            is_active = project["name"] == active_name
            status = "[bold green]* Active[/bold green]" if is_active else "[dim]-[/dim]"
            table.add_row(
                str(index),
                status,
                project["name"],
                f"{project['atom_count']:,}",
                f"{project['tree_count']}",
                project["path"],
            )

        console.print(table)
        console.print()

        action = select_option(
            "Select project action",
            [
                ("Open / Switch Active Project", _ACTION_OPEN),
                ("Create New Project", _ACTION_CREATE),
                ("Delete Project", _ACTION_DELETE),
                ("Register Existing Folder", _ACTION_REGISTER),
                ("Quit", SELECT_QUIT),
            ],
            default=_ACTION_OPEN if projects else _ACTION_CREATE,
        )

        if action in {SELECT_QUIT, SELECT_CANCEL}:
            console.print("[dim]Exiting Project Hub.[/dim]")
            break

        if action == _ACTION_OPEN:
            if not projects:
                console.print(
                    "[yellow]No projects are registered. Create one first.[/yellow]\n"
                )
                Confirm.ask("Press Enter to continue...", default=True)
                continue
            target_name = _select_project(projects, active_name, "Select project to open/switch")
            if target_name == SELECT_CANCEL:
                continue
            try:
                pm.switch_project(target_name)
                console.print(f"[bold green][OK] Active project is now '{target_name}'.[/bold green]\n")
            except KeyError:
                console.print(f"[bold red]Error: Project '{target_name}' not found.[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        elif action == _ACTION_CREATE:
            name = Prompt.ask("Enter new Project Name").strip()
            if not name:
                console.print("[bold red]Project name cannot be empty.[/bold red]\n")
                Confirm.ask("Press Enter to continue...", default=True)
                continue

            custom_path = Prompt.ask(
                "Storage path (leave blank for default ~/.trace_lite/dbs/<name>)", default=""
            ).strip()
            description = Prompt.ask("Description (optional)", default="").strip()

            try:
                info = pm.create_project(name=name, path=custom_path or None, description=description)
                console.print(
                    f"[bold green][OK] Created and switched to new project '{name}' at {info['path']}.[/bold green]\n"
                )
            except Exception as exc:
                console.print(f"[bold red]Error creating project: {exc}[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        elif action == _ACTION_DELETE:
            target_name = _select_project(projects, active_name, "Select project to delete")
            if target_name == SELECT_CANCEL:
                continue

            project_info = pm.get_project(target_name)
            if not project_info:
                console.print(f"[bold red]Error: Project '{target_name}' not found.[/bold red]\n")
                Confirm.ask("Press Enter to continue...", default=True)
                continue

            live_projects = pm.list_projects()
            matched = next((project for project in live_projects if project["name"] == target_name), None)
            atoms = matched["atom_count"] if matched else 0
            trees = matched["tree_count"] if matched else 0
            ownership = matched.get("ownership") if matched else project_info.get("ownership", "unowned")
            warning_text = (
                "[bold red]DANGER ZONE: PROJECT DELETION[/bold red]\n\n"
                f"Project Name: [bold white]{target_name}[/bold white]\n"
                f"Path:         [bold yellow]{project_info['path']}[/bold yellow]\n"
                f"Atoms:        [bold cyan]{atoms:,}[/bold cyan]\n"
                f"Trees:        [bold green]{trees}[/bold green]\n\n"
                + (
                    "[bold red]This will permanently delete the managed database files on disk![/bold red]"
                    if ownership == "managed"
                    else "The registered external folder will be retained by default."
                )
            )
            console.print()
            console.print(Panel(warning_text, border_style="red", title="Confirm Deletion"))

            # Keep the existing destructive typed-name confirmation unchanged.
            typed_name = Prompt.ask(
                f"[bold red]To confirm deletion, type exact project name '{target_name}'[/bold red]"
            ).strip()
            if typed_name == target_name:
                purge_external = False
                if ownership == "external":
                    purge_external = Confirm.ask(
                        "Also permanently remove the external folder?", default=False
                    )
                try:
                    pm.delete_project(
                        target_name,
                        delete_files=True,
                        purge_external=purge_external,
                    )
                    console.print(
                        f"[bold red][OK] Project '{target_name}' deleted successfully.[/bold red]\n"
                    )
                    if pm.get_active_project_name() is None:
                        console.print(f"[bold yellow]{NO_ACTIVE_PROJECT_MESSAGE}[/bold yellow]")
                except ProjectDeletionError as exc:
                    console.print(f"[bold red]Error deleting project: {exc}[/bold red]\n")
            else:
                console.print("[yellow]Deletion cancelled. Project name did not match.[/yellow]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        elif action == _ACTION_REGISTER:
            name = Prompt.ask("Enter name for the existing database project").strip()
            folder_path = Prompt.ask("Enter directory path").strip()
            try:
                info = pm.register_project(name=name, path=folder_path)
                console.print(f"[bold green][OK] Registered project '{name}' -> {info['path']}.[/bold green]\n")
            except Exception as exc:
                console.print(f"[bold red]Error registering project: {exc}[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        else:
            console.print("[bold red]Invalid option selection.[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)
