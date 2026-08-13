"""Vault Watcher module for trace-lite.

Monitors a directory (such as an Obsidian Vault) for markdown file changes and
auto-ingests modified notes into TraceLite.
"""

import logging
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Dict, List, Optional, Any

from trace_lite.db import TraceLite

logger = logging.getLogger("trace_lite.watcher")

_watchers_lock = threading.RLock()
_registered_watchers: dict[int, "VaultWatcher"] = {}


def _register_watcher(watcher: "VaultWatcher") -> None:
    with _watchers_lock:
        _registered_watchers[id(watcher)] = watcher


def _unregister_watcher(watcher: "VaultWatcher") -> None:
    with _watchers_lock:
        _registered_watchers.pop(id(watcher), None)


def stop_watchers_for_path(path: str | Path) -> None:
    """Stop every live watcher attached to one project storage path."""
    target = Path(path).resolve(strict=False)
    with _watchers_lock:
        watchers = [
            watcher
            for watcher in _registered_watchers.values()
            if Path(watcher.db.data_dir).resolve(strict=False) == target
        ]
    for watcher in watchers:
        watcher.stop()


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
    auto_consolidate: bool = False,
    diagnostic_sink=None,
    build_lock=None,
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

    watcher = VaultWatcher(
        vault_path=path,
        db=db,
        auto_consolidate=auto_consolidate,
        diagnostic_sink=diagnostic_sink,
        build_lock=build_lock,
    )
    try:
        return watcher.scan_once()
    finally:
        # A one-shot sync is not a background watcher and should not remain in
        # the project lifecycle registry after the scan has completed.
        watcher.stop()


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
        auto_consolidate: bool = False,
        diagnostic_sink=None,
        build_lock=None,
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
        self.diagnostic_sink = diagnostic_sink
        self.build_lock = build_lock

        # The connection id is stable for this project and folder.  Fingerprints
        # are stored in Spine, so a new watcher process can skip unchanged files.
        self.connection_id = "folder-" + hashlib.sha256(
            str(self.vault_path).encode("utf-8")
        ).hexdigest()[:20]
        self.db.spine.upsert_source_connection(
            self.connection_id,
            str(self.vault_path),
            metadata={"watch_mode": "poll", "auto_consolidate": auto_consolidate},
        )

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
            current_status = self.db.status()
            return {
                "status": "error",
                "message": f"Vault path {self.vault_path} does not exist.",
                "files_scanned": 0,
                "files_ingested": 0,
                "atoms_ingested": 0,
                "trees_updated": 0,
                "pending_atoms": current_status.pending_atoms,
                "pending_trees": current_status.pending_trees,
                "needs_organization": current_status.needs_organization,
                "needs_recovery": current_status.needs_recovery,
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

                fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
                previous = self.db.spine.get_source_file_fingerprint(
                    self.connection_id, str(rel_path).replace("\\", "/")
                )

                if not text.strip():
                    self._file_mtimes[file_path] = mtime
                    self.db.spine.record_source_file_fingerprint(
                        self.connection_id,
                        str(rel_path).replace("\\", "/"),
                        fingerprint,
                        artifact_id=previous.get("artifact_id") if previous else None,
                        file_size=len(text.encode("utf-8")),
                        modified_at=mtime,
                    )
                    continue

                # Content fingerprints, rather than only mtimes, make restart
                # behavior deterministic and avoid duplicate immutable artifacts.
                if previous and previous["fingerprint"] == fingerprint:
                    self._file_mtimes[file_path] = mtime
                    self.db.spine.record_source_file_fingerprint(
                        self.connection_id,
                        str(rel_path).replace("\\", "/"),
                        fingerprint,
                        artifact_id=previous.get("artifact_id"),
                        file_size=len(text.encode("utf-8")),
                        modified_at=mtime,
                    )
                    continue

                doc_name = str(rel_path).replace("\\", "/")
                source_uri = file_path.as_uri()

                # Databases created by an older watcher may have the immutable
                # artifact but not the fingerprint table entry.  Migrate that
                # case without re-ingesting the source.
                if not previous:
                    for artifact in self.db.spine.list_artifacts():
                        if artifact.source_uri == source_uri and artifact.content_hash == fingerprint:
                            self._file_mtimes[file_path] = mtime
                            self.db.spine.record_source_file_fingerprint(
                                self.connection_id,
                                doc_name,
                                fingerprint,
                                artifact_id=artifact.artifact_id,
                                file_size=len(text.encode("utf-8")),
                                modified_at=mtime,
                            )
                            previous = {"artifact_id": artifact.artifact_id}
                            break
                    if previous:
                        continue

                try:
                    res = self.db.ingest(
                        text=text,
                        document_name=doc_name,
                        source_uri=source_uri,
                        metadata={
                            "vault_path": str(self.vault_path),
                            "relative_path": doc_name,
                            "connection_id": self.connection_id,
                            "fingerprint": fingerprint,
                        },
                    )
                    self._file_mtimes[file_path] = mtime
                    self.db.spine.record_source_file_fingerprint(
                        self.connection_id,
                        doc_name,
                        fingerprint,
                        artifact_id=res.artifact_id,
                        file_size=len(text.encode("utf-8")),
                        modified_at=mtime,
                    )
                    files_ingested += 1
                    total_atoms += res.atom_count
                    updated_tree_ids.update(res.tree_ids)
                except Exception as err:
                    logger.warning(f"Error ingesting file {file_path}: {err}")

        trees_updated = 0
        if files_ingested > 0 and self.auto_consolidate:
            try:
                if self.build_lock is None:
                    con_res = self.db.consolidate(diagnostic_sink=self.diagnostic_sink)
                else:
                    with self.build_lock:
                        con_res = self.db.consolidate(diagnostic_sink=self.diagnostic_sink)
                trees_updated = con_res.trees_updated
            except Exception as err:
                logger.warning(f"Error during consolidation: {err}")

        status = self.db.status()
        return {
            "status": "ok",
            "vault_path": str(self.vault_path),
            "connection_id": self.connection_id,
            "files_scanned": files_scanned,
            "files_ingested": files_ingested,
            "atoms_ingested": total_atoms,
            "trees_updated": trees_updated,
            "tree_ids": list(updated_tree_ids),
            "pending_atoms": status.pending_atoms,
            "pending_trees": status.pending_trees,
            "orphaned_atoms": status.orphaned_atoms,
            "needs_organization": status.needs_organization,
            "needs_recovery": status.needs_recovery,
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
        _register_watcher(self)
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"VaultWatcher-{self.vault_path.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background watcher thread."""
        if not self._running:
            _unregister_watcher(self)
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._running = False
        _unregister_watcher(self)

    @property
    def is_running(self) -> bool:
        return self._running
