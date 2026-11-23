"""The home: the master's mail, question cards, and action cards (section 12).

Nothing here is kept. A tap calls ``foil.api`` as ``user``.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from pathlib import Path
from typing import Any

from foil.answer import question_cards
from foil.api import answer, fleet_merge, init, roster_update, send
from foil.board import mail_name
from foil.board_ops import split_front_matter
from foil.errors import FoilError
from foil.master import (
    conversation,
    directory,
    load_record,
    pane_tail,
    queue_reason,
    turn_running,
)
from foil.project import checkout_for_key, store_root
from foil.store import MAX_BYTES, SAFE_ID
from foil_bridge import controls, settings

BODY_CAP = 8 * 1024
_KEY = re.compile(r"[a-z]+")
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,200}")
_STORE = re.compile(
    r"projects/[A-Za-z0-9][A-Za-z0-9-]{0,200}"
    r"(?:/fleets/[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
    r"(?:/board/[A-Za-z0-9][A-Za-z0-9._/-]{0,240}"
    r"|/seats/[A-Za-z0-9][A-Za-z0-9._-]{0,127})?)?"
)
_BOARD = re.compile(r"(?<![\w./-])board/(?P<rest>[A-Za-z0-9][A-Za-z0-9._/-]{0,240})")
_ACTIONS = {
    "init": {"dir"},
    "settings": {"role", "field", "value"},
    "fleet": {"project", "fleet"},
    "merge": {"project", "fleet"},
    "spawn": {"project", "fleet", "role"},
    "resume": {"project", "fleet", "seat"},
    "renew": {"project", "fleet", "seat"},
    "kill": {"project", "fleet", "seat"},
    "archive": {"project", "fleet", "seat"},
}
_OPTIONAL = {
    "init": set(),
    "settings": {"project", "layer", "before", "after", "base"},
    "fleet": {"task"},
    "merge": {"into"},
    "spawn": {"task"},
    "resume": set(),
    "renew": set(),
    "kill": set(),
    "archive": set(),
}
_LABEL = {
    "init": "Init this repository",
    "settings": "Apply this setting",
    "fleet": "Start this fleet",
    "merge": "Merge this fleet",
    "spawn": "Spawn this role",
    "resume": "Resume this seat",
    "renew": "Renew this seat",
    "kill": "Kill this seat",
    "archive": "Archive this seat",
}
_CARD_FLEET = {"fleet", "merge", "spawn", "resume", "renew", "kill", "archive"}


def _cap(text: str) -> str:
    raw = text.encode()
    if len(raw) <= BODY_CAP:
        return text
    return raw[:BODY_CAP].decode(errors="ignore") + "\n... truncated\n"


def _value_ok(action: str, key: str, value: str) -> bool:
    if not value or any(ord(char) < 32 for char in value):
        return False
    limit = 1024 if action == "init" and key == "dir" else 400
    return len(value) <= limit


def parse_card(body: str) -> tuple[dict[str, str] | None, str]:
    """A card block at the start of a message, and the prose after it."""

    lines = body.splitlines()
    if not lines or ": " not in lines[0]:
        return None, body
    name, action = lines[0].split(": ", 1)
    if name != "card" or action not in _ACTIONS:
        return None, body
    fields: dict[str, str] = {}
    index = 1
    while index < len(lines) and lines[index] != "":
        if ": " not in lines[index]:
            return None, body
        key, value = lines[index].split(": ", 1)
        if not _KEY.fullmatch(key) or key in fields or not _value_ok(action, key, value):
            return None, body
        fields[key] = value
        index += 1
    allowed = _ACTIONS[action] | _OPTIONAL[action]
    if not _ACTIONS[action] <= set(fields) <= allowed:
        return None, body
    if action in _CARD_FLEET and not _SAFE.fullmatch(fields.get("fleet", "")):
        return None, body
    if "project" in fields and not _PROJECT.fullmatch(fields["project"]):
        return None, body
    if "seat" in fields and not SAFE_ID.fullmatch(fields["seat"]):
        return None, body
    if "role" in fields and not SAFE_ID.fullmatch(fields["role"]):
        return None, body
    if action == "settings":
        layer = fields.get("layer", "global")
        if layer not in {"global", "project"}:
            return None, body
        if layer == "project" and "project" not in fields:
            return None, body
        if layer == "global" and "project" in fields:
            return None, body
        fields["layer"] = layer
        base = fields.get("base")
        if base is not None and not re.fullmatch(r"[0-9a-f]{64}", base):
            return None, body
        if "after" in fields and fields["after"] != fields["value"]:
            return None, body
    prose = "\n".join(lines[index + 1 :]) if index < len(lines) else ""
    return {"action": action, **fields}, prose


def _within(relative: str) -> Path | None:
    try:
        if relative.startswith("/") or ".." in relative.split("/") or "\x00" in relative:
            return None
        root = store_root()
        path = root / relative
        if path.is_symlink():
            return None
        resolved = path.resolve()
        base = root.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(base):
        return None
    return path


def _unlinked(relative: str) -> bool:
    current = store_root()
    for part in relative.split("/"):
        current = current / part
        try:
            if current.is_symlink():
                return False
        except OSError:
            return False
    return True


def _registry_file(fleet: Path) -> bool:
    """True when this fleet's registry is a real file under a real ``run``."""

    run = fleet / "run"
    registry = run / "registry.json"
    try:
        return not run.is_symlink() and not registry.is_symlink() and registry.is_file()
    except OSError:
        return False


def _seat_known(fleet: Path, name: str) -> bool:
    registry = fleet / "run" / "registry.json"
    if not _registry_file(fleet):
        return False
    try:
        if registry.stat().st_size > MAX_BYTES:
            return False
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    seats = payload.get("seats") if isinstance(payload, dict) else None
    if isinstance(seats, dict):
        return name in seats
    if isinstance(seats, list):
        return any(isinstance(item, dict) and item.get("name") == name for item in seats)
    return False


def describe(target: str) -> dict[str, str] | None:
    """The kind of one store-relative target, or None when it does not resolve."""

    try:
        return _describe(target)
    except (OSError, ValueError):
        return None


def _describe(target: str) -> dict[str, str] | None:
    path = _within(target)
    if path is None or not _unlinked(target):
        return None
    parts = target.split("/")
    if (
        len(parts) >= 5
        and parts[2] == "fleets"
        and parts[4] == "board"
        and path.is_file()
    ):
        relative = "/".join(parts[5:])
        fleet = store_root() / "/".join(parts[:4])
        # Imported here so evidence can import this module.
        from foil_bridge.evidence import board_listing

        if relative not in board_listing(fleet / "board"):
            return None
        return {
            "target": target,
            "kind": "file",
            "project": parts[1],
            "fleet": parts[3],
            "path": "/".join(parts[4:]),
        }
    if (
        len(parts) == 6
        and parts[2] == "fleets"
        and parts[4] == "seats"
        and _SAFE.fullmatch(parts[5])
        and _seat_known(store_root() / "/".join(parts[:4]), parts[5])
    ):
        return {
            "target": target,
            "kind": "seat",
            "project": parts[1],
            "fleet": parts[3],
            "path": parts[5],
        }
    if len(parts) == 4 and parts[2] == "fleets" and path.is_dir():
        if not _registry_file(path):
            return None
        return {
            "target": target,
            "kind": "fleet",
            "project": parts[1],
            "fleet": parts[3],
            "path": "",
        }
    record = path / "project.json"
    if len(parts) == 2 and path.is_dir() and not record.is_symlink() and record.is_file():
        return {
            "target": target,
            "kind": "project",
            "project": parts[1],
            "fleet": "",
            "path": "",
        }
    return None


def links_in(text: str, *, project: str = "", fleet: str = "") -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(target: str) -> None:
        described = describe(target)
        if described is None and target.endswith("."):
            target = target[:-1]
            described = describe(target)
        if described is None or target in seen:
            return
        seen.add(target)
        found.append(described)

    for match in _STORE.finditer(text):
        add(match.group(0))
    if project and fleet and _PROJECT.fullmatch(project) and _SAFE.fullmatch(fleet):
        for match in _BOARD.finditer(text):
            rest = match.group("rest").rstrip("/")
            if rest and ".." not in rest.split("/"):
                add(f"projects/{project}/fleets/{fleet}/board/{rest}")
    return found


def _settings_pair(fields: dict[str, str]) -> tuple[str, str]:
    """The change the card proposes. A missing before is the value on disk."""

    after = fields.get("after") or fields["value"]
    if "before" in fields:
        return fields["before"], after
    try:
        before = settings.current_value(
            fields["role"],
            fields["field"],
            layer=fields.get("layer", "global"),
            project=fields.get("project", ""),
        )
    except FoilError:
        before = ""
    return before, after


def _card_view(fields: dict[str, str]) -> dict[str, Any]:
    action = fields["action"]
    shown = []
    skip = {"base"}
    if action == "settings":
        before, after = _settings_pair(fields)
        if before:
            shown.append({"key": "before", "value": before})
        shown.append({"key": "after", "value": after})
        skip.update({"before", "after", "value"})
    order = ("dir", "project", "fleet", "role", "seat", "field", "value", "layer", "task", "into")
    for key in order:
        if key in skip:
            continue
        if key not in fields:
            continue
        if key == "layer" and fields[key] == "global" and "project" not in fields:
            continue
        shown.append({"key": key, "value": fields[key]})
    flags = controls.card_flags(fields)
    return {"action": action, "label": _LABEL[action], "fields": shown, **flags}


def snapshot() -> dict[str, Any]:
    """The home, computed from files. This stores nothing."""

    root = directory()
    record = load_record(root)
    running = turn_running(root)
    reason = queue_reason(root)
    delivered = set(record["delivered"])
    inflight = set(record["inflight"]) if running else set()
    messages = []
    for path in conversation(root):
        named = mail_name(path.name)
        if named is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        matter, body = split_front_matter(text)
        if matter is None or matter.get("contract") != "mail/v1":
            continue
        sender = named[1]
        card = None
        prose = body
        if sender == "master":
            parsed, prose = parse_card(body)
            if parsed is not None:
                card = _card_view(parsed)
        project = str(matter.get("project") or "")
        fleet = str(matter.get("fleet") or "")
        queued = ""
        if sender != "master" and path.name not in delivered and path.name not in inflight:
            queued = reason
        messages.append(
            {
                "id": path.name,
                "time": named[0],
                "from": sender,
                "body": _cap(prose),
                "queued": queued,
                "card": card,
                "links": links_in(prose, project=project, fleet=fleet),
            }
        )
    questions = []
    for question in question_cards():
        evidence = question["evidence"]
        question["links"] = []
        if evidence and "\n" not in evidence and len(evidence) <= 240:
            question["links"] = links_in(
                evidence, project=question["project"], fleet=question["fleet"]
            )
            if not question["links"]:
                question["links"] = links_in(evidence)
        questions.append(question)
    tail = pane_tail(root) if running else ""
    return {
        "messages": messages,
        "working": {"running": running, "tail": tail},
        "questions": questions,
    }


def post_message(text: str) -> str:
    if len(text.encode()) > 16 * 1024:
        raise FoilError("foil: message text is too long")
    if not text.strip():
        raise FoilError("foil: message text is empty")
    sent = send(None, "main", "user", "master", text)
    name = sent.get("name") if isinstance(sent, dict) else None
    if not isinstance(name, str) or mail_name(name) is None:
        raise FoilError("foil: could not write mail")
    return name


def post_answer(project: str, fleet: str, question_id: str, text: str) -> None:
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise FoilError(f"foil: unknown project '{project}'")
    checkout = checkout_for_key(project)
    answer(checkout, fleet, "user", question_id, text)


def _mail(name: str) -> tuple[Path, str]:
    if mail_name(name) is None or name != Path(name).name:
        raise FoilError("foil: unknown card")
    path = directory() / "mail" / name
    if path.is_symlink() or not path.is_file():
        raise FoilError("foil: unknown card")
    sender = mail_name(name)
    assert sender is not None
    return path, sender[1]


def _parsed(name: str) -> dict[str, str]:
    path, sender = _mail(name)
    if sender != "master":
        raise FoilError("foil: unknown card")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FoilError("foil: unknown card") from exc
    _matter, body = split_front_matter(text)
    parsed, _prose = parse_card(body)
    if parsed is None:
        raise FoilError("foil: unknown card")
    return parsed


def run_card(name: str, *, confirm: bool = False) -> None:
    """Run the card in one master message. The file is read again here.

    A kill or an archive is not an action. This request never runs one.
    """

    parsed = _parsed(name)
    action = parsed["action"]
    if action in {"kill", "archive"}:
        raise FoilError("foil: confirm this")
    needs = action in {"fleet", "spawn", "resume", "renew"}
    if needs and controls.card_flags(parsed)["confirm"] and not confirm:
        raise FoilError("foil: confirm this")
    with contextlib.redirect_stdout(io.StringIO()):
        if action == "init":
            init(None, "main", "user", directory=parsed["dir"])
        elif action == "settings":
            layer_name = parsed.get("layer", "global")
            project_layer = layer_name == "project"
            checkout = checkout_for_key(parsed["project"]) if project_layer else None
            before, after = _settings_pair(parsed)
            try:
                roster_update(
                    checkout,
                    "main",
                    "user",
                    parsed["role"],
                    f"{parsed['field']}={parsed['value']}",
                    project_layer=project_layer,
                    base=parsed.get("base"),
                )
            except FoilError as exc:
                settings.reraise_stale(
                    exc,
                    role=parsed["role"],
                    field=parsed["field"],
                    before=before,
                    after=after,
                    layer=layer_name,
                    project=parsed.get("project", ""),
                )
        elif action == "fleet":
            controls.invoke_card(parsed)
        elif action == "merge":
            checkout = checkout_for_key(parsed["project"])
            fleet_merge(checkout, parsed["fleet"], "user", into=parsed.get("into"))
        elif action in {"spawn", "resume", "renew"}:
            controls.invoke_card(parsed)
        else:
            raise FoilError("foil: unknown card")


def run_control_card(body: dict) -> None:
    """A kill or archive card. It runs only from this path, and only confirmed."""

    if set(body) - {"id", "confirm"} or not isinstance(body.get("id"), str):
        raise FoilError("foil: control is invalid")
    if "confirm" in body and not isinstance(body["confirm"], bool):
        raise FoilError("foil: control is invalid")
    parsed = _parsed(body["id"])
    if parsed["action"] not in {"kill", "archive"}:
        raise FoilError("foil: unknown card")
    if body.get("confirm") is not True:
        raise FoilError("foil: confirm this")
    controls.invoke_card(parsed)
