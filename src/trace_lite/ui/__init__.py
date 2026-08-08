"""UI module for trace-lite web visualizer."""

from pathlib import Path


def launch_web_ui(data_dir: str | Path, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Launch the trace-lite web visualizer server."""
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError(
            "\n\n"
            "┌─────────────────────────────────────────────────────────────┐\n"
            "│ Web Visualizer requires optional UI dependencies.           │\n"
            "│ Please install using:                                       │\n"
            "│   pip install \"trace-lite[ui]\"                              │\n"
            "└─────────────────────────────────────────────────────────────┘\n"
        ) from e

    from trace_lite.ui.server import create_app

    print(f"🚀 Starting trace-lite Web Visualizer at http://{host}:{port}")
    app = create_app(data_dir=data_dir)
    uvicorn.run(app, host=host, port=port)


__all__ = ["launch_web_ui"]
