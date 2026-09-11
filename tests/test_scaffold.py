"""Basic scaffold verification test for trace-lite."""

from trace_lite import __version__
from trace_lite.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_version():
    assert __version__ == "0.2.0"


def test_cli_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.2.0" in result.stdout
