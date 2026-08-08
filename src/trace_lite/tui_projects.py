"""Interactive Rich TUI for trace-lite Project & Database Management Hub."""

from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.text import Text

from trace_lite.projects import ProjectManager


def run_projects_tui(config_path: Path | str | None = None) -> None:
    """Run interactive TUI dashboard for managing trace-lite projects/databases."""
    console = Console()
    pm = ProjectManager(config_path=config_path)

    while True:
        console.clear()
        
        # 1. Header Banner
        header = Text()
        header.append("trace-lite ", style="bold cyan")
        header.append("Project & Database Hub\n", style="bold white")
        header.append(
            "Manage isolated databases, create new project stores, switch context, or register directories.",
            style="dim white",
        )
        console.print(Panel(header, border_style="cyan", expand=False))

        # 2. Table of Projects
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

        for idx, p in enumerate(projects, 1):
            is_active = p["name"] == active_name
            status = "[bold green]★ Active[/bold green]" if is_active else "[dim]-[/dim]"
            
            table.add_row(
                str(idx),
                status,
                p["name"],
                f"{p['atom_count']:,}",
                f"{p['tree_count']}",
                p["path"],
            )

        console.print(table)
        console.print()

        # 3. Prompt Actions
        console.print("[bold cyan]Actions:[/bold cyan]")
        console.print("  [O] Open / Switch Active Project")
        console.print("  [C] Create New Project")
        console.print("  [D] Delete Project")
        console.print("  [R] Register Existing Folder")
        console.print("  [Q] Quit")
        console.print()

        choice = Prompt.ask(
            "[bold yellow]Select action [O / C / D / R / Q] or project number [1-N][/bold yellow]",
            default="Q",
        ).strip()

        if choice.upper() == "Q":
            console.print("[dim]Exiting Project Hub.[/dim]")
            break

        # Quick selection by row number
        if choice.isdigit() and 1 <= int(choice) <= len(projects):
            selected = projects[int(choice) - 1]
            pm.switch_project(selected["name"])
            console.print(f"[bold green]✓ Switched active project to '{selected['name']}'.[/bold green]\n")
            Confirm.ask("Press Enter to continue...", default=True)
            continue

        action = choice.upper()

        # Open / Switch
        if action == "O":
            target = Prompt.ask("Enter project number or name to open/switch").strip()
            if target.isdigit() and 1 <= int(target) <= len(projects):
                target_name = projects[int(target) - 1]["name"]
            else:
                target_name = target

            try:
                pm.switch_project(target_name)
                console.print(f"[bold green]✓ Active project is now '{target_name}'.[/bold green]\n")
            except KeyError:
                console.print(f"[bold red]Error: Project '{target_name}' not found.[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        # Create New Project
        elif action == "C":
            name = Prompt.ask("Enter new Project Name").strip()
            if not name:
                console.print("[bold red]Project name cannot be empty.[/bold red]\n")
                Confirm.ask("Press Enter to continue...", default=True)
                continue

            custom_path = Prompt.ask(
                "Storage path (leave blank for default ~/.trace_lite/dbs/<name>)", default=""
            ).strip()

            desc = Prompt.ask("Description (optional)", default="").strip()

            try:
                info = pm.create_project(name=name, path=custom_path or None, description=desc)
                console.print(f"[bold green]✓ Created and switched to new project '{name}' at {info['path']}.[/bold green]\n")
            except Exception as e:
                console.print(f"[bold red]Error creating project: {e}[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        # Delete Project (With High-Visibility Safety Guardrails)
        elif action == "D":
            target = Prompt.ask("Enter project number or name to delete").strip()
            if target.isdigit() and 1 <= int(target) <= len(projects):
                target_name = projects[int(target) - 1]["name"]
            else:
                target_name = target

            proj_info = pm.get_project(target_name)
            if not proj_info:
                console.print(f"[bold red]Error: Project '{target_name}' not found.[/bold red]\n")
                Confirm.ask("Press Enter to continue...", default=True)
                continue

            # Fetch statistics for warning panel
            live_list = pm.list_projects()
            matched = next((p for p in live_list if p["name"] == target_name), None)
            atoms = matched["atom_count"] if matched else 0
            trees = matched["tree_count"] if matched else 0

            warning_text = (
                f"[bold red]⚠️ DANGER ZONE: PROJECT DELETION ⚠️[/bold red]\n\n"
                f"Project Name: [bold white]{target_name}[/bold white]\n"
                f"Path:         [bold yellow]{proj_info['path']}[/bold yellow]\n"
                f"Atoms:        [bold cyan]{atoms:,}[/bold cyan]\n"
                f"Trees:        [bold green]{trees}[/bold green]\n\n"
                f"[bold red]This will permanently delete the database files on disk![/bold red]"
            )

            console.print()
            console.print(Panel(warning_text, border_style="red", title="Confirm Deletion"))

            typed_name = Prompt.ask(
                f"[bold red]To confirm deletion, type exact project name '{target_name}'[/bold red]"
            ).strip()

            if typed_name == target_name:
                pm.delete_project(target_name, delete_files=True)
                console.print(f"[bold red]✓ Project '{target_name}' deleted successfully.[/bold red]\n")
            else:
                console.print("[yellow]Deletion cancelled. Project name did not match.[/yellow]\n")

            Confirm.ask("Press Enter to continue...", default=True)

        # Register Existing Directory
        elif action == "R":
            name = Prompt.ask("Enter name for the existing database project").strip()
            folder_path = Prompt.ask("Enter directory path").strip()

            try:
                info = pm.register_project(name=name, path=folder_path)
                console.print(f"[bold green]✓ Registered project '{name}' -> {info['path']}.[/bold green]\n")
            except Exception as e:
                console.print(f"[bold red]Error registering project: {e}[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)

        else:
            console.print("[bold red]Invalid option selection.[/bold red]\n")
            Confirm.ask("Press Enter to continue...", default=True)
