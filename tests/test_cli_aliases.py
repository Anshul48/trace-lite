"""Tests for trace-lite CLI command aliases (tracel, tl)."""

from click.testing import CliRunner
from trace_lite.cli import main


def test_cli_main_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "trace-lite (aliases: tracel, tl)" in result.output
    assert "chat" in result.output
    assert "project" in result.output
    assert "config" in result.output
    assert "  projects" not in result.output
    assert "  configure" not in result.output


def test_removed_aliases_and_config_set_are_rejected():
    runner = CliRunner()
    for command in ("configure", "projects", "use", "delete", "providers", "search", "ls-trees", "web", "serve"):
        assert runner.invoke(main, [command]).exit_code == 2
    assert runner.invoke(main, ["config", "set"]).exit_code == 2


def test_cli_subcommands_invoke(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["--data-dir", str(tmp_path / "db"), "status"])
    assert result.exit_code == 0
    assert "trace-lite Status" in result.output or "Data Dir:" in result.output
