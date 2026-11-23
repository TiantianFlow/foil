"""Fleet and seat controls. A tap calls ``foil.api`` as ``user``.

Kill and archive always ask first. Any other control asks when that
fleet's mandate does not cover it. The file is read again on the tap.
Nothing here is kept.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

from foil.api import (
    fleet_pause,
    fleet_resume,
    seat_archive,
    seat_kill,
    seat_renew,
    seat_resume,
    seat_spawn,
)
from foil.errors import FoilError
from foil.front import parse_front
from foil.presets import rotation_index, rotation_key, spawn_entry
from foil.project import checkout_for_key, foil_root
from foil.roster_ops import roster_list_payload
from foil.store import FLEET_ID, SAFE_ID, fleet_dir, load_registry

_STATUS_CAP = 1024 * 1024
_TASK_CAP = 16 * 1024
_WORD = re.compile(r"[A-Za-z]+")
_FIELDS = {"op", "project", "fleet", "seat", "role", "task", "confirm"}
_ALWAYS = {"kill", "archive"}
_WORDS = {
    "pause": "pause",
    "resume-fleet": "resume",
    "resume": "resume",
    "renew": "renew",
    "spawn": "spawn",
    "fleet": "fleet",
}
_OPS = {
    "pause": (set(), set()),
    "resume-fleet": (set(), set()),
    "resume": ({"seat"}, set()),
    "renew": ({"seat"}, set()),
    "spawn": ({"role"}, {"task"}),
    "kill": ({"seat"}, set()),
    "archive": ({"seat"}, set()),
    "fleet": (set(), {"task"}),
}


def _section(lines: list[str]) -> list[str] | None:
    start = next(
        (index + 1 for index, line in enumerate(lines) if line.strip() == "## Mandate"), None
    )
    if start is None:
        return None
    chunk = lines[start:]
    for index, line in enumerate(chunk):
        if line.startswith("## "):
            return chunk[:index]
    return chunk


def _labeled(lines: list[str], label: str) -> set[str]:
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith(label):
            rest = stripped.split(":", 1)[1]
            return {word.lower() for word in _WORD.findall(rest)}
    return set()


def mandate_words(checkout: Path, fleet: str) -> tuple[set[str], set[str]]:
    """Words on ``May decide:`` and ``Must stop for:``. Unreadable covers nothing."""

    if not FLEET_ID.fullmatch(fleet):
        return set(), set()
    board = fleet_dir(checkout, fleet) / "board"
    path = board / "status.md"
    try:
        if board.is_symlink() or path.is_symlink() or not path.is_file():
            return set(), set()
        if path.stat().st_size > _STATUS_CAP:
            return set(), set()
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set(), set()
    lines = _section(parse_front(text).body.splitlines())
    if lines is None:
        return set(), set()
    return _labeled(lines, "may decide:"), _labeled(lines, "must stop for:")


def needs_confirm(checkout: Path, fleet: str, op: str) -> bool:
    """Kill and archive always. Anything the mandate does not cover, too."""

    if op in _ALWAYS:
        return True
    word = _WORDS[op]
    may, stop = mandate_words(checkout, fleet)
    return word not in may or word in stop


def card_flags(fields: dict[str, str]) -> dict[str, object]:
    """How the home posts one card. Kill and archive are never an action."""

    action = fields["action"]
    if action in _ALWAYS:
        return {"confirm": True, "route": "control"}
    if action not in _WORDS:
        return {"confirm": False, "route": "act"}
    try:
        checkout = _checkout(fields["project"])
        confirm = needs_confirm(checkout, fields["fleet"], action)
    except (FoilError, OSError):
        confirm = True
    return {"confirm": confirm, "route": "act"}


def next_roles(checkout: Path, fleet: str) -> list[dict[str, object]]:
    """Each role and the harness and model its next spawn takes (F3)."""

    try:
        registry = load_registry(checkout, fleet)
        templates = roster_list_payload(checkout)["templates"]
    except (FoilError, OSError):
        return []
    confirm = needs_confirm(checkout, fleet, "spawn")
    rows = []
    for template in templates:
        if template.get("error"):
            continue
        role = str(template["role"])
        entries = template.get("harness") or []
        try:
            key = rotation_key(checkout, role, entries, fleet)
            count = rotation_index(registry, role, key)
            chosen = spawn_entry(checkout, role, entries, count, fleet)
        except FoilError as exc:
            rows.append(
                {"role": role, "harness": "", "model": "", "note": str(exc), "confirm": confirm}
            )
            continue
        rows.append(
            {
                "role": role,
                "harness": chosen["id"],
                "model": chosen["model"],
                "note": "",
                "confirm": confirm,
            }
        )
    return rows


def _checkout(project: str) -> Path:
    if not isinstance(project, str):
        raise FoilError("foil: control is invalid")
    checkout = checkout_for_key(project)
    if foil_root(checkout).name != project:
        raise FoilError(f"foil: unknown project '{project}'")
    return checkout


def _task(value: object) -> str | None:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise FoilError("foil: control is invalid")
    if len(value.encode()) > _TASK_CAP:
        raise FoilError("foil: task text is too long")
    return value


def apply(body: dict) -> None:
    """One page control. The body names the op. It does not name a command."""

    if set(body) - _FIELDS or "id" in body:
        raise FoilError("foil: control is invalid")
    op = body.get("op")
    project = body.get("project")
    fleet = body.get("fleet")
    if not isinstance(op, str) or op not in _OPS or not isinstance(fleet, str):
        raise FoilError("foil: control is invalid")
    if "confirm" in body and not isinstance(body["confirm"], bool):
        raise FoilError("foil: control is invalid")
    if not FLEET_ID.fullmatch(fleet):
        raise FoilError("foil: control is invalid")
    required, optional = _OPS[op]
    present = set(body) - {"op", "project", "fleet", "confirm"}
    if not required <= present <= required | optional:
        raise FoilError("foil: control is invalid")
    seat = body.get("seat")
    role = body.get("role")
    if "seat" in present and (not isinstance(seat, str) or not SAFE_ID.fullmatch(seat)):
        raise FoilError("foil: control is invalid")
    if "role" in present and (not isinstance(role, str) or not SAFE_ID.fullmatch(role)):
        raise FoilError("foil: control is invalid")
    task = _task(body["task"]) if "task" in present else None
    checkout = _checkout(project)
    if needs_confirm(checkout, fleet, op) and body.get("confirm") is not True:
        raise FoilError("foil: confirm this")
    invoke(
        checkout,
        fleet,
        op,
        seat=seat if isinstance(seat, str) else None,
        role=role if isinstance(role, str) else None,
        task=task,
    )


def invoke_card(fields: dict[str, str]) -> None:
    """Run one card's op. The caller has already checked confirmation."""

    invoke(
        _checkout(fields["project"]),
        fields["fleet"],
        fields["action"],
        seat=fields.get("seat"),
        role=fields.get("role"),
        task=fields.get("task"),
    )


def invoke(
    checkout: Path,
    fleet: str,
    op: str,
    *,
    seat: str | None = None,
    role: str | None = None,
    task: str | None = None,
) -> None:
    """The matching ``foil.api`` call, as ``user``."""

    with contextlib.redirect_stdout(io.StringIO()):
        if op == "pause":
            fleet_pause(checkout, fleet, "user")
        elif op == "resume-fleet":
            fleet_resume(checkout, fleet, "user")
        elif op == "resume":
            seat_resume(checkout, fleet, "user", seat)
        elif op == "renew":
            seat_renew(checkout, fleet, "user", seat or "")
        elif op == "spawn":
            seat_spawn(checkout, fleet, "user", role or "", task=task)
        elif op == "kill":
            seat_kill(checkout, fleet, "user", name=seat)
        elif op == "archive":
            seat_archive(checkout, fleet, "user", seat or "")
        elif op == "fleet":
            seat_spawn(checkout, fleet, "user", "lead", task=task)
        else:
            raise FoilError("foil: control is invalid")
