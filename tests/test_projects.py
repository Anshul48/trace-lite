"""Tests for trace-lite ProjectManager, project switching, registration, and deletion."""

import pytest
from pathlib import Path
from trace_lite.projects import ProjectManager
from trace_lite.db import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter


@pytest.fixture
def temp_config_path(tmp_path):
    """Fixture providing temporary projects.json path."""
    return tmp_path / "projects_test.json"


def test_project_manager_initialization(temp_config_path):
    pm = ProjectManager(config_path=temp_config_path)
    active = pm.get_active_project_name()
    assert active == "default"

    projects = pm.list_projects()
    assert len(projects) == 1
    assert projects[0]["name"] == "default"
    assert projects[0]["is_active"] is True


def test_create_and_switch_projects(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)

    p1_dir = tmp_path / "db1"
    info1 = pm.create_project("alpha", path=p1_dir, description="Alpha KB")
    assert info1["name"] == "alpha"
    assert pm.get_active_project_name() == "alpha"
    assert pm.get_active_project_path() == p1_dir.resolve()

    p2_dir = tmp_path / "db2"
    info2 = pm.create_project("beta", path=p2_dir, description="Beta KB")
    assert pm.get_active_project_name() == "beta"

    # Switch back to alpha
    pm.switch_project("alpha")
    assert pm.get_active_project_name() == "alpha"
    assert pm.get_active_project_path() == p1_dir.resolve()


def test_register_existing_project(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)
    existing_dir = tmp_path / "custom_existing"
    existing_dir.mkdir(parents=True, exist_ok=True)

    info = pm.register_project("custom", path=existing_dir, description="Pre-existing dir")
    assert info["name"] == "custom"

    projects = pm.list_projects()
    names = [p["name"] for p in projects]
    assert "custom" in names


def test_delete_project(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)

    dir1 = tmp_path / "to_delete"
    pm.create_project("proj_delete", path=dir1)
    assert dir1.exists()

    # Delete project and files
    success = pm.delete_project("proj_delete", delete_files=True)
    assert success is True
    assert not dir1.exists()

    # Verify project removed from registry
    projects = pm.list_projects()
    names = [p["name"] for p in projects]
    assert "proj_delete" not in names


def test_database_isolation(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)

    dir_a = tmp_path / "kb_a"
    dir_b = tmp_path / "kb_b"

    pm.create_project("kb_a", path=dir_a)
    embedder = MockEmbedder(dim=384)
    llm = MockLLMAdapter()

    db_a = TraceLite(dir_a, embedder=embedder, llm=llm)
    db_a.ingest("This is knowledge base A content.", document_name="Doc A")

    pm.create_project("kb_b", path=dir_b)
    db_b = TraceLite(dir_b, embedder=embedder, llm=llm)
    db_b.ingest("This is knowledge base B content.", document_name="Doc B")

    assert db_a.status().total_atoms > 0
    assert db_b.status().total_atoms > 0

    # Ensure isolated stats
    p_list = pm.list_projects()
    p_a = next(p for p in p_list if p["name"] == "kb_a")
    p_b = next(p for p in p_list if p["name"] == "kb_b")

    assert p_a["atom_count"] == db_a.status().total_atoms
    assert p_b["atom_count"] == db_b.status().total_atoms
