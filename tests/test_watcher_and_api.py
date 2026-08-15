"""Tests for Vault Watcher and Extended REST API endpoints."""

import time
from pathlib import Path
import pytest
from click.testing import CliRunner

from trace_lite.db import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.cortex import MockVectorStore
from trace_lite.watcher import VaultWatcher, sync_vault, is_markdown_file, is_hidden_path
from trace_lite.cli import main
from trace_lite.visualizer.serializers import serialize_dag


def test_markdown_and_hidden_path_helpers(tmp_path):
    md_file = tmp_path / "note.md"
    md_file.write_text("Hello", encoding="utf-8")
    txt_file = tmp_path / "note.txt"
    txt_file.write_text("Hello", encoding="utf-8")

    assert is_markdown_file(md_file) is True
    assert is_markdown_file(txt_file) is False

    hidden_rel = Path(".obsidian/plugins/config.json")
    visible_rel = Path("folder/subfolder/note.md")
    assert is_hidden_path(hidden_rel) is True
    assert is_hidden_path(visible_rel) is False


def test_vault_watcher_scan_and_rescan(tmp_path):
    db_dir = tmp_path / "db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    # Create notes
    note1 = vault_dir / "Note1.md"
    note1.write_text("Obsidian is a powerful knowledge base that uses local Markdown files.", encoding="utf-8")
    
    subfolder = vault_dir / "Subfolder"
    subfolder.mkdir()
    note2 = subfolder / "Note2.md"
    note2.write_text("TraceLite self-organizes markdown notes into hierarchical summary trees.", encoding="utf-8")

    hidden_folder = vault_dir / ".obsidian"
    hidden_folder.mkdir()
    hidden_file = hidden_folder / "app.json"
    hidden_file.write_text("{}", encoding="utf-8")

    db = TraceLite(db_dir)
    watcher = VaultWatcher(vault_path=vault_dir, db=db, auto_consolidate=True)

    # Initial scan
    res1 = watcher.scan_once()
    assert res1["status"] == "ok"
    assert res1["files_scanned"] == 2
    assert res1["files_ingested"] == 2
    assert res1["atoms_ingested"] > 0

    # Rescan without changes
    res2 = watcher.scan_once()
    assert res2["status"] == "ok"
    assert res2["files_scanned"] == 2
    assert res2["files_ingested"] == 0

    # Modify a file
    time.sleep(0.05)
    note1.write_text("Obsidian updated content with new facts about knowledge graphs.", encoding="utf-8")
    res3 = watcher.scan_once()
    assert res3["files_ingested"] == 1


def test_vault_watcher_background_start_stop(tmp_path):
    db_dir = tmp_path / "db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    db = TraceLite(db_dir)
    watcher = VaultWatcher(vault_path=vault_dir, db=db, poll_interval=0.1)

    assert watcher.is_running is False
    watcher.start()
    assert watcher.is_running is True

    # Add a file while watcher is running
    note = vault_dir / "Dynamic.md"
    note.write_text("Dynamic note added while background watcher thread is running.", encoding="utf-8")

    # Poll with timeout for background thread to complete ingestion
    for _ in range(50):
        if db.status().total_atoms > 0:
            break
        time.sleep(0.1)

    watcher.stop()
    assert watcher.is_running is False

    status = db.status()
    assert status.total_atoms > 0



def test_sync_vault_helper(tmp_path):
    db_dir = tmp_path / "db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    (vault_dir / "Topic.md").write_text("Graph databases model entities as nodes and relationships as edges.", encoding="utf-8")

    db = TraceLite(db_dir)
    res = sync_vault(vault_dir, db=db)
    assert res["status"] == "ok"
    assert res["files_ingested"] == 1


def test_cli_watch_command(tmp_path):
    runner = CliRunner()
    db_dir = tmp_path / "db"
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "CliNote.md").write_text("Testing CLI watch command integration.", encoding="utf-8")

    # Run cli watch with invalid path
    res_err = runner.invoke(main, ["--data-dir", str(db_dir), "watch", str(tmp_path / "nonexistent")])
    assert res_err.exit_code != 0

    # Run scan via watcher directly to ensure CLI integration syntax works
    res_ok = runner.invoke(main, ["--data-dir", str(db_dir), "status"])
    assert res_ok.exit_code == 0


def test_serialize_dag(tmp_path):
    db = TraceLite(
        tmp_path / "db",
        embedder=MockEmbedder(dim=16),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )
    res = db.ingest("Raptor trees recursively structure data for retrieval.", document_name="Doc1")
    db.consolidate()

    trees = db.trees()
    assert len(trees) > 0

    tree_id = trees[0].tree_id
    dag = serialize_dag(db, tree_id)

    assert dag["tree_id"] == tree_id
    assert "nodes" in dag
    assert "links" in dag
    assert len(dag["nodes"]) > 0


def test_api_v1_endpoints_and_cors(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from trace_lite.ui.server import create_app

    db_dir = tmp_path / "db"
    monkeypatch.setenv("TRACE_LITE_PROJECTS_CONFIG_PATH", str(tmp_path / "projects.json"))
    from trace_lite.projects import ProjectManager
    project_manager = ProjectManager()
    project_config = project_manager.load_config()
    project_config["projects"]["default"]["path"] = str((tmp_path / "default").resolve())
    project_manager.save_config(project_config)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "APINote.md").write_text("API endpoints for sync and DAG visualization.", encoding="utf-8")

    db = TraceLite(
        db_dir,
        embedder=MockEmbedder(dim=16),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )
    app = create_app(db_dir, db=db)
    client = TestClient(app)

    # 1. Test CORS Headers
    response_cors = client.options(
        "/api/status",
        headers={
            "Origin": "app://obsidian.md",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response_cors.status_code == 200
    assert response_cors.headers.get("access-control-allow-origin") == "app://obsidian.md"

    # 2. Test POST /api/v1/vault/sync
    sync_resp = client.post("/api/v1/vault/sync", json={"vault_path": str(vault_dir)})
    assert sync_resp.status_code == 200
    data = sync_resp.json()
    assert data["status"] == "ok"
    assert data["files_ingested"] == 1

    # Invalid vault path
    bad_sync = client.post("/api/v1/vault/sync", json={"vault_path": str(tmp_path / "invalid")})
    assert bad_sync.status_code == 400

    # 3. Source sync does not create a derived tree before organization.
    trees_resp = client.get("/api/trees")
    trees_data = trees_resp.json()["trees"]
    assert trees_data == []

    # 4. Explicit organization publishes a verified candidate, then DAG works.
    consolidate_resp = client.post("/api/consolidate", json={})
    assert consolidate_resp.status_code == 200
    cons_data = consolidate_resp.json()
    assert cons_data["status"] == "ok"
    assert "trees_updated" in cons_data
    assert "summaries_generated" in cons_data

    trees_data = client.get("/api/trees").json()["trees"]
    assert len(trees_data) > 0
    tree_id = trees_data[0]["tree_id"]

    dag_resp = client.get(f"/api/v1/trees/{tree_id}/dag")
    assert dag_resp.status_code == 200
    dag_data = dag_resp.json()
    assert dag_data["tree_id"] == tree_id
    assert "nodes" in dag_data
    assert "links" in dag_data

    # Invalid tree ID
    bad_dag = client.get("/api/v1/trees/nonexistent-tree-id/dag")
    assert bad_dag.status_code == 404

    # 5. Test Project Management Endpoints
    proj_list_resp = client.get("/api/projects")
    assert proj_list_resp.status_code == 200
    assert "active_project" in proj_list_resp.json()

    create_proj_resp = client.post(
        "/api/projects/create",
        json={
            "name": "test-api-proj",
            "path": str(tmp_path / "test-api-proj"),
            "description": "API created project",
        },
    )
    assert create_proj_resp.status_code == 200
    assert create_proj_resp.json()["project"]["name"] == "test-api-proj"

    switch_proj_resp = client.post("/api/projects/switch", json={"name": "default"})
    assert switch_proj_resp.status_code == 200

    del_proj_resp = client.delete("/api/projects/test-api-proj")
    assert del_proj_resp.status_code == 200, del_proj_resp.text

    # 6. Test Configuration Endpoints
    cfg_resp = client.get("/api/config")
    assert cfg_resp.status_code == 200
    assert "available_providers" in cfg_resp.json()

    save_cfg_resp = client.post("/api/config", json={"provider_id": "ollama", "model": "ollama/llama3.1:8b"})
    assert save_cfg_resp.status_code == 422

    # 7. Test Export Zip Endpoint
    export_resp = client.get("/api/export")
    assert export_resp.status_code == 200
    assert export_resp.headers.get("content-type") == "application/zip"


def test_api_empty_registry_is_usable_and_creates_first_project(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from trace_lite.projects import ProjectManager
    from trace_lite.ui.server import create_app

    monkeypatch.setenv("TRACE_LITE_PROJECTS_CONFIG_PATH", str(tmp_path / "projects.json"))
    pm = ProjectManager()
    pm.delete_project("default")

    app = create_app(None)
    client = TestClient(app)

    projects = client.get("/api/projects")
    assert projects.status_code == 200
    assert projects.json()["active_project"] is None
    assert projects.json()["projects"] == []

    workspace = client.get("/api/workspace")
    assert workspace.status_code == 200
    assert workspace.json()["project"] is None
    assert workspace.json()["next_action"] == "create_project"

    status = client.get("/api/status")
    assert status.status_code == 409
    assert "create one" in status.json()["detail"].lower()

    created = client.post(
        "/api/projects/create",
        json={"name": "first", "path": str(tmp_path / "first")},
    )
    assert created.status_code == 200
    assert client.get("/api/projects").json()["active_project"] == "first"


def test_benchmark_api_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from trace_lite.ui.server import create_app

    app = create_app(data_dir=tmp_path / "db")
    client = TestClient(app)

    # Test listing runs
    runs_res = client.get("/api/benchmarks/runs")
    assert runs_res.status_code == 200
    assert "runs" in runs_res.json()


