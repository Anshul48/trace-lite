"""Tests for modernized CLI subcommands (tl project, tl config, --json flags, aliases)."""

import json
from click.testing import CliRunner
from trace_lite.cli import main
from trace_lite import providers


def parse_json(output: str):
    """Extract JSON object or array from runner output."""
    for i in range(len(output)):
        if output[i] in ('{', '['):
            try:
                return json.loads(output[i:])
            except Exception:
                continue
    return json.loads(output)


def test_cli_project_subcommands(tmp_path, monkeypatch):
    runner = CliRunner()
    db_dir = tmp_path / "db"
    monkeypatch.setenv("TRACE_LITE_PROJECTS_CONFIG_PATH", str(tmp_path / "projects.json"))

    # 1. Project list with --json
    res_list = runner.invoke(main, ["--data-dir", str(db_dir), "project", "list", "--json"])
    assert res_list.exit_code == 0
    projects = parse_json(res_list.output)
    assert isinstance(projects, list)

    # 2. Project create
    res_create = runner.invoke(
        main,
        [
            "--data-dir", str(db_dir), "project", "create", "mod-proj-test",
            "--path", str(tmp_path / "mod-proj-test"),
            "--description", "Modernized CLI test",
        ]
    )
    assert res_create.exit_code == 0
    assert "✓ Created and switched to project 'mod-proj-test'" in res_create.output

    # 3. Project current with --json
    res_curr = runner.invoke(main, ["--data-dir", str(db_dir), "project", "current", "--json"])
    assert res_curr.exit_code == 0
    curr_info = parse_json(res_curr.output)
    assert curr_info["name"] == "mod-proj-test"

    # 4. Project switch (use)
    res_switch = runner.invoke(main, ["--data-dir", str(db_dir), "project", "use", "default"])
    assert res_switch.exit_code == 0
    assert "✓ Switched active project to 'default'" in res_switch.output

    # 5. Project delete
    res_del = runner.invoke(main, ["--data-dir", str(db_dir), "project", "delete", "mod-proj-test", "--force"])
    assert res_del.exit_code == 0
    assert "✓ Project 'mod-proj-test' deleted" in res_del.output


def test_cli_config_subcommands(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("TRACE_LITE_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(providers, "_verify_runtime", lambda *_args: (True, "Provider verified."))

    # 1. Config list with --json
    res_list = runner.invoke(main, ["config", "list", "--json"])
    assert res_list.exit_code == 0
    cfg_data = parse_json(res_list.output)
    assert "providers" in cfg_data

    # 2. Config get with --json
    res_get = runner.invoke(main, ["config", "get", "--json"])
    assert res_get.exit_code == 0
    active_data = parse_json(res_get.output)
    assert "active_provider" in active_data

    # Secrets are intentionally never accepted in process arguments.
    res_set = runner.invoke(main, ["config", "set", "-p", "ollama"])
    assert res_set.exit_code == 2


def test_cli_json_flags(tmp_path):
    runner = CliRunner()
    db_dir = tmp_path / "db"

    # Status --json
    res_stat = runner.invoke(main, ["--data-dir", str(db_dir), "status", "--json"])
    assert res_stat.exit_code == 0
    stat_data = parse_json(res_stat.output)
    assert "total_atoms" in stat_data

    # Trees --json
    res_trees = runner.invoke(main, ["--data-dir", str(db_dir), "trees", "--json"])
    assert res_trees.exit_code == 0
    trees_data = parse_json(res_trees.output)
    assert isinstance(trees_data, list)

    # Ingest text & query --json
    runner.invoke(main, ["--data-dir", str(db_dir), "ingest", "LATTICE traversal routes search requests efficiently."])
    res_query = runner.invoke(main, ["--data-dir", str(db_dir), "query", "LATTICE", "--json"])
    assert res_query.exit_code != 0
    assert "pending organization" in res_query.output
