"""Rich-based terminal visualizer for trace-lite summary trees and database stats."""

from typing import TYPE_CHECKING
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree as RichTree
from rich.text import Text
from rich.table import Table

if TYPE_CHECKING:
    from trace_lite.db import TraceLite


def render_terminal_visualizer(db: "TraceLite") -> None:
    """Render full interactive/rich visual status of trace-lite in terminal."""
    console = Console()

    # 1. Header & Status Panel
    s = db.status()
    header = Text()
    header.append("trace-lite ", style="bold cyan")
    header.append("Hierarchical Database Visualizer\n", style="bold white")
    header.append(f"Storage Dir: {db.data_dir}  |  Embedding: {db.config.embedding_model}", style="dim white")

    console.print(Panel(header, border_style="cyan", expand=False))

    stats_table = Table(show_header=True, header_style="bold magenta", expand=False)
    stats_table.add_column("Total Atoms", justify="center", style="bold yellow")
    stats_table.add_column("Forest Trees", justify="center", style="bold green")
    stats_table.add_column("Active / Total Nodes", justify="center", style="bold cyan")
    stats_table.add_column("Token Estimate", justify="center", style="dim white")

    stats_table.add_row(
        f"{s.total_atoms:,}",
        f"{s.total_trees}",
        f"{s.active_nodes:,} / {s.total_nodes:,}",
        f"~{s.estimated_tokens:,}",
    )
    console.print(stats_table)
    console.print()

    # 2. RAPTOR Summary Forest Unicode Trees
    trees = db.trees()
    if not trees:
        console.print("[dim yellow]No trees exist in the forest yet. Ingest documents to populate.[/dim yellow]\n")
        return

    console.print(f"[bold white]=== RAPTOR Summary Forest ({len(trees)} Trees) ===[/bold white]\n")

    for tree in trees:
        root_tree = RichTree(
            f"[bold green]🌲 Tree ID: [{tree.tree_id[:8]}][/bold green] [bold white]{tree.name}[/bold white] "
            f"[dim](Leaves: {tree.leaf_count}, Depth: {tree.depth})[/dim]"
        )
        if tree.description:
            root_tree.add(f"[dim italic]Description: {tree.description}[/dim italic]")

        nodes = db.forest.get_tree_nodes(tree.tree_id)
        # Group nodes by level descending
        level_map: dict[int, list] = {}
        for n in nodes:
            level_map.setdefault(n.level, []).append(n)

        # Render summary levels (Level > 0)
        sorted_levels = sorted(level_map.keys(), reverse=True)
        for lvl in sorted_levels:
            if lvl == 0:
                continue
            lvl_branch = root_tree.add(f"[bold yellow]📦 Summary Level [{lvl}][/bold yellow]")
            for node in level_map[lvl]:
                energy = db.energy.compute_activation(
                    last_accessed=node.last_accessed, access_count=node.access_count
                )
                energy_style = "green" if energy > 0.7 else ("yellow" if energy > 0.3 else "red")
                
                node_label = (
                    f"[bold white]Node [{node.node_id[:8]}][/bold white] "
                    f"Energy: [{energy_style}]{energy:.2f}[/{energy_style}] "
                    f"\"{node.summary_text[:100]}\""
                )
                sub_branch = lvl_branch.add(node_label)
                if node.atom_ids:
                    sub_branch.add(f"[dim cyan]Linked Atoms: {len(node.atom_ids)} atom(s)[/dim cyan]")

        # Render L0 Raw Atoms Summary
        if 0 in level_map:
            l0_nodes = level_map[0]
            l0_branch = root_tree.add(f"[bold blue]📄 Level [0] Base Atoms ({len(l0_nodes)} total)[/bold blue]")
            for atom_node in l0_nodes[:5]:  # Show first 5 atoms
                l0_branch.add(f"[dim white]📄 [{atom_node.node_id[:8]}] \"{atom_node.summary_text[:80]}\"[/dim white]")
            if len(l0_nodes) > 5:
                l0_branch.add(f"[dim gray]... and {len(l0_nodes) - 5} more atoms[/dim gray]")

        console.print(root_tree)
        console.print()
