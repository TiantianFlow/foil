"""`foil view`: a read-only page for one project on 127.0.0.1. It writes nothing."""

from __future__ import annotations

import errno
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import stat
import threading
from collections.abc import Callable
from contextlib import suppress
from html import escape
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from foil.errors import FoilError, assert_no_secret
from foil.lifecycle import peek_text, seat_rows
from foil.memory import read_lessons
from foil.presets import fleet_overrides, load_preset, preset_ids, read_maps, select_harness
from foil.project import cached_common_dirs, foil_root, project_record
from foil.roster_ops import roster_rows
from foil.store import SAFE_ID, load_registry

BASE_PORT = 7460
PORT_SPAN = 50
PREVIEW = 1024 * 1024
CHUNK = 64 * 1024
MAX_LINES = 1280
DEADLINE = 10
SLOTS = 8
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
HELP = (
    "One project shares roles, harnesses, and the default mapping. Each fleet has its own "
    "seats, board, and posts. This viewer does not type into a pane and does not decide "
    "progress. Progress is status.md."
)
TIPS = {
    "Lessons": "Project lessons. They outlive fleets and are copied into a seat at the next "
    "spawn or resume. States are proposed, accepted, rejected, and superseded. This is not "
    "the fleet override list.",
    "Roles": "A role is a description, a persona, a worktree flag, and a permission. auto "
    "asks the harness to skip prompts. ask stops at the first approval. Worktree means that "
    "seat gets its own branch. The description is the first line of the persona.",
    "Harnesses": "How one CLI is launched, and the model ids that list names. Spawn does not "
    "refuse a model that is absent from the list. Installed means the command is on PATH. "
    "A file harnesses/<id>.toml replaces that one id.",
    "Default mapping": "The shared default in mapping.toml. The next spawn takes the row at "
    "that role's seat count, including killed seats. An omitted model starts the harness "
    "with no model. A fleet override replaces the model only, and only on the next spawn. "
    "Resume keeps the harness and model recorded on the seat.",
    "Attach": "The session name stored for this fleet. The viewer does not type into the pane.",
    "Workspace": "The checkout recorded for the project. Every fleet on this repository "
    "shares it. Seat worktrees are separate branches.",
    "Overrides": "This fleet's overrides.toml. The last row for a role is the model the next "
    "spawn uses. The harness still comes from the default mapping. A running seat keeps its "
    "last launch. Resume does not read this file.",
    "Agents": "Alive means the window exists and the process is running. Dead means the "
    "window is gone or the process has exited, and the seat was not killed. A dead window "
    "can still be peeked. Killed means foil seat kill, and that name stays reserved.",
    "Board": "This fleet's shared folder. Mail is to one seat. Notes are scratch and wake "
    "nobody. status.md is the lead's report, including the checklist. tasks/ is the brief. "
    "results/ is the report. The viewer shows the files as written.",
    "Posts": "Threads under this fleet's board/room/. A post wakes every other alive seat in "
    "this fleet. A reply stays in that thread. A decline is a file and types nothing. A "
    "hand-written file there wakes nobody. Status still comes from status.md.",
    "Pane": "Raw tmux capture. The viewer does not decide that the seat is busy, blocked, or "
    "waiting on a dialog.",
}
_CSS = """
:root{--bg:#fff;--fg:#1f1f1f;--muted:#737373;--soft:#f5f5f5;--line:#e5e5e5;--ok:#10b981;
--bad:#dc2626;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#171717;--fg:#fafafa;--muted:#a3a3a3;
--soft:#262626;--line:#333;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,system-ui,sans-serif}a{color:inherit}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
header{display:flex;gap:16px;align-items:center;height:44px;padding:0 24px;
border-bottom:1px solid var(--line)}header a{text-decoration:none}
header .name{font-weight:600}header .link{color:var(--muted)}
main{max-width:72rem;margin:0 auto;padding:24px;display:grid;gap:32px}
h1{font-size:20px;margin:0}h2{font-size:14px;margin:0 0 8px;display:flex;gap:6px;
align-items:center}h3{font-size:12px;margin:0;padding:6px 12px;color:var(--muted);
border-top:1px solid var(--line)}.muted,nav a{color:var(--muted)}nav a{text-decoration:none}
.box{border:1px solid var(--line);border-radius:6px;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 12px;
vertical-align:top;border-top:1px solid var(--line)}th{border-top:0;font-weight:500;
color:var(--muted);font-size:13px}td small{display:block;color:var(--muted);font-size:12px}
pre{margin:0;padding:12px 16px;white-space:pre;overflow-x:auto;background:var(--soft)}
.facts{display:grid;grid-template-columns:110px 1fr;gap:12px;align-items:center}
.facts>span{font-size:12px;color:var(--muted);display:flex;gap:6px}
.cmd{display:flex;gap:8px;align-items:center;border:1px solid var(--line);
border-radius:6px;background:var(--soft);padding:4px 4px 4px 12px}.cmd code{flex:1}
button{font:inherit;font-size:12px;border:0;background:none;color:var(--muted);cursor:pointer}
.files{display:grid;gap:24px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
ul{list-style:none;margin:0;padding:0}li a{display:flex;gap:8px;padding:8px 12px;
text-decoration:none;border-top:1px solid var(--line)}li:first-child a{border-top:0}
li a:hover{background:var(--soft)}li a span{margin-left:auto;color:var(--muted);font-size:12px}
.bar{margin:0;padding:6px 12px;display:flex;gap:12px}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px;
background:var(--muted)}.alive{background:var(--ok)}.killed{background:var(--bad)}
.err{color:var(--bad)}.empty{margin:0;padding:20px;text-align:center;color:var(--muted)}
.tip{position:relative;display:inline-block}.tip summary{list-style:none;cursor:pointer;
color:var(--muted);font-size:11px;border:1px solid var(--muted);border-radius:50%;
width:15px;height:15px;line-height:13px;text-align:center}
.tip summary::-webkit-details-marker{display:none}
.tip p{position:absolute;z-index:1;left:0;top:20px;width:18rem;margin:0;padding:12px;
font-size:12px;font-weight:400;line-height:1.5;color:var(--fg);background:var(--bg);
border:1px solid var(--line);border-radius:6px;box-shadow:0 4px 12px #0002}
"""
_SCRIPT = """document.querySelectorAll('button[data-copy]').forEach(b=>b.addEventListener(
'click',async()=>{try{await navigator.clipboard.writeText(b.dataset.copy);
b.textContent='Copied'}catch(e){b.textContent='Copy failed'}
setTimeout(()=>{b.textContent='Copy'},1600)}))"""


class _Missing(Exception):
    """A fleet or seat this project does not have."""


def _error(exc: BaseException) -> str:
    return str(exc) if isinstance(exc, FoilError) else "foil: unreadable"


def _failed(exc: BaseException) -> str:
    return f'<p class="empty err">{escape(_error(exc))}</p>'


def _guard(build: Callable[[], str]) -> str:
    """One section. Any failure inside it is one line, and the rest of the page stays."""

    try:
        return build()
    except Exception as exc:
        return _failed(exc)


def _tip(title: str) -> str:
    return (
        f'<details class="tip"><summary aria-label="About {escape(title)}">i</summary>'
        f"<p>{escape(TIPS[title])}</p></details>"
    )


def _section(title: str, inner: str, *, meta: str = "", note: str = "") -> str:
    tip = _tip(title) if title in TIPS else ""
    extra = f' <span class="muted">{escape(meta)}</span>' if meta else ""
    footer = f'<p class="muted">{escape(note)}</p>' if note else ""
    heading = f"<h2>{escape(title)}{tip}{extra}</h2>"
    return f'<section>{heading}<div class="box">{inner}</div>{footer}</section>'


def _table(headers: list[str], rows: list[list[str]], empty: str = "None.") -> str:
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>'
    head = "".join(f"<th>{escape(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _mono(text: str) -> str:
    return f'<span class="mono">{escape(text)}</span>'


def _faded(text: str) -> str:
    return f'<span class="muted">{escape(text)}</span>'


def _branch(row: dict) -> str:
    return _mono(row["branch"]) if row["branch"] else _faded("no branch")


def _state(state: str) -> str:
    return f'<span class="dot {escape(state)}"></span>{escape(state)}'


def _command(command: str | None) -> str:
    if command is None:
        value = _faded("No session yet. This fleet has not started.")
    else:
        value = (
            f'<div class="cmd"><code>{escape(command)}</code>'
            f'<button type="button" data-copy="{escape(command)}">Copy</button></div>'
        )
    return f"<span>Attach{_tip('Attach')}</span>{value}"


def _crumbs(items: list[tuple[str, str | None]]) -> str:
    parts = [
        f'<a href="{escape(href)}">{escape(label)}</a>'
        if href
        else f"<strong>{escape(label)}</strong>"
        for label, href in items
    ]
    return f'<nav class="mono">{" / ".join(parts)}</nav>'


def _size(count: int) -> str:
    for unit in ("bytes", "KiB", "MiB"):
        if count < 1024 or unit == "MiB":
            return f"{count} {unit}" if unit == "bytes" else f"{count:.1f} {unit}"
        count /= 1024
    return ""


def _checkout(root: Path) -> str:
    return project_record(foil_root(root)).get("path") or str(root)


def _project_name(root: Path) -> str:
    return Path(_checkout(root)).name


def _fleet_ids(root: Path) -> list[str]:
    directory = foil_root(root) / "fleets"
    if directory.is_symlink() or not directory.is_dir():
        return []
    return sorted(
        item.name
        for item in directory.iterdir()
        if SAFE_ID.fullmatch(item.name) and item.is_dir() and not item.is_symlink()
    )


def _argv(command: list[str]) -> str:
    """Launch argv as written, with credential-shaped tokens hidden (best effort)."""

    shown: list[str] = []
    hide_next = False
    for token in command:
        name, equals, _value = token.partition("=")
        secret_flag = False
        if token.startswith("-"):
            try:
                assert_no_secret({name.lstrip("-").replace("-", "_"): ""})
            except FoilError:
                secret_flag = True
        try:
            assert_no_secret(token)
            hidden = hide_next
        except FoilError:
            hidden = True
        if secret_flag and equals:
            shown.append(f"{name}=[hidden]")
        else:
            shown.append("[hidden]" if hidden else shlex.quote(token) if " " in token else token)
        hide_next = secret_flag and not equals
    return " ".join(shown)


def _fleets_page(root: Path) -> str:
    def fleets() -> str:
        rows = []
        for fleet in _fleet_ids(root):
            link = f'<a class="mono" href="/fleets/{quote(fleet)}">{escape(fleet)}</a>'
            try:
                states = [row["state"] for row in seat_rows(root, load_registry(root, fleet))]
            except Exception as exc:
                rows.append([link, f'<span class="err">{escape(_error(exc))}</span>', "", ""])
                continue
            counts = [str(states.count(state)) for state in ("alive", "dead", "killed")]
            rows.append([link, *counts])
        return _table(["Fleet", "Alive", "Dead", "Killed"], rows, "No fleets.")

    def lessons() -> str:
        rows = [
            [_mono(str(item.get("id", ""))), escape(str(item.get("state", ""))),
             escape(" ".join(str(item.get("text", "")).split()))]
            for item in read_lessons(root, all_lessons=True)
        ]
        return _table(["Id", "State", "Text"], rows, "No lessons.")

    return (
        f'<div><h1>Fleets</h1><p class="muted">{escape(HELP)}</p></div>'
        + _section("Fleets", _guard(fleets))
        + _section("Lessons", _guard(lessons))
    )


def _settings_page(root: Path) -> str:
    def roles() -> str:
        loaded, personas = roster_rows(root)
        rows = [
            [_mono(name), escape(row["description"]), escape(row["permission"]),
             "yes" if row["worktree"] else _faded("no")]
            if row is not None
            else [_mono(name), '<span class="err">invalid template</span>', "", ""]
            for name, row in loaded
        ]
        rows += [
            [_mono(f"+ {item['name']}"), escape(item["description"]), _faded("no role"), ""]
            for item in personas
        ]
        return _table(["Role", "Description", "Permission", "Worktree"], rows)

    skipped: list[tuple[str, str]] = []

    def harnesses() -> str:
        ids, found = preset_ids(root)
        skipped.extend(found)
        rows = []
        for harness_id in ids:
            try:
                preset = load_preset(root, harness_id)
            except Exception as exc:
                rows.append([_mono(harness_id), _faded("invalid"), "", escape(_error(exc))])
                continue
            env = preset["env"]
            names = f"<small>env: {escape(', '.join(env))}</small>" if env else ""
            installed = "yes" if shutil.which(preset["command"][0]) else _faded("no")
            models = _mono(", ".join(preset["models"])) if preset["models"] else _faded("none")
            launch = f"<code>{escape(_argv(preset['command']))}</code>{names}"
            rows.append([_mono(harness_id), installed, models, launch])
        return _table(["Id", "Installed", "Model ids", "Launch command"], rows)

    def mapping() -> str:
        rows = [
            [_mono(item["role"]), _mono(item["id"]),
             _mono(item["model"]) if item["model"] else _faded("no model")]
            for item in read_maps(root)
        ]
        return _table(["Role", "Harness", "Model"], rows, "No rows.")

    harness_box = _guard(harnesses)
    note = "; ".join(f"{path} skipped: {reason}" for path, reason in skipped)
    return (
        _crumbs([(_project_name(root), "/"), ("Settings", None)])
        + "<h1>Settings</h1>"
        + _section("Roles", _guard(roles))
        + _section("Harnesses", harness_box, note=note)
        + _section("Default mapping", _guard(mapping))
    )


def _last_overrides(root: Path, fleet: str) -> dict[str, dict[str, str]]:
    last: dict[str, dict[str, str]] = {}
    for item in fleet_overrides(root, fleet):
        last.pop(item["role"], None)
        last[item["role"]] = item
    return last


def _next_models(root: Path, fleet: str, registry: dict) -> dict[str, str]:
    """Per role, the override model when it changes what the next spawn takes (F3)."""

    try:
        last = _last_overrides(root, fleet)
        maps = read_maps(root)
    except Exception:
        return {}
    found = {}
    for role, item in last.items():
        entries = [row for row in maps if row["role"] == role]
        if not entries:
            continue
        stored = sum(seat["template"] == role for seat in registry["seats"].values())
        if item["model"] != select_harness(entries, stored)["model"]:
            found[role] = item["model"]
    return found


def _attach(registry: dict, row: dict | None = None) -> str | None:
    session = str(registry.get("tmux_session") or "")
    if not session:
        return None
    command = f"tmux attach -t {shlex.quote('=' + session)}"
    if row is not None and row["owned"]:
        command += f" \\; select-window -t {shlex.quote(row['window_id'])}"
    return command


def _board_files(root: Path, fleet: str) -> list[tuple[str, int]]:
    board = foil_root(root) / "fleets" / fleet / "board"
    if board.is_symlink() or not board.is_dir():
        return []
    found = []
    for top, dirs, names in os.walk(board):
        base = Path(top)
        dirs[:] = [name for name in dirs if not name.startswith(".")]
        for name in names:
            path = base / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if name.startswith(".") or not stat.S_ISREG(info.st_mode):
                continue
            found.append((path.relative_to(board).as_posix(), info.st_size))
    return sorted(found, key=lambda item: (item[0] != "status.md", item[0]))


def _open_listed(root: Path, fleet: str, rel: str) -> int:
    """Open a listed board file without following a symlink anywhere on its path."""

    parts = ["fleets", fleet, "board", *rel.split("/")]
    folder = os.open(foil_root(root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=folder)
            os.close(folder)
            folder = child
        handle = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except OSError:
        raise FoilError(f"foil: cannot read: {rel}") from None
    finally:
        os.close(folder)
    if not stat.S_ISREG(os.fstat(handle).st_mode):
        os.close(handle)
        raise FoilError(f"foil: cannot read: {rel}")
    return handle


def _preview(root: Path, fleet: str, rel: str, here: str) -> str:
    handle = _open_listed(root, fleet, rel)
    try:
        size = os.fstat(handle).st_size
        data = bytearray()
        while len(data) < PREVIEW:
            chunk = os.read(handle, PREVIEW - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(handle)
    text = data.decode("utf-8", errors="replace")
    cut = _faded(f"Showing the first 1 MiB of {_size(size)}.") if size > PREVIEW else ""
    raw = f"{here}/raw?open={quote(rel)}"
    return (
        f'<p class="bar"><a href="{escape(here)}">&larr; Back</a>{_mono(rel)}'
        f'<a href="{escape(raw)}">Raw</a>{cut}</p><pre>{escape(text)}</pre>'
    )


def _file_rows(files: list[tuple[str, int]], here: str) -> str:
    items = [
        f'<li><a href="{escape(here + "?open=" + quote(rel))}"><code>{escape(rel)}</code>'
        f"<span>{_size(size)}</span></a></li>"
        for rel, size in files
    ]
    return f"<ul>{''.join(items)}</ul>"


def _board_sections(root: Path, fleet: str, opened: str, here: str) -> str:
    try:
        files = _board_files(root, fleet)
    except Exception as exc:
        return _section("Board", _failed(exc)) + _section("Posts", _failed(exc))
    board = [item for item in files if not item[0].startswith("room/")]
    posts = [item for item in files if item[0].startswith("room/")]

    def inner(group: list[tuple[str, int]], empty: str, threads: bool) -> str:
        if any(rel == opened for rel, _size in group):
            return _guard(lambda: _preview(root, fleet, opened, here))
        if not group:
            return f'<p class="empty">{empty}</p>'
        if not threads:
            return _file_rows(group, here)
        by_thread: dict[str, list[tuple[str, int]]] = {}
        for rel, size in group:
            parts = rel.split("/")
            by_thread.setdefault(parts[1] if len(parts) > 2 else "room", []).append((rel, size))
        return "".join(
            f"<h3>{escape(thread)}</h3>{_file_rows(items, here)}"
            for thread, items in sorted(by_thread.items())
        )

    def meta(group: list) -> str:
        return f"{len(group)} {'file' if len(group) == 1 else 'files'}"

    return (
        '<div class="files">'
        + _section("Board", inner(board, "No files.", False), meta=meta(board))
        + _section("Posts", inner(posts, "No posts.", True), meta=meta(posts))
        + "</div>"
    )


def _fleet_page(root: Path, fleet: str, opened: str) -> str:
    here = f"/fleets/{quote(fleet)}"
    try:
        registry = load_registry(root, fleet)
        rows = seat_rows(root, registry)
        attach, failure = _command(_attach(registry)), None
    except Exception as exc:
        registry, rows, failure = None, [], exc
        attach = f"<span>Attach{_tip('Attach')}</span>{_failed(exc)}"

    def overrides() -> str:
        last = _last_overrides(root, fleet)
        noted = any(item["note"] for item in last.values())
        cells = [
            [_mono(item["role"]), _mono(item["model"])] + ([escape(item["note"])] if noted else [])
            for item in last.values()
        ]
        return _table(
            ["Role", "Next spawn model"] + (["Note"] if noted else []),
            cells,
            "No overrides. The next spawn uses the default mapping.",
        )

    def agents() -> str:
        if registry is None:
            return _failed(failure or Exception())
        upcoming = _next_models(root, fleet, registry)
        cells = []
        for row in rows:
            model = _mono(row["model"]) if row["model"] else _faded("no model")
            if row["template"] in upcoming:
                model += f"<small>next spawn {_mono(upcoming[row['template']])}</small>"
            link = escape(f"{here}/seats/{quote(row['name'])}")
            cells.append(
                [f'<a class="mono" href="{link}">{escape(row["name"])}</a>',
                 _mono(row["template"]), escape(row["description"]), _state(row["state"]),
                 _mono(row["harness"]), model, _branch(row)]
            )
        headers = ["Name", "Role", "Description", "State", "Harness", "Model", "Branch"]
        return _table(headers, cells, "No seats.")

    workspace = _guard(lambda: f"<code>{escape(_checkout(root))}</code>")
    noun = "seat" if len(rows) == 1 else "seats"
    return (
        _crumbs([(_project_name(root), "/"), (fleet, None)])
        + f'<div class="facts">{attach}'
        + f"<span>Workspace{_tip('Workspace')}</span>{workspace}</div>"
        + _section("Overrides", _guard(overrides))
        + _section("Agents", _guard(agents), meta=f"{len(rows)} {noun}")
        + _board_sections(root, fleet, opened, here)
    )


def _lines(value: str) -> int:
    return int(value) if value.isdecimal() and 1 <= int(value) <= MAX_LINES else 40


def _seat_page(root: Path, fleet: str, name: str, lines: int) -> str:
    registry = load_registry(root, fleet)
    if not SAFE_ID.fullmatch(name) or name not in registry["seats"]:
        raise _Missing(f"No seat named {name} in fleet {fleet}.")
    row = next(item for item in seat_rows(root, registry) if item["name"] == name)
    upcoming = _next_models(root, fleet, registry).get(row["template"], "")
    launched = f"{row['harness']} / {row['model']}" if row["model"] else row["harness"]
    model = _mono(launched) + (f"<small>next spawn {_mono(upcoming)}</small>" if upcoming else "")
    facts = [
        ("Seat", _mono(name)), ("Role", _mono(row["template"])), ("State", _state(row["state"])),
        ("Harness / model", model), ("Branch", _branch(row)),
    ]
    here = f"/fleets/{quote(fleet)}/seats/{quote(name)}"
    try:
        pane = peek_text(root, name, lines=lines, fleet=fleet)
    except Exception as exc:
        back = ""
        if lines > 40:
            shorter = max(40, lines // 2)
            back = f' <a href="{escape(here)}?lines={shorter}">Show {shorter} lines</a>'
        pane_box, meta = f'<p class="empty err">{escape(_error(exc))}{back}</p>', ""
    else:
        more = ""
        if lines < MAX_LINES:
            longer = min(MAX_LINES, lines * 2)
            more = f'<p class="bar"><a href="{escape(here)}?lines={longer}">More</a></p>'
        pane_box = f'{more}<pre aria-label="Captured pane text">{escape(pane)}</pre>'
        meta = f"{len(pane.splitlines())} lines"
    return (
        _crumbs([(_project_name(root), "/"), (fleet, f"/fleets/{quote(fleet)}"), (name, None)])
        + f'<div class="box">{_table([label for label, _ in facts], [[v for _, v in facts]])}</div>'
        + f'<div class="facts">{_command(_attach(registry, row))}</div>'
        + _section("Pane", pane_box, meta=meta)
    )


def _route(root: Path, target: str) -> tuple[int, str] | tuple[str, int, int]:
    """A page as (status, body), or a raw file as ("raw", descriptor, size)."""

    parts = urlsplit(target)
    path = [unquote(item) for item in parts.path.split("/") if item]
    query = parse_qs(parts.query)
    opened = query.get("open", [""])[0]
    try:
        if not path:
            return 200, _fleets_page(root)
        if path == ["settings"]:
            return 200, _settings_page(root)
        if len(path) < 2 or path[0] != "fleets":
            raise _Missing("No such page.")
        if path[1] not in _fleet_ids(root):
            raise _Missing(f"No fleet named {path[1]} in this project.")
        fleet = path[1]
        if len(path) == 2:
            return 200, _fleet_page(root, fleet, opened)
        if path[2:] == ["raw"]:
            if opened not in {rel for rel, _size in _board_files(root, fleet)}:
                raise _Missing("No such file on this board.")
            handle = _open_listed(root, fleet, opened)
            return "raw", handle, os.fstat(handle).st_size
        if len(path) == 4 and path[2] == "seats":
            return 200, _seat_page(root, fleet, path[3], _lines(query.get("lines", [""])[0]))
        raise _Missing("No such page.")
    except _Missing as exc:
        return 404, f'<p class="empty">{escape(str(exc))} <a href="/">Back to fleets</a></p>'
    except Exception as exc:
        return 500, _failed(exc)


def _document(root: Path, body: str, nonce: str) -> bytes:
    try:
        name = _project_name(root)
    except Exception:
        name = root.name
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<link rel="icon" href="data:,"><title>{escape(name)} · Foil</title>'
        f"<style>{_CSS}</style></head><body><header>"
        f'<a class="name mono" href="/">{escape(name)}</a>'
        '<a class="link" href="/settings">Settings</a></header>'
        f'<main>{body}</main><script nonce="{nonce}">{_SCRIPT}</script></body></html>'
    ).encode()


def _loopback_host(host: str) -> bool:
    match = re.fullmatch(r"(\[::1\]|[A-Za-z0-9.-]+)(?::\d{1,5})?", host)
    return match is not None and match.group(1).lower() in LOOPBACK


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "foil"
    sys_version = ""
    timeout = DEADLINE

    def setup(self) -> None:
        super().setup()
        # The request line and headers must arrive within DEADLINE in total.
        self._deadline = threading.Timer(DEADLINE, self._cut)
        self._deadline.daemon = True
        self._deadline.start()

    def _cut(self) -> None:
        with suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        self._deadline.cancel()
        return parsed

    def finish(self) -> None:
        self._deadline.cancel()
        super().finish()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """No access log."""

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        """Nothing about a request is printed."""

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        self._send(405 if code == 501 else code, b"")

    def _send(self, code: int, payload: bytes, kind: str = "text/html", policy: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._common(policy or "default-src 'none'")
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _common(self, policy: str) -> None:
        self.send_header("Content-Security-Policy", policy)
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()

    def _allowed(self) -> bool:
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        return _loopback_host(host) and (origin is None or origin == f"http://{host}")

    def do_GET(self) -> None:
        if not self._allowed():
            self._send(403, b"")
            return
        if not self.server.slots.acquire(blocking=False):
            self._send(503, b"")
            return
        try:
            with cached_common_dirs():
                result = _route(self.server.root, self.path)
                if result[0] != "raw":
                    nonce = secrets.token_urlsafe(16)
                    page = _document(self.server.root, str(result[1]), nonce)
        except Exception:
            self._send(500, b"")
            return
        finally:
            self.server.slots.release()
        if result[0] == "raw":
            self._raw(int(result[1]), int(result[2]))
            return
        policy = (
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self._send(int(result[0]), page, policy=policy)

    do_HEAD = do_GET

    def _raw(self, handle: int, size: int) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(size))
            self._common("default-src 'none'; sandbox")
            left = 0 if self.command == "HEAD" else size
            while left > 0:
                chunk = os.read(handle, min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        finally:
            os.close(handle)


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, root: Path, port: int) -> None:
        self.root = root
        self.slots = threading.BoundedSemaphore(SLOTS)
        super().__init__(("127.0.0.1", port), _Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A failed connection prints nothing."""


def _answers(port: int) -> bool:
    """Something already listens there. SO_REUSEADDR alone may not say so on macOS."""

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _bind(root: Path, port: int) -> _Server | None:
    if _answers(port):
        return None
    try:
        return _Server(root, port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return None
        raise FoilError(f"foil: could not listen on 127.0.0.1:{port}") from None


def open_server(root: Path, port: str | None, *, base: int = BASE_PORT) -> _Server:
    """Bind the first free port from ``base``, or exactly ``port``."""

    if port is None:
        server = None
        for candidate in range(base, base + PORT_SPAN):
            server = _bind(root, candidate)
            if server is not None:
                break
        if server is None:
            raise FoilError(f"foil: no free port in {base}-{base + PORT_SPAN - 1}")
    else:
        if not (port.isdecimal() and port.isascii() and 1024 <= int(port) <= 65535):
            raise FoilError("foil: invalid port")
        server = _bind(root, int(port))
        if server is None:
            raise FoilError(f"foil: port {int(port)} is in use")
    return server


def serve(root: Path, port: str | None, *, base: int = BASE_PORT) -> int:
    server = open_server(root, port, base=base)

    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        signal.signal(signal.SIGTERM, previous)
    return 0
