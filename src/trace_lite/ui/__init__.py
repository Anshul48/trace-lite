"""UI module for the trace-lite web workspace."""

import webbrowser
import ipaddress
import logging
import sys
from pathlib import Path


class _RedactingFormatter(logging.Formatter):
    """Keep operational logs complete without printing secrets or payloads."""

    def format(self, record: logging.LogRecord) -> str:
        from trace_lite.adapters.llm import redact_diagnostic

        return redact_diagnostic(super().format(record))


def _configure_ui_logging() -> None:
    """Make ``tl ui`` operationally useful without provider transport noise."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    else:
        # Uvicorn or an embedding host may have installed handlers already.
        # Replace their formatters so propagated LiteLLM records receive the
        # same redaction guarantee as trace-lite's own logger.
        for handler in root.handlers:
            handler.setFormatter(_RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    for name in ("trace_lite", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.INFO)
    for name in (
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
        "litellm",
        "httpcore",
        "httpx",
        "fastapi",
        "starlette",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def launch_web_ui(
    data_dir: str | Path,
    host: str = "127.0.0.1",
    port: int = 8420,
    *,
    no_browser: bool = False,
    data_dir_locked: bool = False,
) -> None:
    """Launch the trace-lite web workspace server."""
    normalized = host.strip().lower()
    try:
        loopback = normalized in {"localhost", "localhost.localdomain"} or ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError("The trace-lite workspace must bind to a loopback address.")
    _configure_ui_logging()
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            'Web Visualizer requires optional UI dependencies. '
            'Install with: pip install "trace-lite[ui]"'
        ) from exc

    from trace_lite.ui.server import create_app

    url = f"http://{host}:{port}"
    print(f"Starting trace-lite workspace at {url}")
    if not no_browser:
        webbrowser.open(url)
    app = create_app(
        data_dir=data_dir,
        host=host,
        data_dir_locked=data_dir_locked,
        start_model_warmup=True,
    )
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=True)


__all__ = ["launch_web_ui"]
