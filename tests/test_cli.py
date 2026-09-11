"""F5: tl ingest / tl status wired to live storage (no stubs)."""

from typer.testing import CliRunner

from trace_lite.cli import app

runner = CliRunner()


def test_ingest_file_and_live_status(tmp_path):
    db = tmp_path / "cli.db"
    note = tmp_path / "hello.md"
    note.write_text("# Hello\n\nNote about #cli ingest flow.\n")
    result = runner.invoke(app, ["ingest", str(note), "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "atom" in result.output
    status = runner.invoke(app, ["status", "--db", str(db)])
    assert status.exit_code == 0, status.output
    assert "atoms=1" in status.output and "journal=wal" in status.output


def test_ingest_vault_dir_and_missing_path(tmp_path):
    db = tmp_path / "vault.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n")
    (vault / "b.md").write_text("# B\n")
    result = runner.invoke(app, ["ingest", str(vault), "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "synced 2 notes" in result.output
    missing = runner.invoke(app, ["ingest", str(tmp_path / "nope.md"), "--db", str(db)])
    assert missing.exit_code != 0
    no_db = runner.invoke(app, ["status", "--db", str(tmp_path / "absent.db")])
    assert no_db.exit_code != 0 and "no database" in no_db.output
