"""Vault Watcher module for trace-lite.

Monitors a directory (such as an Obsidian Vault) for markdown file changes and
auto-ingests modified notes into TraceLite.
"""

import logging
import os
from pathlib import Path
import threading
import time
from typing import Dict, List, Optional, Any

from trace_lite.db import TraceLite

logger = logging.getLogger("trace_lite.watcher")


def is_markdown_file(path: Path) -> bool:
    """Check if a file path is a markdown file."""
    return path.is_file() and path.suffix.lower() in (".md", ".markdown")


def is_hidden_path(rel_path: Path) -> bool:
    """Check if any parent component or filename starts with dot (e.g. .obsidian, .git)."""
    for part in rel_path.parts:
        if part.startswith("."):
            return True
    return False


def sync_vault(
    vault_path: str | Path,
    db: TraceLite | None = None,
    data_dir: str | Path | None = None,
    auto_consolidate: bool = True,
) -> Dict[str, Any]:
    """
    Perform a one-shot scan of a vault directory, ingesting all new/modified markdown notes.
    """
    path = Path(vault_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Vault directory does not exist: {vault_path}")
    if not path.is_dir():
        raise ValueError(f"Vault path is not a directory: {vault_path}")

    if db is None:
        if data_dir is None:
            from trace_lite.projects import ProjectManager
            data_dir = ProjectManager().get_active_project_path()
        db = TraceLite(data_dir)

    watcher = VaultWatcher(vault_path=path, db=db, auto_consolidate=auto_consolidate)
    return watcher.scan_once()


class VaultWatcher:
    """
    Watches a vault directory for markdown file modifications and ingests them into TraceLite.
    Supports both single-pass scan and continuous background watching/polling.
    """

    def __init__(
        self,
        vault_path: str | Path,
        db: TraceLite | None = None,
        data_dir: str | Path | None = None,
        poll_interval: float = 2.0,
        auto_consolidate: bool = True,
    ):
        self.vault_path = Path(vault_path).resolve()
        if not self.vault_path.exists():
            raise FileNotFoundError(f"Vault directory does not exist: {vault_path}")
        if not self.vault_path.is_dir():
            raise ValueError(f"Vault path is not a directory: {vault_path}")

        if db is not None:
            self.db = db
        else:
            if data_dir is None:
                from trace_lite.projects import ProjectManager
                data_dir = ProjectManager().get_active_project_path()
            self.db = TraceLite(data_dir)

        self.poll_interval = max(0.5, float(poll_interval))
        self.auto_consolidate = auto_consolidate

        # Map of absolute path -> last modification time
        self._file_mtimes: Dict[Path, float] = {}

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def scan_once(self) -> Dict[str, Any]:
        """
        Scan all markdown files in the vault directory once.
        Ingest new or modified files into TraceLite.
        """
        files_scanned = 0
        files_ingested = 0
        total_atoms = 0
        updated_tree_ids: set[str] = set()

        if not self.vault_path.exists():
            return {
                "status": "error",
                "message": f"Vault path {self.vault_path} does not exist.",
                "files_scanned": 0,
                "files_ingested": 0,
                "atoms_ingested": 0,
                "trees_updated": 0,
            }

        # Recursively walk vault directory
        for root, dirs, files in os.walk(self.vault_path):
            root_path = Path(root)
            # Filter out hidden directories in-place
            dirs[:] = [d for d in dirs if not d.startswith(".")]

            for file_name in files:
                if file_name.startswith("."):
                    continue

                file_path = root_path / file_name
                rel_path = file_path.relative_to(self.vault_path)

                if is_hidden_path(rel_path) or not is_markdown_file(file_path):
                    continue

                files_scanned += 1

                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    continue

                last_mtime = self._file_mtimes.get(file_path)
                if last_mtime is not None and mtime <= last_mtime:
                    continue

                # Read and ingest markdown file
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue

                if not text.strip():
                    self._file_mtimes[file_path] = mtime
                    continue

                doc_name = str(rel_path).replace("\\", "/")
                source_uri = file_path.as_uri()

                try:
                    res = self.db.ingest(
                        text=text,
                        document_name=doc_name,
                        source_uri=source_uri,
                        metadata={
                            "vault_path": str(self.vault_path),
                            "relative_path": doc_name,
                        },
                    )
                    self._file_mtimes[file_path] = mtime
                    files_ingested += 1
                    total_atoms += res.atom_count
                    updated_tree_ids.update(res.tree_ids)
                except Exception as err:
                    logger.warning(f"Error ingesting file {file_path}: {err}")

        trees_updated = 0
        if files_ingested > 0 and self.auto_consolidate:
            try:
                con_res = self.db.consolidate()
                trees_updated = con_res.trees_updated
            except Exception as err:
                logger.warning(f"Error during consolidation: {err}")

        return {
            "status": "ok",
            "vault_path": str(self.vault_path),
            "files_scanned": files_scanned,
            "files_ingested": files_ingested,
            "atoms_ingested": total_atoms,
            "trees_updated": trees_updated,
            "tree_ids": list(updated_tree_ids),
        }

    def _worker_loop(self) -> None:
        """Background thread loop for polling file changes."""
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as e:
                logger.error(f"Error in vault watcher loop: {e}")

            # Sleep in small increments to respond quickly to stop signal
            end_time = time.time() + self.poll_interval
            while time.time() < end_time and not self._stop_event.is_set():
                time.sleep(0.1)

    def start(self) -> None:
        """Start the background watcher thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"VaultWatcher-{self.vault_path.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background watcher thread."""
        if not self._running:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
