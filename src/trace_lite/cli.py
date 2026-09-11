"""CLI entrypoint for trace-lite."""

import typer
from rich.console import Console

app = typer.Typer(help="trace-lite: single-node filing cabinet and local memory substrate")
console = Console()


@app.command()
def status():
    """Display status of the local filing cabinet."""
    console.print("[bold green]trace-lite[/bold green] v0.2.0: Ready for development.")


@app.command()
def version():
    """Show version."""
    console.print("trace-lite 0.2.0")


if __name__ == "__main__":
    app()
