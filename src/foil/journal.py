"""Append-only fleet journal: one line per fact, and no body or turn output (N5)."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.errors import FoilError
from foil.store import exclusive_lock, scan

LIMIT = 64 * 1024
TEXT_LIMIT = 400
POLL = 0.2
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_BARE = re.compile(r"[A-Za-z0-9_./:-]+")
_FIELDS = {
    "spawn": ("seat",),
    "turn_start": ("seat", "kind"),
    "turn_end": ("seat", "exit"),
    "mail_sent": ("from", "to", "path"),
    "mail_delivered": ("seat", "path"),
    "retry": ("seat", "exit", "wait"),
    "pause": ("seat", "exit"),
    "resume": ("seat",),
    "renew": ("seat",),
    "kill": ("seat",),
    "join": ("seat",),
    "leave": ("seat",),
    "decision": ("by", "text", "why"),
}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_since(value: str) -> str:
    if not _TIME.fullmatch(value):
        raise FoilError("foil: --since is YYYY-MM-DDTHH:MM:SSZ")
    return value


def check_line(value: str, label: str) -> str:
    """One short line, with the same credential refusal as a board write."""

    if any(ord(char) < 32 for char in value):
        raise FoilError(f"foil: {label} must be one line")
    if not value.strip():
        raise FoilError(f"foil: {label} is empty")
    if len(value) > TEXT_LIMIT:
        raise FoilError(f"foil: {label} is too long")
    scan(value)
    return value


def _run(fleet: Path) -> Path:
    return fleet / "run"


def _paths(fleet: Path) -> tuple[Path, Path]:
    run = _run(fleet)
    return run / "journal.log.1", run / "journal.log"


def _events(path: Path) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise FoilError("foil: refusing symlink")
        if not path.is_file():
            return []
        if path.stat().st_size > LIMIT:
            raise FoilError("foil: journal cannot be read")
        raw_lines = path.read_bytes().splitlines()
    except OSError as exc:
        raise FoilError("foil: journal cannot be read") from exc
    found: list[dict[str, Any]] = []
    for raw in raw_lines:
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(item, dict) and isinstance(item.get("time"), str):
            found.append(item)
    return found


def read_log(fleet: Path, since: str | None = None) -> list[dict[str, Any]]:
    """The rotated file, then the active one, oldest first."""

    previous, active = _paths(fleet)
    events = _events(previous) + _events(active)
    if since is None:
        return events
    return [item for item in events if item["time"] >= since]


def _number(fleet: Path) -> int:
    highest = 0
    for event in read_log(fleet):
        number = event.get("n")
        if type(number) is int and number > highest:
            highest = number
    return highest + 1


def _line(event: dict[str, Any]) -> bytes:
    text = json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode()


def _append(fleet: Path, payload: bytes) -> None:
    previous, active = _paths(fleet)
    if previous.is_symlink() or active.is_symlink():
        raise FoilError("foil: refusing symlink")
    size = active.stat().st_size if active.is_file() else 0
    if size and size + len(payload) > LIMIT:
        os.replace(active, previous)
    descriptor = os.open(active, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, payload)
    finally:
        os.close(descriptor)


def _check(event: str, fields: dict[str, Any]) -> None:
    if tuple(fields) != _FIELDS[event]:
        raise ValueError(f"journal fields for {event}")
    for key, value in fields.items():
        if key in {"exit", "wait"}:
            number = int if key == "exit" else (int, float)
            if type(value) is bool or not isinstance(value, number):
                raise ValueError(f"journal {key}")
            continue
        if not isinstance(value, str) or any(ord(char) < 32 for char in value):
            raise ValueError(f"journal {key}")
    if event == "turn_start" and fields["kind"] not in {"start", "turn"}:
        raise ValueError("journal kind")


def note(fleet: Path, event: str, **fields: Any) -> None:
    """Append one fact. ``fields`` are that event's fields, in display order."""

    ordered = {key: fields[key] for key in _FIELDS[event]}
    _check(event, ordered)
    with exclusive_lock(_run(fleet) / "journal.lock"):
        record = {"time": _now(), "n": _number(fleet), "event": event, **ordered}
        _append(fleet, _line(record))


def sent(fleet: Path, board: Path, sender: str, to: str, path: Path) -> None:
    relative = path.resolve().relative_to(board.resolve()).as_posix()
    note(fleet, "mail_sent", **{"from": sender, "to": to, "path": relative})


def _shown(value: object) -> str:
    if isinstance(value, str) and _BARE.fullmatch(value):
        return value
    if type(value) is int:
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def render(event: dict[str, Any]) -> str:
    kind = str(event.get("event", ""))
    parts = [str(event.get("time", "")), kind]
    for key in _FIELDS.get(kind, ()):
        if key in event:
            parts.append(f"{key}={_shown(event[key])}")
    return " ".join(parts)


def show_log(events: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(events, ensure_ascii=False, sort_keys=True))
        return
    for event in events:
        print(render(event))


def _print_one(event: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(event, ensure_ascii=False, sort_keys=True), flush=True)
        return
    print(render(event), flush=True)


def stream(fleet: Path, since: str | None, *, as_json: bool, stop=None) -> None:
    """Print the selected lines, then each new line. ``stop`` ends a test."""

    last = 0
    while True:
        for event in read_log(fleet, since):
            number = event.get("n")
            if type(number) is int and number > last:
                _print_one(event, as_json=as_json)
                last = number
        if stop is not None and stop():
            return
        time.sleep(POLL)
