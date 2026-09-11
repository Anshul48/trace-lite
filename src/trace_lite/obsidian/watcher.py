"""Debounced vault watcher: coalesces rapid saves into one re-index pass (MED-02)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

DEBOUNCE_SECONDS = 0.5


class DebouncedWatcher:
    """Poll-based markdown watcher with a per-path 500ms debounce buffer.

    Polling (not inotify) keeps the zero-dependency footprint and works across
    network filesystems. Only `.md` files are tracked.
    """

    def __init__(
        self,
        vault: str | Path,
        on_sync: Callable[[list[Path]], None],
        on_remove: Callable[[list[Path]], None] | None = None,
        debounce_seconds: float = DEBOUNCE_SECONDS,
        poll_seconds: float = 0.2,
    ) -> None:
        self.vault = Path(vault)
        self.on_sync = on_sync
        self.on_remove = on_remove
        self.debounce_seconds = debounce_seconds
        self.poll_seconds = poll_seconds
        self._mtimes: dict[str, float] = {}
        self._pending: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.sync_count = 0
        self.remove_count = 0

    # -- polling ------------------------------------------------------
    def scan_once(self) -> list[Path]:
        """One poll pass. Returns newly-changed files; fires sync for debounced ones."""
        now = time.monotonic()
        changed: list[Path] = []
        if self.vault.is_dir():
            for root, _, files in os.walk(self.vault):
                for name in files:
                    if not name.endswith(".md"):
                        continue
                    path = Path(root) / name
                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        continue
                    key = str(path)
                    if self._mtimes.get(key) != mtime:
                        self._mtimes[key] = mtime
                        self._pending[key] = now
        # Vanished tracked files propagate as removals (never silent ghosts).
        removed = [Path(k) for k in list(self._mtimes) if not Path(k).exists()]
        for path in removed:
            self._mtimes.pop(str(path), None)
            self._pending.pop(str(path), None)
        if removed and self.on_remove is not None:
            self.remove_count += 1
            self.on_remove(sorted(removed))
        due = [Path(k) for k, first in list(self._pending.items())
               if now - first >= self.debounce_seconds]
        for path in due:
            del self._pending[str(path)]
            changed.append(path)
        if changed:
            self.sync_count += 1
            self.on_sync(sorted(changed))
        return changed

    def notify_changed(self, path: str | Path) -> None:
        """Direct notification hook (used by tests and external file events)."""
        key = str(path)
        self._pending.setdefault(key, time.monotonic())

    def pending_count(self) -> int:
        return len(self._pending)

    # -- background loop ------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.scan_once()
            time.sleep(self.poll_seconds)
