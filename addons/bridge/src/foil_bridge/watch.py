"""File times under the store, once a second. Events carry ids, not bytes."""

from __future__ import annotations

import os
import threading
from pathlib import Path

CAP = 500


def diff(previous: dict[str, int], current: dict[str, int], *, cap: int = CAP) -> list[str]:
    """Ids whose time appeared, disappeared, or moved. ``*`` means list again."""

    changed = [
        key
        for key in sorted(set(previous) | set(current))
        if previous.get(key) != current.get(key)
    ]
    if len(changed) > cap:
        return ["*"]
    return changed


def scan(root: Path) -> dict[str, int]:
    """Modification times, in nanoseconds, for regular files outside ``bridge/``."""

    found: dict[str, int] = {}
    if not root.is_dir() or root.is_symlink():
        return found
    root_resolved = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            child = current / name
            if child.is_symlink():
                continue
            if current == root and name == "bridge":
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(root)
                resolved = path.resolve()
            except (OSError, ValueError):
                continue
            if relative.parts[:1] == ("bridge",) or not resolved.is_relative_to(root_resolved):
                continue
            try:
                found[relative.as_posix()] = path.stat().st_mtime_ns
            except OSError:
                continue
    return found


class Watcher:
    """Polls ``scan`` and wakes stream threads with the latest ids."""

    def __init__(self, root: Path, interval: float) -> None:
        self.root = root
        self.interval = interval
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.generation = 0
        self._previous: dict[str, int] = {}
        self._pending: list[str] = []
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="foil-bridge-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()
        with self._cond:
            self._cond.notify_all()

    def items(self) -> list[dict[str, int | str]]:
        with self._cond:
            current = dict(self._previous)
        return [
            {"id": key, "mtime": current[key] // 1_000_000_000} for key in sorted(current)
        ]

    def wait(self, cursor: int, timeout: float) -> tuple[list[str], int]:
        """Ids published after ``cursor``. An empty list is a heartbeat."""

        with self._cond:
            if self.generation == cursor and not self.stopped.is_set():
                self._cond.wait(timeout=timeout)
            if self.generation == cursor:
                return [], cursor
            return list(self._pending), self.generation

    def _run(self) -> None:
        while not self.stopped.is_set():
            current = scan(self.root)
            with self._cond:
                if not self.ready.is_set():
                    self._previous = current
                    self.ready.set()
                else:
                    changed = diff(self._previous, current)
                    self._previous = current
                    if changed:
                        self._pending = changed
                        self.generation += 1
                        self._cond.notify_all()
            self.stopped.wait(self.interval)
