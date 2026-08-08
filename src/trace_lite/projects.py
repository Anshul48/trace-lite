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

"""Project and database registry management for trace-lite."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_default_config_dir() -> Path:
    """Return central configuration directory ~/.trace_lite."""
    cfg_dir = Path.home() / ".trace_lite"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_projects_config_path(custom_path: Path | str | None = None) -> Path:
    """Return path to projects.json config file."""
    if custom_path:
        p = Path(custom_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return get_default_config_dir() / "projects.json"


class ProjectManager:
    """Manages central project registry (~/.trace_lite/projects.json) and database isolation."""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = get_projects_config_path(config_path)

    def load_config(self) -> dict[str, Any]:
        """Load project configuration dictionary."""
        if not self.config_path.exists():
            default_path = str((Path.home() / ".trace_lite" / "dbs" / "default").resolve())
            default_cfg: dict[str, Any] = {
                "active_project": "default",
                "projects": {
                    "default": {
                        "name": "default",
                        "path": default_path,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "last_accessed": datetime.now(timezone.utc).isoformat(),
                        "description": "Default local database",
                    }
                },
            }
            self.save_config(default_cfg)
            return default_cfg

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "projects" not in data:
                    data["projects"] = {}
                if "active_project" not in data:
                    data["active_project"] = "default"
                return data
        except Exception:
            return {
                "active_project": "default",
                "projects": {},
            }

    def save_config(self, data: dict[str, Any]) -> None:
        """Save project configuration dictionary."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_active_project_name(self) -> str:
        """Get the currently active project name."""
        cfg = self.load_config()
        active = cfg.get("active_project", "default")
        projects = cfg.get("projects", {})

        if active not in projects:
            if projects:
                active = next(iter(projects.keys()))
                cfg["active_project"] = active
                self.save_config(cfg)
            else:
                self.create_project("default")
                active = "default"
        return active

    def get_active_project_path(self) -> Path:
        """Return Path object for active project data directory."""
        name = self.get_active_project_name()
        info = self.get_project(name)
        if info and "path" in info:
            p = Path(info["path"])
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()
        fallback = Path.home() / ".trace_lite" / "dbs" / "default"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback.resolve()

    def get_project(self, name: str) -> dict[str, Any] | None:
        """Get metadata dictionary for a specific project."""
        cfg = self.load_config()
        return cfg.get("projects", {}).get(name)

    def list_projects(self) -> list[dict[str, Any]]:
        """
        List all registered projects with live database statistics.
        
        Returns a list of dicts with:
        name, path, is_active, created_at, last_accessed, atom_count, tree_count, estimated_tokens
        """
        cfg = self.load_config()
        active_name = self.get_active_project_name()
        projects_dict = cfg.get("projects", {})

        results = []
        for p_name, p_info in projects_dict.items():
            p_path = Path(p_info["path"])
            is_active = (p_name == active_name)
            
            # Fetch live metrics if TraceLite DB exists at location
            atom_count = 0
            tree_count = 0
            estimated_tokens = 0
            exists = p_path.exists()

            if exists:
                try:
                    from trace_lite.spine import SpineStore
                    from trace_lite.cortex import ForestIndex

                    spine_file = p_path / "spine.sqlite3"
                    cortex_file = p_path / "cortex.sqlite3"

                    if spine_file.exists():
                        spine = SpineStore(spine_file)
                        atom_count = spine.count_atoms()
                        spine.close()

                    if cortex_file.exists():
                        forest = ForestIndex(cortex_file)
                        tree_count = len(forest.list_trees())
                        forest.close()

                    estimated_tokens = int(atom_count * 30 * 1.33)
                except Exception:
                    pass

            results.append({
                "name": p_name,
                "path": str(p_path),
                "is_active": is_active,
                "created_at": p_info.get("created_at", ""),
                "last_accessed": p_info.get("last_accessed", ""),
                "description": p_info.get("description", ""),
                "atom_count": atom_count,
                "tree_count": tree_count,
                "estimated_tokens": estimated_tokens,
                "exists_on_disk": exists,
            })

        return results

    def create_project(
        self, name: str, path: str | Path | None = None, description: str = ""
    ) -> dict[str, Any]:
        """Create a new project and set it active."""
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty.")

        cfg = self.load_config()

        if path:
            target_path = Path(path).resolve()
        else:
            target_path = (Path.home() / ".trace_lite" / "dbs" / name).resolve()

        target_path.mkdir(parents=True, exist_ok=True)

        now_str = datetime.now(timezone.utc).isoformat()
        proj_info = {
            "name": name,
            "path": str(target_path),
            "created_at": now_str,
            "last_accessed": now_str,
            "description": description,
        }

        cfg["projects"][name] = proj_info
        cfg["active_project"] = name
        self.save_config(cfg)

        return proj_info

    def register_project(
        self, name: str, path: str | Path, description: str = ""
    ) -> dict[str, Any]:
        """Register an existing project directory."""
        name = name.strip()
        if not name:
            raise ValueError("Project name cannot be empty.")

        target_path = Path(path).resolve()
        if not target_path.exists():
            raise FileNotFoundError(f"Path does not exist: {target_path}")

        cfg = self.load_config()
        now_str = datetime.now(timezone.utc).isoformat()
        proj_info = {
            "name": name,
            "path": str(target_path),
            "created_at": now_str,
            "last_accessed": now_str,
            "description": description,
        }

        cfg["projects"][name] = proj_info
        self.save_config(cfg)
        return proj_info

    def switch_project(self, name: str) -> dict[str, Any]:
        """Switch the active project."""
        cfg = self.load_config()
        if name not in cfg.get("projects", {}):
            raise KeyError(f"Project '{name}' is not registered.")

        cfg["active_project"] = name
        cfg["projects"][name]["last_accessed"] = datetime.now(timezone.utc).isoformat()
        self.save_config(cfg)
        return cfg["projects"][name]

    def delete_project(self, name: str, delete_files: bool = True) -> bool:
        """
        Delete a project from the registry and optionally delete its folder on disk.
        """
        cfg = self.load_config()
        projects = cfg.get("projects", {})

        if name not in projects:
            raise KeyError(f"Project '{name}' does not exist.")

        proj_info = projects[name]
        proj_path = Path(proj_info["path"])

        # Remove from registry
        del cfg["projects"][name]

        # Handle active project fallback if we deleted the active one
        if cfg.get("active_project") == name:
            if cfg["projects"]:
                cfg["active_project"] = next(iter(cfg["projects"].keys()))
            else:
                cfg["active_project"] = "default"

        self.save_config(cfg)

        # Remove directory on disk if requested
        if delete_files and proj_path.exists():
            shutil.rmtree(proj_path, ignore_errors=True)

        return True
