"""Rich-based terminal visualizer for the actual RAPTOR tree topology."""

from typing import TYPE_CHECKING
import sys

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.tree import Tree as RichTree

if TYPE_CHECKING:
    from trace_lite.db import TraceLite
    from trace_lite.cortex import TreeNode


def _supports(console: Console, value: str) -> bool:
    encoding = getattr(console, "encoding", None) or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        value.encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def render_terminal_visualizer(db: "TraceLite") -> None:
    """Render the active forest recursively from each tree's root node."""
    console = Console()
    s = db.status()
    header = Text()
    header.append("trace-lite ", style="bold cyan")
    header.append("Hierarchical Database Visualizer\n", style="bold white")
    header.append(
        f"Storage Dir: {db.data_dir}  |  Embedding: {db.config.embedding_model}",
        style="dim white",
    )
    console.print(Panel(header, border_style="cyan", expand=False))

    stats = Table(show_header=True, header_style="bold magenta", expand=False)
    for column in ("Total Atoms", "Indexed Atoms", "Forest Trees", "Active / Total Nodes", "Approx. Tokens", "Index Health"):
        stats.add_column(column, justify="center")
    stats.add_row(
        f"{s.total_atoms:,}", f"{s.indexed_atoms:,}", f"{s.total_trees}",
        f"{s.active_nodes:,} / {s.total_nodes:,}", f"~{s.estimated_tokens:,}", s.validation_state,
    )
    console.print(stats)
    if s.validation_errors:
        console.print(f"[red]Index error: {escape(s.validation_errors[0])}[/red]")
    elif s.validation_warnings:
        console.print(f"[yellow]Index warning: {escape(s.validation_warnings[0])}[/yellow]")
    console.print()

    trees = db.trees()
    if not trees:
        console.print("[dim yellow]No trees exist in the forest yet. Ingest documents to populate.[/dim yellow]")
        return

    emoji_symbols = "\U0001F333\U0001F4C4"
    emoji = _supports(console, emoji_symbols)
    tree_symbol = "\U0001F333" if emoji else "TREE"
    leaf_symbol = "\U0001F4C4" if emoji else "LEAF"
    console.print(f"[bold white]=== RAPTOR Summary Forest ({len(trees)} Trees) ===[/bold white]\n")

    for tree in trees:
        root = db.forest.get_node(tree.root_node_id) if tree.root_node_id else None
        title = (
            f"[bold green]{tree_symbol}[/bold green] [bold white]{escape(tree.name)}[/bold white] "
            f"[dim](leaves: {tree.leaf_count}, depth: {tree.depth})[/dim]"
        )
        rich_tree = RichTree(title)
        if tree.description:
            rich_tree.add(f"[dim italic]Description: {escape(tree.description)}[/dim italic]")
        if root:
            _render_node(db, rich_tree, root, leaf_symbol, quality_verified=s.quality_verified)
        else:
            rich_tree.add("[yellow]No root node; index needs validation/rebuild.[/yellow]")
        console.print(rich_tree)
        console.print()


def _render_node(
    db: "TraceLite",
    branch: RichTree,
    node: "TreeNode",
    leaf_symbol: str,
    *,
    quality_verified: bool = False,
) -> None:
    summary = escape(node.summary_text or "(blank)")
    summary = summary[:180] + ("..." if len(summary) > 180 else "")
    energy = db.energy.compute_activation(node.last_accessed, node.access_count)
    if node.level == 0:
        label = (
            f"[blue]{leaf_symbol}[/blue] [dim]level 0[/dim] "
            f"[white]{escape(node.node_id[:12])}[/white] "
            f"[dim]source[/dim] [white]\\\"{summary}\\\"[/white]"
        )
    else:
        if quality_verified:
            status = "quality verified"
        elif node.summary_text and node.summary_text.strip():
            status = "structurally present / unverified"
        else:
            status = "BLANK"
        label = (
            f"[yellow]level {node.level}[/yellow] [white]{escape(node.node_id[:12])}[/white] "
            f"[dim]summary {escape(node.summary_provenance)} / {status} / energy {energy:.2f}[/dim] "
            f"[white]\\\"{summary}\\\"[/white]"
        )
    child_branch = branch.add(label)
    # The database's child links are the topology; ordering by level/node ID
    # keeps terminal output stable across rebuilds.
    children = sorted(db.forest.get_children(node.node_id), key=lambda child: (child.level, child.node_id))
    for child in children:
        _render_node(
            db,
            child_branch,
            child,
            leaf_symbol,
            quality_verified=quality_verified,
        )
