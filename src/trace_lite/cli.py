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


@app.command()
def serve(
    port: int = typer.Option(8420, "--port", envvar="TRACE_LITE_PORT", help="Loopback port."),
    db: str = typer.Option("~/.trace-lite/storage.db", "--db", help="SQLite database path."),
    vault: str | None = typer.Option(None, "--vault", help="Obsidian vault directory."),
):
    """Start the loopback REST daemon consumed by the Obsidian plugin."""
    import uvicorn

    from trace_lite.api import create_app

    console.print(f"[bold green]trace-lite[/bold green] serving on 127.0.0.1:{port}")
    uvicorn.run(create_app(db, vault), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
