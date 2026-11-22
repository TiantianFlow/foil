"""What needs the human, computed on read, and one fleet's status line.

Neither command calls a model or writes a file. A mail body is never a field (N5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from foil.board import items_for
from foil.delivery import fleet_paused
from foil.errors import FoilError
from foil.front import parse_front
from foil.journal import read_log
from foil.journal import render as render_event
from foil.lifecycle import seat_rows
from foil.mail_ops import undelivered
from foil.project import foil_root, project_record, store_root
from foil.store import SAFE_ID, fleet_dir, fleet_ids, load_registry

JOURNAL_TAIL = 5
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RANKS = {
    "question": 0,
    "mail": 0,
    "seat": 1,
    "undelivered": 2,
    "blocked": 3,
    "paused": 4,
    "done": 4,
}
_STUCK = {"seat", "undelivered", "blocked"}
_FIELDS = (
    "id",
    "project",
    "fleet",
    "subject",
    "kind",
    "rank",
    "since",
    "title",
    "options",
    "recommended",
    "from",
    "evidence",
)


def _line(value: str) -> str:
    return " ".join(value.split())


def _stamp(value: object) -> str:
    return value if isinstance(value, str) and _TIME.fullmatch(value) else ""


def _item(kind: str, **fields: Any) -> dict[str, Any]:
    item = {
        "id": "",
        "project": "",
        "fleet": "",
        "subject": "",
        "kind": kind,
        "rank": _RANKS[kind],
        "since": "",
        "title": "",
        "options": [],
        "recommended": "",
        "from": "",
        "evidence": "",
    }
    item.update(fields)
    return item


def _questions(fields: dict[str, Any] | None, raw: str | None) -> list[dict[str, Any]] | None:
    """Question records, or None when the front matter was refused whole."""

    if raw is not None:
        return None
    if fields is None:
        return []
    value = fields.get("questions")
    if value is None or value == "" or value == []:
        return []
    entries: list[tuple[str, Any]] = []
    if isinstance(value, str):
        entries.append(("str", value))
    elif isinstance(value, list):
        for entry in value:
            if isinstance(entry, str):
                if entry.strip():
                    entries.append(("str", entry.strip()))
            elif isinstance(entry, dict):
                entries.append(("map", entry))
    else:
        return None
    used: set[str] = set()
    found = []
    for number, (kind, entry) in enumerate(entries, start=1):
        fallback = f"q{number}"
        while fallback in used:
            number += 1
            fallback = f"q{number}"
        if kind == "str":
            chosen = fallback
            found.append(
                {"id": chosen, "title": entry, "options": [], "recommended": "", "from": "",
                 "evidence": ""}
            )
            used.add(chosen)
            continue
        name = entry.get("id")
        safe = isinstance(name, str) and SAFE_ID.fullmatch(name) and name not in used
        chosen = name if safe else fallback
        options = entry.get("options")
        owner = entry.get("owner")
        evidence = entry.get("evidence")
        ask = entry.get("ask")
        recommended = entry.get("recommended")
        found.append(
            {
                "id": chosen,
                "title": ask if isinstance(ask, str) else "",
                "options": [item for item in options if isinstance(item, str)]
                if isinstance(options, list)
                else [],
                "recommended": recommended if isinstance(recommended, str) else "",
                "from": owner if owner in {"human", "master"} else "",
                "evidence": evidence if isinstance(evidence, str) else "",
            }
        )
        used.add(chosen)
    return found


def shown_questions(text: str) -> list[str]:
    """Question titles for the viewer. A refused file is one raw line."""

    front = parse_front(text)
    found = _questions(front.fields, front.raw)
    if found is None:
        shown = _line(front.raw or "")
        return [shown] if shown else []
    titles = []
    for item in found:
        title = item["title"] or item["id"]
        if title:
            titles.append(title)
    return titles


def _status(board: Path) -> tuple[dict[str, Any] | None, str, str | None]:
    path = board / "status.md"
    if path.is_symlink() or not path.is_file():
        return None, "", None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None, "", None
    front = parse_front(text)
    return front.fields, front.body, front.raw


def _section(lines: list[str], heading: str) -> list[str] | None:
    start = next((index + 1 for index, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return None
    chunk = lines[start:]
    for index, line in enumerate(chunk):
        if line.startswith("## "):
            return chunk[:index]
    return chunk


def _goal(body: str) -> str:
    """The mandate's `Done when:`, else that section's first line, else the body's."""

    lines = body.splitlines()
    mandate = _section(lines, "## Mandate")
    if mandate is not None:
        for line in mandate:
            stripped = line.strip()
            if stripped.lower().startswith("done when:"):
                rest = stripped.split(":", 1)[1].strip()
                if rest:
                    return _line(rest)
        for line in mandate:
            if line.strip():
                return _line(line)
    for line in lines:
        if line.strip():
            return _line(line)
    return ""


def _seen(fleet: Path) -> set[str]:
    path = fleet / "run" / "seats" / "user.json"
    if path.is_symlink() or not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return set()
    seen = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(seen, list):
        return set()
    return {item for item in seen if isinstance(item, str)}


def _pause_time(fleet: Path, seat: str) -> str:
    found = ""
    for event in read_log(fleet):
        if event.get("event") == "pause" and event.get("seat") == seat:
            found = _stamp(event.get("time")) or found
    return found


def fleet_items(root: Path, fleet: str, project: str) -> list[dict[str, Any]]:
    """One fleet's items, in source order. The caller sorts across fleets."""

    directory = fleet_dir(root, fleet)
    board = directory / "board"
    fields, _body, raw = _status(board)
    updated = _stamp((fields or {}).get("updated"))
    state = (fields or {}).get("state") if raw is None else None
    parsed = _questions(fields, raw)
    items: list[dict[str, Any]] = []
    if parsed is None:
        items.append(
            _item(
                "question",
                id="raw",
                subject="question:raw",
                title=_line(raw or ""),
                since=updated,
                project=project,
                fleet=fleet,
            )
        )
    else:
        for question in parsed:
            items.append(
                _item(
                    "question",
                    id=question["id"],
                    subject=f"question:{question['id']}",
                    title=question["title"],
                    options=question["options"],
                    recommended=question["recommended"],
                    **{"from": question["from"]},
                    evidence=question["evidence"],
                    since=updated,
                    project=project,
                    fleet=fleet,
                )
            )
    seen = _seen(directory)
    unread = [
        item for item in items_for(board, "user", seen) if item["relative"].startswith("mail/")
    ]
    try:
        stuck = undelivered(board)
    except FoilError:
        stuck = []
    unread_paths = {mail["relative"] for mail in unread}
    for mail in unread:
        relative = mail["relative"]
        items.append(
            _item(
                "mail",
                id=f"mail:{relative}",
                subject=f"mail:{relative}",
                title=relative,
                **{"from": mail["from"]},
                evidence=relative,
                since=_stamp(mail["time"]),
                project=project,
                fleet=fleet,
            )
        )
    try:
        rows = seat_rows(root, load_registry(root, fleet), fleet=fleet)
    except FoilError:
        rows = []
    hot = [row for row in rows if row["state"] in {"dead", "paused"}]
    hot.sort(key=lambda row: (row["state"] != "dead", row["name"]))
    folded: dict[str, list[str]] = {row["name"]: [] for row in hot}
    standalone = []
    for item in stuck:
        # Unread human mail stays one mail item, even when its delivery is stuck.
        if item["to"] == "user" and item["path"] in unread_paths:
            continue
        if item["to"] in folded:
            folded[item["to"]].append(item["path"])
        else:
            standalone.append(item)
    for row in hot:
        name = row["name"]
        paths = folded[name]
        items.append(
            _item(
                "seat",
                id=f"seat:{name}",
                subject=f"seat:{name}",
                title=f"{name} is {row['state']}",
                evidence=", ".join(paths),
                since=_pause_time(directory, name) if row["state"] == "paused" else "",
                project=project,
                fleet=fleet,
            )
        )
    for item in standalone:
        relative = item["path"]
        items.append(
            _item(
                "undelivered",
                id=f"mail:{relative}",
                subject=f"mail:{relative}",
                title=relative,
                **{"from": item["from"]},
                evidence=relative,
                since=_stamp(item["time"]),
                project=project,
                fleet=fleet,
            )
        )
    if state == "blocked" and not any(item["kind"] == "question" for item in items):
        items.append(
            _item(
                "blocked",
                id="status",
                subject="status",
                title="blocked",
                since=updated,
                project=project,
                fleet=fleet,
            )
        )
    try:
        paused = fleet_paused(directory)
    except FoilError:
        paused = False
    if paused:
        items.append(
            _item(
                "paused",
                id="fleet",
                subject="fleet",
                title="fleet paused",
                project=project,
                fleet=fleet,
            )
        )
    if state == "done":
        items.append(
            _item(
                "done",
                id="goal",
                subject="goal",
                title="done",
                since=updated,
                project=project,
                fleet=fleet,
            )
        )
    return items


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(
        enumerate(items),
        key=lambda pair: (pair[1]["rank"], pair[1]["project"], pair[1]["fleet"], pair[0]),
    )
    return [{key: item[key] for key in _FIELDS} for _index, item in ranked]


def collect(root: Path, fleets: list[str], project: str) -> list[dict[str, Any]]:
    found = []
    for fleet in fleets:
        found.extend(fleet_items(root, fleet, project))
    return _ordered(found)


def projects() -> list[tuple[str, Path]]:
    """Store projects whose checkout still resolves to that folder, in key order."""

    directory = store_root() / "projects"
    if directory.is_symlink() or not directory.is_dir():
        return []
    found = []
    for folder in sorted(directory.iterdir(), key=lambda path: path.name):
        record = folder / "project.json"
        if (
            folder.is_symlink()
            or not folder.is_dir()
            or record.is_symlink()
            or not record.is_file()
        ):
            continue
        checkout = project_record(folder).get("path") or ""
        if not checkout or not Path(checkout).exists():
            continue
        try:
            if foil_root(Path(checkout)).resolve() != folder.resolve():
                continue
        except FoilError:
            continue
        found.append((folder.name, Path(checkout)))
    return found


def collect_projects() -> list[dict[str, Any]]:
    found = []
    for key, root in projects():
        found.extend(collect(root, fleet_ids(root), key))
    return _ordered(found)


def format_items(items: list[dict[str, Any]], *, scope: str) -> str:
    lines = []
    for item in items:
        since = item["since"] or "-"
        title = _line(item["title"])
        if scope == "projects":
            prefix = f"{item['project']}\t{item['fleet']}\t"
        elif scope == "fleets":
            prefix = f"{item['fleet']}\t"
        else:
            prefix = ""
        lines.append(f"{prefix}{item['kind']}\t{since}\t{item['id']}\t{title}")
    return "\n".join(lines)


def show_items(items: list[dict[str, Any]], *, scope: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False, sort_keys=True))
        return
    text = format_items(items, scope=scope)
    if text:
        print(text)


def _record(root: Path, fleet: str, project: str) -> dict[str, Any]:
    directory = fleet_dir(root, fleet)
    fields, body, raw = _status(directory / "board")
    state = ""
    value = (fields or {}).get("state")
    if raw is None and isinstance(value, str) and value in {"working", "blocked", "done"}:
        state = value
    items = fleet_items(root, fleet, project)
    try:
        paused = fleet_paused(directory)
    except FoilError:
        paused = False
    return {
        "project": project,
        "fleet": fleet,
        "goal": _goal(body),
        "state": state,
        "paused": paused,
        "journal": read_log(directory)[-JOURNAL_TAIL:],
        "questions": [item for item in items if item["kind"] == "question"],
        "stuck": [item for item in items if item["kind"] in _STUCK],
    }


def collect_brief(root: Path, fleets: list[str], project: str) -> list[dict[str, Any]]:
    return [_record(root, fleet, project) for fleet in fleets]


def collect_brief_projects() -> list[dict[str, Any]]:
    found = []
    for key, root in projects():
        found.extend(collect_brief(root, fleet_ids(root), key))
    return found


def format_brief(records: list[dict[str, Any]], *, projects_named: bool) -> str:
    blocks = []
    for record in records:
        lines = []
        if projects_named:
            lines.append(f"project {record['project']}")
        lines.append(f"fleet {record['fleet']}")
        lines.append(f"goal: {record['goal'] or '-'}")
        lines.append(f"state: {record['state'] or '-'}")
        lines.append(f"paused: {'yes' if record['paused'] else 'no'}")
        lines.append("journal:")
        if record["journal"]:
            lines.extend(f"  {render_event(event)}" for event in record["journal"])
        else:
            lines.append("  -")
        lines.append("questions:")
        if record["questions"]:
            lines.extend(
                f"  {item['id']}\t{_line(item['title'])}" for item in record["questions"]
            )
        else:
            lines.append("  -")
        lines.append("stuck:")
        if record["stuck"]:
            lines.extend(
                f"  {item['kind']}\t{item['id']}\t{_line(item['title'])}"
                for item in record["stuck"]
            )
        else:
            lines.append("  -")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def show_brief(records: list[dict[str, Any]], *, projects_named: bool, as_json: bool) -> None:
    if as_json:
        print(json.dumps(records, ensure_ascii=False, sort_keys=True))
        return
    text = format_brief(records, projects_named=projects_named)
    if text:
        print(text)
