# Copyright 2026 trace-lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Project and database registry management for trace-lite.

The project registry is deliberately small, but it is still durable user
configuration.  In particular, an empty registry is a valid state: deleting
the final project must not cause a later read to recreate a project behind the
user's back.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any


MANAGED_OWNERSHIP = "managed"
EXTERNAL_OWNERSHIP = "external"
UNOWNED_OWNERSHIP = "unowned"

NO_ACTIVE_PROJECT_MESSAGE = (
    "No active project. Create one with 'tl project create <name>'."
)


class ProjectConfigError(RuntimeError):
    """Raised when the project registry cannot be read safely."""


class NoActiveProjectError(RuntimeError):
    """Raised when an operation requires a database but none is selected."""

    def __init__(self, message: str = NO_ACTIVE_PROJECT_MESSAGE):
        super().__init__(message)


class ProjectDeletionError(RuntimeError):
    """Raised when project storage could not be removed safely."""


def get_default_config_dir() -> Path:
    """Return central configuration directory ~/.trace_lite."""
    cfg_dir = Path.home() / ".trace_lite"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_projects_config_path(custom_path: Path | str | None = None) -> Path:
    """Return path to projects.json config file."""
    if custom_path is not None:
        p = Path(custom_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    # A process-local override keeps CLI/API tests and embedded applications
    # from touching the user's global registry while preserving the documented
    # global default for normal use.
    configured_path = os.environ.get("TRACE_LITE_PROJECTS_CONFIG_PATH")
    if configured_path:
        p = Path(configured_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return get_default_config_dir() / "projects.json"


class ProjectManager:
    """Manage the global project registry and isolated database stores."""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = get_projects_config_path(config_path)

    @staticmethod
    def _default_storage_path() -> Path:
        return (Path.home() / ".trace_lite" / "dbs" / "default").resolve()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _path_key(path: Path | str) -> str:
        """Return a comparison-safe absolute storage path key."""
        return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))

    @classmethod
    def _is_builtin_default_path(cls, path: Path | str) -> bool:
        return cls._path_key(path) == cls._path_key(cls._default_storage_path())

    @staticmethod
    def _validate_project_name(raw_name: str) -> str:
        if not isinstance(raw_name, str):
            raise ValueError("Project name must be text.")
        name = raw_name.strip()
        if not name:
            raise ValueError("Project name cannot be empty.")
        if (
            name in {".", ".."}
            or "/" in name
            or "\\" in name
            or ":" in name
            or Path(name).is_absolute()
            or PureWindowsPath(name).is_absolute()
            or PureWindowsPath(name).drive
            or any(ord(char) < 32 for char in name)
        ):
            raise ValueError("Project name must be a simple name, not a filesystem path.")
        return name

    @classmethod
    def _validate_config(cls, data: Any) -> dict[str, Any]:
        """Validate the registry shape without inventing missing projects."""
        if not isinstance(data, dict):
            raise ProjectConfigError("Project registry must contain a JSON object.")
        if "projects" not in data or not isinstance(data["projects"], dict):
            raise ProjectConfigError("Project registry is missing a valid 'projects' object.")

        for key, info in data["projects"].items():
            if not isinstance(key, str) or not key:
                raise ProjectConfigError("Project registry contains an invalid project name.")
            try:
                cls._validate_project_name(key)
            except ValueError as exc:
                raise ProjectConfigError(str(exc)) from exc
            if not isinstance(info, dict):
                raise ProjectConfigError(f"Project '{key}' has invalid metadata.")
            path = info.get("path")
            if not isinstance(path, str) or not path.strip():
                raise ProjectConfigError(f"Project '{key}' has no valid storage path.")
            record_name = info.get("name")
            if record_name is not None and record_name != key:
                raise ProjectConfigError(
                    f"Project registry key '{key}' does not match its metadata name."
                )

        active = data.get("active_project")
        if active is not None and not isinstance(active, str):
            raise ProjectConfigError("'active_project' must be a project name or null.")
        return data

    def _initial_config(self) -> dict[str, Any]:
        """Create the one built-in project only for a genuinely new registry."""
        now = self._now()
        default_path = str(self._default_storage_path())
        return {
            "active_project": "default",
            "projects": {
                "default": {
                    "name": "default",
                    "path": default_path,
                    "created_at": now,
                    "last_accessed": now,
                    "description": "Default local database",
                    "ownership": MANAGED_OWNERSHIP,
                    "managed": True,
                }
            },
        }

    def load_config(self) -> dict[str, Any]:
        """Load the registry, failing closed for malformed or unreadable files."""
        if not self.config_path.exists():
            config = self._initial_config()
            self.save_config(config)
            return config

        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            raise ProjectConfigError(
                f"Unable to read project registry '{self.config_path}': {exc}"
            ) from exc

        config = self._validate_config(data)
        # Older valid registries may omit the selection field.  Migrate only
        # the selection metadata; never add a synthetic project to a registry
        # that already exists, including an intentionally empty one.
        if "active_project" not in config:
            config["active_project"] = next(iter(config["projects"]), None)
            self.save_config(config)
        return config

    def save_config(self, data: dict[str, Any]) -> None:
        """Atomically save project configuration and never leave a torn JSON file."""
        self._validate_config(data)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.config_path.name}.",
                suffix=".tmp",
                dir=str(self.config_path.parent),
            )
            temporary_path = Path(temporary_name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.config_path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def get_active_project_name(self) -> str | None:
        """Return the selected project name, or ``None`` for an empty registry."""
        cfg = self.load_config()
        projects = cfg.get("projects", {})
        active = cfg.get("active_project")

        if active in projects:
            return active
        if projects:
            # Preserve the historical fallback when a legacy registry points
            # at a removed project, but persist the repaired selection.
            active = next(iter(projects))
        else:
            active = None

        if cfg.get("active_project") != active:
            cfg["active_project"] = active
            self.save_config(cfg)
        return active

    def get_active_project_path(self) -> Path:
        """Return the active store path, or raise a clear empty-state error."""
        name = self.get_active_project_name()
        if name is None:
            raise NoActiveProjectError()
        info = self.get_project(name)
        if not info or not info.get("path"):
            raise ProjectConfigError(f"Active project '{name}' has no storage path.")
        path = Path(info["path"]).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_project(self, name: str | None) -> dict[str, Any] | None:
        """Get metadata for a registered project."""
        if name is None:
            return None
        cfg = self.load_config()
        return cfg.get("projects", {}).get(name)

    @classmethod
    def _effective_ownership(cls, info: dict[str, Any]) -> str:
        ownership = info.get("ownership") or info.get("storage_ownership")
        if ownership in {MANAGED_OWNERSHIP, EXTERNAL_OWNERSHIP, UNOWNED_OWNERSHIP}:
            return ownership
        if isinstance(info.get("managed"), bool):
            return MANAGED_OWNERSHIP if info["managed"] else EXTERNAL_OWNERSHIP
        # The exact built-in path is the sole legacy exception.  All other
        # records without metadata are conservatively treated as unowned.
        if cls._is_builtin_default_path(info["path"]):
            return MANAGED_OWNERSHIP
        return UNOWNED_OWNERSHIP

    @classmethod
    def _assert_unique_path(
        cls, projects: dict[str, Any], target_path: Path, *, name: str | None = None
    ) -> None:
        target_key = cls._path_key(target_path)
        for existing_name, existing_info in projects.items():
            if name is not None and existing_name == name:
                continue
            try:
                existing_key = cls._path_key(existing_info["path"])
            except (KeyError, TypeError, ValueError):
                continue
            if existing_key == target_key:
                raise ValueError(
                    f"Storage path '{target_path}' is already registered by project "
                    f"'{existing_name}'."
                )

    @classmethod
    def _is_shared_path(
        cls, projects: dict[str, Any], target_path: Path, excluding: str
    ) -> bool:
        target_key = cls._path_key(target_path)
        return any(
            project_name != excluding
            and cls._path_key(project_info["path"]) == target_key
            for project_name, project_info in projects.items()
        )

    def list_projects(self) -> list[dict[str, Any]]:
        """List registered projects with live database statistics."""
        cfg = self.load_config()
        active_name = self.get_active_project_name()
        projects_dict = cfg.get("projects", {})
        results = []

        for project_name, project_info in projects_dict.items():
            project_path = Path(project_info["path"]).expanduser()
            is_active = project_name == active_name
            atom_count = 0
            tree_count = 0
            pending_atoms = 0
            pending_trees = 0
            orphaned_atoms = 0
            needs_organization = False
            estimated_tokens = 0
            exists = project_path.exists()

            try:
                from trace_lite.spine import SpineStore
                from trace_lite.cortex import ForestIndex

                spine = None
                forest = None
                spine_file = project_path / "spine.sqlite3"
                cortex_file = project_path / "cortex.sqlite3"
                if spine_file.exists():
                    spine = SpineStore(spine_file)
                    atom_count = spine.count_atoms()
                if cortex_file.exists():
                    forest = ForestIndex(cortex_file)
                    tree_count = len(forest.list_trees())
                    pending_atom_ids = forest.get_pending_atom_ids()
                    pending_atoms = len(pending_atom_ids)
                    pending_trees = len(forest.get_pending_tree_ids())
                    indexed_atom_ids = forest.get_indexed_atom_ids()
                    if spine is not None:
                        orphaned_atoms = sum(
                            1
                            for atom in spine.list_atoms()
                            if atom.atom_id not in indexed_atom_ids
                            and atom.atom_id not in pending_atom_ids
                        )
                    needs_organization = pending_atoms > 0 or orphaned_atoms > 0
                if spine is not None:
                    estimated_tokens = sum(
                        max(1, round(max(len(atom.content), len(atom.content.split()) * 4) / 4))
                        for atom in spine.list_atoms()
                    )
            except Exception:
                # A project listing should remain useful even when a derived
                # SQLite file is incomplete or from an older schema.
                pass

            results.append(
                {
                    "name": project_name,
                    "path": str(project_path.resolve(strict=False)),
                    "is_active": is_active,
                    "created_at": project_info.get("created_at", ""),
                    "last_accessed": project_info.get("last_accessed", ""),
                    "description": project_info.get("description", ""),
                    "ownership": self._effective_ownership(project_info),
                    "managed": self._effective_ownership(project_info) == MANAGED_OWNERSHIP,
                    "atom_count": atom_count,
                    "tree_count": tree_count,
                    "pending_atoms": pending_atoms,
                    "pending_trees": pending_trees,
                    "orphaned_atoms": orphaned_atoms,
                    "needs_organization": needs_organization,
                    "needs_recovery": orphaned_atoms > 0,
                    "estimated_tokens": estimated_tokens,
                    "exists_on_disk": exists,
                }
            )

        return results

    def _load_for_mutation(self) -> dict[str, Any]:
        cfg = self.load_config()
        if not isinstance(cfg.get("projects"), dict):
            # load_config validates this; retain a defensive guard for callers
            # that subclass or monkeypatch the manager.
            raise ProjectConfigError("Project registry has no valid projects object.")
        return cfg

    def create_project(
        self, name: str, path: str | Path | None = None, description: str = ""
    ) -> dict[str, Any]:
        """Create a new empty managed project and make it active."""
        name = self._validate_project_name(name)
        cfg = self._load_for_mutation()
        projects = cfg["projects"]
        if name in projects:
            raise ValueError(f"Project '{name}' is already registered.")

        target_path = (
            Path(path).expanduser()
            if path is not None and str(path).strip()
            else (Path.home() / ".trace_lite" / "dbs" / name)
        )
        compare_path = target_path.resolve(strict=False)
        self._assert_unique_path(projects, compare_path)

        if target_path.exists():
            if target_path.is_symlink() or not target_path.is_dir():
                raise ValueError(f"Project storage path is not a directory: {target_path}")
            try:
                has_contents = any(target_path.iterdir())
            except OSError as exc:
                raise ValueError(f"Unable to inspect project storage path: {target_path}") from exc
            if has_contents:
                raise ValueError(
                    f"Project storage path is not empty: {target_path}. "
                    "Use register_project for an existing database folder."
                )
        else:
            target_path.mkdir(parents=True, exist_ok=True)
        target_path = target_path.resolve()

        now = self._now()
        project_info = {
            "name": name,
            "path": str(target_path),
            "created_at": now,
            "last_accessed": now,
            "description": description,
            "ownership": MANAGED_OWNERSHIP,
            "managed": True,
        }
        projects[name] = project_info
        cfg["active_project"] = name
        self.save_config(cfg)
        return project_info

    def register_project(
        self, name: str, path: str | Path, description: str = ""
    ) -> dict[str, Any]:
        """Register an existing external directory without taking ownership."""
        name = self._validate_project_name(name)
        target_path = Path(path).expanduser()
        if not target_path.exists():
            raise FileNotFoundError(f"Path does not exist: {target_path.resolve(strict=False)}")
        if target_path.is_symlink() or not target_path.is_dir():
            raise ValueError(f"Registered project path is not a directory: {target_path.resolve(strict=False)}")
        target_path = target_path.resolve()

        cfg = self._load_for_mutation()
        projects = cfg["projects"]
        if name in projects:
            raise ValueError(f"Project '{name}' is already registered.")
        self._assert_unique_path(projects, target_path)

        now = self._now()
        project_info = {
            "name": name,
            "path": str(target_path),
            "created_at": now,
            "last_accessed": now,
            "description": description,
            "ownership": EXTERNAL_OWNERSHIP,
            "managed": False,
        }
        projects[name] = project_info
        if cfg.get("active_project") is None:
            cfg["active_project"] = name
        self.save_config(cfg)
        return project_info

    def switch_project(self, name: str) -> dict[str, Any]:
        """Switch the active project."""
        cfg = self._load_for_mutation()
        if name not in cfg["projects"]:
            raise KeyError(f"Project '{name}' is not registered.")
        cfg["active_project"] = name
        cfg["projects"][name]["last_accessed"] = self._now()
        self.save_config(cfg)
        return cfg["projects"][name]

    @staticmethod
    def _stop_watchers_for_path(path: Path) -> None:
        """Stop watchers for one project before attempting storage removal."""
        try:
            from trace_lite.watcher import stop_watchers_for_path

            stop_watchers_for_path(path)
        except Exception as exc:
            raise ProjectDeletionError(
                f"Unable to stop watchers for project storage '{path}': {exc}"
            ) from exc

    def delete_project(
        self,
        name: str,
        delete_files: bool = True,
        *,
        purge_external: bool = False,
    ) -> bool:
        """Unregister a project and optionally purge owned storage.

        Managed storage is purged when ``delete_files`` is true.  External
        registrations are retained unless ``purge_external`` is explicitly
        supplied, and shared or legacy-unowned paths are never recursively
        removed.  Storage is removed before the registry is changed so a
        purge failure leaves the project entry intact.
        """
        cfg = self._load_for_mutation()
        projects = cfg["projects"]
        if name not in projects:
            raise KeyError(f"Project '{name}' does not exist.")

        project_info = projects[name]
        raw_project_path = Path(project_info["path"]).expanduser()
        project_path = raw_project_path.resolve(strict=False)
        ownership = self._effective_ownership(project_info)
        shared = self._is_shared_path(projects, project_path, excluding=name)
        should_purge = bool(delete_files) and (
            ownership == MANAGED_OWNERSHIP
            or (ownership == EXTERNAL_OWNERSHIP and purge_external)
        ) and not shared

        if should_purge and project_path.exists():
            self._stop_watchers_for_path(project_path)
            if raw_project_path.is_symlink() or not project_path.is_dir():
                raise ProjectDeletionError(
                    f"Project storage is not a directory and was not removed: {project_path}"
                )
            try:
                shutil.rmtree(project_path)
            except Exception as exc:
                raise ProjectDeletionError(
                    f"Failed to remove project storage '{project_path}'; "
                    f"the project remains registered: {exc}"
                ) from exc
        else:
            # Even when storage is retained (delete_files=False, external,
            # shared, or unowned), a live watcher must not continue writing to
            # a project being unregistered.
            self._stop_watchers_for_path(project_path)

        del projects[name]
        if cfg.get("active_project") == name:
            cfg["active_project"] = next(iter(projects), None)
        self.save_config(cfg)
        return True


__all__ = [
    "EXTERNAL_OWNERSHIP",
    "MANAGED_OWNERSHIP",
    "NO_ACTIVE_PROJECT_MESSAGE",
    "NoActiveProjectError",
    "ProjectConfigError",
    "ProjectDeletionError",
    "ProjectManager",
    "UNOWNED_OWNERSHIP",
    "get_default_config_dir",
    "get_projects_config_path",
]
