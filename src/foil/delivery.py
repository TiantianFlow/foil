"""Per-seat delivery log, lead pointers, and the fleet pause flag.

The log records a board path and a state. It holds no message body and no
turn output (N5). Core stores a preset's transcript id and does not read one.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.errors import FoilError
from foil.store import SAFE_ID, exclusive_lock, write_bytes

LIMIT = 64 * 1024
STUCK_AFTER = 60
STATES = frozenset({"inflight", "delivered", "retry", "skipped", "printed"})
_POINTER_STATES = frozenset({"open", "listed"})


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_seconds(stamp: str, *, now: datetime | None = None) -> float:
    """Seconds since an UTC stamp. A future or unreadable stamp is 0 or stuck."""

    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return float(STUCK_AFTER)
    current = now or datetime.now(UTC)
    return max(0.0, (current - when).total_seconds())


def derived(latest: str | None, *, age: float, running: bool) -> str:
    """The `mail list --sent` state for one path.

    `running` is true when a turn is in progress and this path is in that
    turn. `age` is the age of the latest log line, or of the mail file when
    the log has no line for it.
    """

    if latest in {"delivered", "printed"}:
        return "delivered"
    if latest == "skipped":
        return "skipped"
    if running:
        return "inflight"
    if latest in {"inflight", "retry"} and age < STUCK_AFTER:
        return "inflight"
    if latest is None and age < STUCK_AFTER:
        return "queued"
    return "stuck"


def _check_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("delivery path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("delivery path")
    return value


def _seat(seat: str) -> str:
    if not SAFE_ID.fullmatch(seat):
        raise ValueError("delivery seat")
    return seat


def _log_paths(fleet: Path, seat: str) -> tuple[Path, Path]:
    turns = fleet / "run" / "turns"
    return turns / f"{seat}.log.1", turns / f"{seat}.log"


def _read_lines(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.is_file():
        return []
    found: list[dict[str, Any]] = []
    for raw in path.read_bytes().splitlines():
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            found.append(item)
    return found


def read_log(fleet: Path, seat: str) -> list[dict[str, Any]]:
    """The rotated file, then the active one, oldest first."""

    previous, active = _log_paths(fleet, _seat(seat))
    return _read_lines(previous) + _read_lines(active)


def latest_line(fleet: Path, seat: str, path: str) -> dict[str, Any] | None:
    found = None
    for item in read_log(fleet, seat):
        if item.get("path") == path and item.get("state") in STATES:
            found = item
    return found


def latest_state(fleet: Path, seat: str, path: str) -> str | None:
    item = latest_line(fleet, seat, path)
    if item is None:
        return None
    return str(item["state"])


def _line(payload: dict[str, str]) -> bytes:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode()


def _append_file(previous: Path, active: Path, payload: bytes) -> None:
    if previous.is_symlink() or active.is_symlink():
        raise FoilError("foil: refusing symlink")
    active.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    size = active.stat().st_size if active.is_file() else 0
    if size and size + len(payload) > LIMIT:
        os.replace(active, previous)
    descriptor = os.open(active, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def append(fleet: Path, seat: str, entries: list[tuple[str, str]]) -> None:
    """Append delivery lines. ``entries`` are ``(board path, state)``."""

    seat = _seat(seat)
    lines = []
    for path, state in entries:
        if state not in STATES:
            raise ValueError("delivery state")
        lines.append(_line({"time": _now(), "path": _check_path(path), "state": state}))
    if not lines:
        return
    previous, active = _log_paths(fleet, seat)
    with exclusive_lock(fleet / "run" / "turns" / f"{seat}.lock"):
        for payload in lines:
            _append_file(previous, active, payload)


def note(fleet: Path, seat: str, path: str, state: str) -> None:
    append(fleet, seat, [(path, state)])


def skipped(fleet: Path, seat: str, paths: list[str]) -> None:
    """Log ``skipped`` for paths renew or spawn marked delivered with no turn."""

    pending = []
    known = {item.get("path"): item.get("state") for item in read_log(fleet, seat)}
    for path in paths:
        # A later spawn must not turn a delivered path into skipped.
        if known.get(path) in {"skipped", "delivered", "printed"}:
            continue
        pending.append((path, "skipped"))
        known[path] = "skipped"
    append(fleet, seat, pending)


def _pointer_paths(fleet: Path) -> tuple[Path, Path]:
    run = fleet / "run"
    return run / "pointers.log.1", run / "pointers.log"


def read_pointers(fleet: Path) -> list[dict[str, Any]]:
    previous, active = _pointer_paths(fleet)
    return _read_lines(previous) + _read_lines(active)


def pending_pointers(fleet: Path) -> list[dict[str, str]]:
    """Open pointers, in order. A later ``listed`` line for the path drops it."""

    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in read_pointers(fleet):
        path = item.get("path")
        state = item.get("state")
        if not isinstance(path, str) or state not in _POINTER_STATES:
            continue
        if path not in latest:
            order.append(path)
        latest[path] = item
    pending = []
    for path in order:
        item = latest[path]
        if item.get("state") != "open":
            continue
        to = item.get("to")
        when = item.get("time")
        if not isinstance(to, str) or not isinstance(when, str):
            continue
        pending.append({"time": when, "to": to, "path": path})
    return pending


def note_pointer(fleet: Path, path: str, to: str) -> None:
    """The human mailed a worker. The lead's next wake lists it; nothing wakes."""

    if not SAFE_ID.fullmatch(to) or to in {"lead", "user", "foil", "master"}:
        return
    payload = _line(
        {"time": _now(), "path": _check_path(path), "to": to, "state": "open"}
    )
    previous, active = _pointer_paths(fleet)
    with exclusive_lock(fleet / "run" / "pointers.lock"):
        _append_file(previous, active, payload)


def list_pointers(fleet: Path, paths: list[str]) -> None:
    """Mark these pointers listed, so the next wake does not repeat them."""

    if not paths:
        return
    previous, active = _pointer_paths(fleet)
    with exclusive_lock(fleet / "run" / "pointers.lock"):
        for path in paths:
            payload = _line({"time": _now(), "path": _check_path(path), "state": "listed"})
            _append_file(previous, active, payload)


def _fleet_file(fleet: Path) -> Path:
    return fleet / "run" / "fleet.json"


def fleet_state(fleet: Path) -> dict[str, bool]:
    path = _fleet_file(fleet)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.is_file():
        return {"paused": False, "told": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {"paused": False, "told": False}
    if not isinstance(data, dict):
        return {"paused": False, "told": False}
    return {"paused": data.get("paused") is True, "told": data.get("told") is True}


def fleet_paused(fleet: Path) -> bool:
    return fleet_state(fleet)["paused"]


def _store_fleet(fleet: Path, *, paused: bool, told: bool) -> None:
    path = _fleet_file(fleet)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    payload = (json.dumps({"paused": paused, "told": told}, sort_keys=True) + "\n").encode()
    write_bytes(path, payload)


def pause_fleet(fleet: Path, *, told: bool) -> None:
    """Runners start no new turn. ``told`` is set when the joined lead just paused."""

    with exclusive_lock(fleet / "run" / "fleet.lock"):
        current = fleet_state(fleet)
        if current["paused"]:
            return
        _store_fleet(fleet, paused=True, told=told)


def resume_fleet(fleet: Path) -> None:
    with exclusive_lock(fleet / "run" / "fleet.lock"):
        _store_fleet(fleet, paused=False, told=False)


def tell_paused(fleet: Path) -> str | None:
    """One line the first time a joined lead runs a command while paused."""

    with exclusive_lock(fleet / "run" / "fleet.lock"):
        current = fleet_state(fleet)
        if not current["paused"] or current["told"]:
            return None
        _store_fleet(fleet, paused=True, told=True)
    return "foil: fleet is paused"
