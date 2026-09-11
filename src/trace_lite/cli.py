"""CLI entrypoint for trace-lite."""

import typer
from rich.console import Console

app = typer.Typer(help="trace-lite: single-node filing cabinet and local memory substrate")
console = Console()


@app.command()
def status(
    db: str = typer.Option("~/.trace-lite/storage.db", "--db", help="SQLite database path."),
):
    """Display live counters from the local filing cabinet."""
    from pathlib import Path

    from trace_lite.store import Database

    path = Path(db).expanduser()
    if not path.exists():
        console.print(f"[yellow]no database yet at {path}[/yellow]")
        raise typer.Exit(code=1)
    with Database(path) as database:
        atoms = database.count_atoms()
        facets = database.conn.execute("SELECT COUNT(*) FROM facets").fetchone()[0]
        events = database.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        journal = database.conn.execute("PRAGMA journal_mode").fetchone()[0]
        page_count = database.conn.execute("PRAGMA page_count").fetchone()[0]
        size_mb = path.stat().st_size / 1_048_576
    console.print(
        f"[bold green]trace-lite[/bold green] atoms={atoms} facets={facets} events={events} "
        f"size={size_mb:.1f}MB journal={journal} pages={page_count}"
    )


@app.command()
def ingest(
    path: str = typer.Argument(..., help="Markdown file or vault directory to ingest."),
    db: str = typer.Option("~/.trace-lite/storage.db", "--db", help="SQLite database path."),
):
    """Ingest a markdown note or a whole vault directory into the filing cabinet."""
    from pathlib import Path

    from trace_lite.api import ingest_note, sync_vault
    from trace_lite.filing import FilingEngine, Taxonomy
    from trace_lite.store import Database

    target = Path(path)
    if not target.exists():
        console.print(f"[red]path not found: {target}[/red]")
        raise typer.Exit(code=1)
    database = Database(Path(db).expanduser())
    try:
        taxonomy = Taxonomy(database.conn)
        engine = FilingEngine(database.conn, taxonomy)
        if target.is_dir():
            synced = sync_vault(database, taxonomy, engine, target)
            console.print(f"synced {synced} notes ({database.count_atoms()} atoms)")
        else:
            try:
                raw = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                console.print(f"[red]not UTF-8 markdown: {target}[/red]")
                raise typer.Exit(code=1)
            atom_id = ingest_note(
                database, taxonomy, engine, target.name,
                raw, event_type="note.ingested",
            )
            console.print(f"ingested {target} as atom {atom_id}")
    finally:
        database.close()


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
