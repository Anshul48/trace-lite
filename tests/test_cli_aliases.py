"""Tests for trace-lite CLI command aliases (tracel, tl)."""

from click.testing import CliRunner
from trace_lite.cli import main


def test_cli_main_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "trace-lite (aliases: tracel, tl)" in result.output
    assert "chat" in result.output
    assert "projects" in result.output
    assert "configure" in result.output


def test_cli_subcommands_invoke():
    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "trace-lite Status" in result.output or "Data Dir:" in result.output
