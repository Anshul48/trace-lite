"""Tests for trace-lite ProjectManager, project switching, registration, and deletion."""

import pytest
from pathlib import Path
from trace_lite.projects import (
    NoActiveProjectError,
    ProjectConfigError,
    ProjectDeletionError,
    ProjectManager,
)
from trace_lite.db import TraceLite
from trace_lite.adapters import MockEmbedder, MockLLMAdapter
from trace_lite.cortex import MockVectorStore


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


def test_list_projects_reports_populated_tree_counts(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)
    project_dir = tmp_path / "populated"
    pm.create_project("populated", path=project_dir)
    db = TraceLite(
        project_dir,
        embedder=MockEmbedder(dim=32),
        llm=MockLLMAdapter(),
        vector_store=MockVectorStore(),
    )
    db.ingest(
        "RAPTOR creates summary trees for durable retrieval across documents. "
        "This source contains enough context to make a leaf atom.",
        document_name="Project metrics",
    )
    db.consolidate()

    project = next(p for p in pm.list_projects() if p["name"] == "populated")

    assert project["atom_count"] == db.status().total_atoms
    assert project["tree_count"] == db.status().total_trees
    assert project["tree_count"] == 1


def test_deleting_default_persists_empty_registry(temp_config_path):
    pm = ProjectManager(config_path=temp_config_path)

    pm.delete_project("default")

    assert pm.get_active_project_name() is None
    assert pm.list_projects() == []
    assert ProjectManager(config_path=temp_config_path).get_active_project_name() is None

    with pytest.raises(NoActiveProjectError, match="No active project"):
        pm.get_active_project_path()


def test_deleting_active_project_selects_existing_project(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)
    config = pm.load_config()
    config["projects"]["default"]["path"] = str((tmp_path / "default").resolve())
    pm.save_config(config)
    pm.create_project("alpha", path=tmp_path / "alpha")
    pm.create_project("beta", path=tmp_path / "beta")
    pm.switch_project("default")

    pm.delete_project("default")

    assert pm.get_active_project_name() == "alpha"
    assert not (tmp_path / "default").exists()


def test_empty_registry_can_create_project_again(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)
    pm.delete_project("default")

    info = pm.create_project("restored", path=tmp_path / "restored")

    assert info["ownership"] == "managed"
    assert pm.get_active_project_name() == "restored"
    assert pm.get_active_project_path() == (tmp_path / "restored").resolve()


def test_project_validation_rejects_duplicates_shared_paths_unsafe_and_nonempty(
    temp_config_path, tmp_path
):
    pm = ProjectManager(config_path=temp_config_path)
    with pytest.raises(ValueError, match="already registered"):
        pm.create_project("default")

    shared = tmp_path / "shared"
    pm.create_project("alpha", path=shared)
    with pytest.raises(ValueError, match="already registered"):
        pm.create_project("alpha", path=tmp_path / "other")
    with pytest.raises(ValueError, match="already registered"):
        pm.create_project("beta", path=shared)
    with pytest.raises(ValueError, match="simple name"):
        pm.create_project("../unsafe", path=tmp_path / "unsafe")

    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "existing.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="not empty"):
        pm.create_project("populated", path=populated)


def test_external_registration_is_retained_unless_explicitly_purged(temp_config_path, tmp_path):
    pm = ProjectManager(config_path=temp_config_path)
    external = tmp_path / "external"
    external.mkdir()
    marker = external / "marker.txt"
    marker.write_text("retain", encoding="utf-8")

    pm.register_project("external", external)
    pm.delete_project("external")
    assert marker.exists()

    pm.register_project("external-again", external)
    pm.delete_project("external-again", purge_external=True)
    assert not external.exists()


def test_purge_failure_keeps_registry_entry(temp_config_path, tmp_path, monkeypatch):
    pm = ProjectManager(config_path=temp_config_path)
    target = tmp_path / "managed"
    pm.create_project("managed", target)

    def fail_rmtree(_path):
        raise OSError("simulated purge failure")

    monkeypatch.setattr("trace_lite.projects.shutil.rmtree", fail_rmtree)
    with pytest.raises(ProjectDeletionError, match="remains registered"):
        pm.delete_project("managed")
    assert pm.get_project("managed") is not None


def test_malformed_registry_fails_closed(temp_config_path):
    temp_config_path.write_text("{not valid json", encoding="utf-8")
    pm = ProjectManager(config_path=temp_config_path)

    with pytest.raises(ProjectConfigError):
        pm.load_config()
