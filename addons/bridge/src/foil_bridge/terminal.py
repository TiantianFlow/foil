"""A PTY running the user's shell in a worktree Foil owns.

The process is the shell alone. This module does not call tmux and does
not send keys to a pane. Closing the socket ends the shell's process group.
Nothing here is written to disk.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import select
import shlex
import socket
import subprocess
import termios
import threading
from pathlib import Path
from typing import BinaryIO

from foil.project import store_root
from foil.store import FLEET_ID, SAFE_ID

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_WINDOW = re.compile(r"@\d{1,20}")
LIMIT = 8
_LOCK = threading.Lock()
_OPEN = 0
_GROUPS: set[int] = set()
_PENDING_MAX = 1024 * 1024
_DROPPED = ("FOIL_SEAT_ID", "TMUX", "TMUX_PANE")


def accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _GUID).encode()).digest()
    return base64.b64encode(digest).decode("ascii")


def attach_command(session: str, window: str = "") -> str:
    """The tmux command a person can copy. It is text, not a process."""

    if not SAFE_ID.fullmatch(session or ""):
        return ""
    quoted = "'" + ("=" + session).replace("'", "'\\''") + "'"
    command = f"tmux attach -t {quoted}"
    if window and _WINDOW.fullmatch(window):
        command += " \\; select-window -t " + shlex.quote(window)
    return command


def owned_worktree(text: str) -> Path | None:
    """The directory when ``text`` is a worktree Foil owns, else None.

    A worktree Foil owns is a real directory directly under
    ``<root>/projects/<key>/worktrees``, and that resolved path is the
    ``worktree`` recorded for a seat in that project. A symlink on that
    chain is not owned. Any other path, including the checkout, is refused.
    """

    if not _plain(text):
        return None
    projects = store_root() / "projects"
    try:
        if projects.is_symlink() or not projects.is_dir():
            return None
        requested = Path(text)
        if requested.is_symlink() or not requested.is_dir():
            return None
        resolved = requested.resolve()
    except OSError:
        return None
    for project in projects.iterdir():
        if not _project(project):
            continue
        seats = project / "worktrees"
        try:
            if seats.is_symlink() or not seats.is_dir():
                continue
            if resolved.parent != seats.resolve() or not SAFE_ID.fullmatch(resolved.name):
                continue
            child = seats / resolved.name
            if child.is_symlink() or not child.is_dir() or child.resolve() != resolved:
                continue
        except OSError:
            continue
        if resolved in _recorded(project):
            return resolved
    return None


def acquire() -> bool:
    global _OPEN
    with _LOCK:
        if _OPEN >= LIMIT:
            return False
        _OPEN += 1
        return True


def release() -> None:
    global _OPEN
    with _LOCK:
        if _OPEN:
            _OPEN -= 1


def end_sessions() -> None:
    """End every desk shell. The Bridge calls this when it stops."""

    with _LOCK:
        groups = tuple(_GROUPS)
    for pid in groups:
        with contextlib.suppress(OSError):
            os.killpg(pid, 15)
    for pid in groups:
        with contextlib.suppress(OSError):
            os.killpg(pid, 9)


def session(reader: BinaryIO, writer: BinaryIO, sock: socket.socket, directory: Path) -> None:
    """Run the shell until the socket closes or the shell exits."""

    lock = threading.Lock()
    try:
        held = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        _close_frame(writer, lock)
        return
    master, slave = os.openpty()

    def _prepare() -> None:
        # The fd is the directory owned_worktree opened. cwd would look the path up again.
        os.fchdir(held)
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    try:
        proc = subprocess.Popen(
            _shell(),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=_env(),
            start_new_session=True,
            close_fds=True,
            preexec_fn=_prepare,
        )
    except OSError:
        os.close(held)
        os.close(master)
        os.close(slave)
        _close_frame(writer, lock)
        return
    os.close(held)
    os.close(slave)
    os.set_blocking(master, False)
    _track(proc.pid)
    stop = threading.Event()
    pending = bytearray()
    pending_lock = threading.Lock()

    def pump() -> None:
        try:
            while not stop.is_set():
                with pending_lock:
                    waiting = bool(pending)
                try:
                    readable, writable, _ = select.select(
                        [master], [master] if waiting else [], [], 0.25
                    )
                except OSError:
                    break
                if writable and not _drain(master, pending, pending_lock):
                    break
                if not readable:
                    if proc.poll() is not None:
                        with pending_lock:
                            if not pending:
                                break
                    continue
                try:
                    data = os.read(master, 4096)
                except BlockingIOError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if not _frame(writer, 0x2, data, lock):
                    break
        finally:
            stop.set()
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RD)

    thread = threading.Thread(target=pump, name="foil-bridge-terminal", daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            frame = _read_frame(reader)
            if frame is None:
                break
            opcode, payload = frame
            if opcode == 0x8:
                break
            if opcode == 0x9:
                if not _frame(writer, 0xA, payload, lock):
                    break
                continue
            if opcode in {0x1, 0x2} and payload:
                with pending_lock:
                    if len(pending) + len(payload) > _PENDING_MAX:
                        continue
                    pending.extend(payload)
                _drain(master, pending, pending_lock)
    finally:
        stop.set()
        _end(proc, master)
        _untrack(proc.pid)
        thread.join(timeout=2)
        _close_frame(writer, lock)


def _plain(text: str) -> bool:
    return (
        bool(text)
        and len(text) <= 4096
        and text.startswith("/")
        and not any(ord(char) < 32 for char in text)
    )


def _project(project: Path) -> bool:
    record = project / "project.json"
    try:
        return (
            not project.is_symlink()
            and not record.is_symlink()
            and record.is_file()
            and not project.name.startswith(".")
        )
    except OSError:
        return False


def _recorded(project: Path) -> set[Path]:
    fleets = project / "fleets"
    found: set[Path] = set()
    try:
        if fleets.is_symlink() or not fleets.is_dir():
            return found
        names = list(fleets.iterdir())
    except OSError:
        return found
    for fleet in names:
        if fleet.is_symlink() or not FLEET_ID.fullmatch(fleet.name):
            continue
        run = fleet / "run"
        registry = run / "registry.json"
        try:
            if run.is_symlink() or registry.is_symlink() or not registry.is_file():
                continue
            payload = json.loads(registry.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        seats = payload.get("seats") if isinstance(payload, dict) else None
        if not isinstance(seats, dict):
            continue
        for record in seats.values():
            path = _directory(record)
            if path is not None:
                found.add(path)
    return found


def _directory(record: object) -> Path | None:
    if not isinstance(record, dict):
        return None
    text = record.get("worktree")
    if not isinstance(text, str) or not _plain(text):
        return None
    path = Path(text)
    try:
        if path.is_symlink() or not path.is_dir():
            return None
        return path.resolve()
    except OSError:
        return None


def _shell() -> list[str]:
    shell = os.environ.get("SHELL", "")
    if shell.startswith("/") and os.path.isfile(shell) and os.access(shell, os.X_OK):
        return [shell]
    return ["/bin/sh"]


def _env() -> dict[str, str]:
    env = os.environ.copy()
    for name in _DROPPED:
        env.pop(name, None)
    env["TERM"] = "dumb"
    env["PAGER"] = "cat"
    env["GIT_PAGER"] = "cat"
    return env


def _end(proc: subprocess.Popen[bytes], master: int) -> None:
    with contextlib.suppress(OSError):
        os.close(master)
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, 15)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, 9)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=1)
    # A shell that already exited can leave a child in its process group.
    with contextlib.suppress(OSError):
        os.killpg(proc.pid, 9)


def _track(pid: int) -> None:
    with _LOCK:
        _GROUPS.add(pid)


def _untrack(pid: int) -> None:
    with _LOCK:
        _GROUPS.discard(pid)


def _drain(fd: int, pending: bytearray, lock: threading.Lock) -> bool:
    """Write queued input in order. A full PTY keeps the rest."""

    with lock:
        while pending:
            try:
                wrote = os.write(fd, pending)
            except BlockingIOError:
                return True
            except OSError:
                pending.clear()
                return False
            if not wrote:
                pending.clear()
                return False
            del pending[:wrote]
    return True


def _frame(writer: BinaryIO, opcode: int, payload: bytes, lock: threading.Lock) -> bool:
    length = len(payload)
    if length < 126:
        header = bytes((0x80 | opcode, length))
    elif length < 65536:
        header = bytes((0x80 | opcode, 126)) + length.to_bytes(2, "big")
    else:
        header = bytes((0x80 | opcode, 127)) + length.to_bytes(8, "big")
    try:
        with lock:
            writer.write(header + payload)
            writer.flush()
    except (BrokenPipeError, ConnectionError, OSError):
        return False
    return True


def _close_frame(writer: BinaryIO, lock: threading.Lock) -> None:
    _frame(writer, 0x8, b"", lock)


def _read_exact(reader: BinaryIO, count: int) -> bytes | None:
    data = bytearray()
    while len(data) < count:
        try:
            chunk = reader.read(count - len(data))
        except (ConnectionError, OSError):
            return None
        if not chunk:
            return None
        data += chunk
    return bytes(data)


def _read_frame(reader: BinaryIO) -> tuple[int, bytes] | None:
    head = _read_exact(reader, 2)
    if head is None or head[0] & 0x70 or not head[0] & 0x80:
        return None
    if not head[1] & 0x80:
        return None
    length = head[1] & 0x7F
    if length == 126:
        raw = _read_exact(reader, 2)
        if raw is None:
            return None
        length = int.from_bytes(raw, "big")
    elif length == 127:
        return None
    if length > 65536:
        return None
    mask = _read_exact(reader, 4)
    if mask is None:
        return None
    payload = _read_exact(reader, length) if length else b""
    if payload is None:
        return None
    return head[0] & 0x0F, bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
