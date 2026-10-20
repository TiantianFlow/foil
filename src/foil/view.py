"""`foil view`: a read-only page for every project on this host, on 127.0.0.1.

It writes nothing.

The tailnet reaches it only through `tailscale serve`, which keeps the request's `Host`
and sets `Tailscale-User-Login` after deleting any value the client sent.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from html import escape
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

from foil.errors import FoilError, assert_no_secret
from foil.lifecycle import peek_text, seat_rows
from foil.memory import read_lessons
from foil.presets import (
    entry_label,
    fleet_overrides,
    load_preset,
    preset_ids,
    read_maps,
    select_harness,
    spawn_entry,
)
from foil.project import cached_common_dirs, foil_root, project_record, store_root
from foil.roster_ops import roster_rows
from foil.store import SAFE_ID, load_registry
from foil.tmux import time_budget

BASE_PORT = 7460
PORT_SPAN = 50
PREVIEW = 1024 * 1024
RAW_CAP = 16 * 1024 * 1024
CHUNK = 64 * 1024
MAX_LINES = 1280
DEADLINE = 10
SLOTS = 8
CONNECTIONS = 64
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}
# tailscale serve sets these on every request it forwards; a local browser sends none.
PROXIED = (
    "X-Forwarded-For", "X-Forwarded-Host", "Tailscale-User-Login", "Tailscale-Funnel-Request"
)
# Argv display only: names the mail scan does not refuse but a launch flag often carries.
_SECRET_NAME = re.compile(r"token|(^|_)(key|pat|pass|passwd|pwd)$")
_USERINFO = re.compile(r"(://)[^/@\s]+@")
_KEY = re.compile(r"[A-Za-z0-9-]{1,128}")
PROJECTS = (
    "Every project in this user's Foil store on this machine. A project is one repository. "
    "Open one to see its fleets, settings, and seats. This viewer does not type into a pane "
    "and does not decide progress."
)
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
    "with no model. A fleet's override rows for a role replace that role's rows, only in "
    "that fleet and only on the next spawn. Resume keeps the harness and model recorded on "
    "the seat.",
    "Attach": "The session name stored for this fleet. The viewer does not type into the pane.",
    "Workspace": "The checkout recorded for the project. Every fleet on this repository "
    "shares it. Seat worktrees are separate branches.",
    "Overrides": "This fleet's overrides.toml. A role's rows, in order, replace its default "
    "mapping rows in this fleet, and the next spawn takes the row at that role's seat count. "
    "A row with no harness keeps the harness the default mapping selects. A running seat "
    "keeps its last launch. Resume does not read this file.",
    "Projects": "Each project's recorded checkout. A project whose checkout is gone, or now "
    "belongs to another project folder, has no link until foil init runs in that checkout.",
    "Agents": "Alive means the window exists and the process is running. Dead means the "
    "window is gone or the process has exited, and the seat was not killed. A dead window "
    "can still be peeked. Killed means foil seat kill. A killed seat stays listed and its "
    "name stays reserved until foil seat archive frees it. An archived seat is not listed.",
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
main{max-width:72rem;margin:0 auto;padding:24px;display:grid;gap:32px;
grid-template-columns:minmax(0,1fr)}@media (max-width:640px){main{padding:16px}}
h1{font-size:20px;margin:0}h2{font-size:14px;margin:0 0 8px;display:flex;gap:6px;
align-items:center}h3{font-size:12px;margin:0;padding:6px 12px;color:var(--muted);
border-top:1px solid var(--line)}.muted,nav a{color:var(--muted)}nav a{text-decoration:none}
.box{border:1px solid var(--line);border-radius:6px;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 12px;
vertical-align:top;border-top:1px solid var(--line)}th{border-top:0;font-weight:500;
color:var(--muted);font-size:13px}td small{display:block;color:var(--muted);font-size:12px}
pre{margin:0;padding:12px 16px;white-space:pre;overflow-x:auto;background:var(--soft)}
.facts{display:grid;grid-template-columns:110px minmax(0,1fr);gap:12px;align-items:center}
.facts code,li code{overflow-wrap:anywhere;min-width:0}
.facts>span{font-size:12px;color:var(--muted);display:flex;gap:6px}
.cmd{display:flex;gap:8px;align-items:center;border:1px solid var(--line);
border-radius:6px;background:var(--soft);padding:4px 4px 4px 12px}.cmd code{flex:1}
button{font:inherit;font-size:12px;border:0;background:none;color:var(--muted);cursor:pointer}
.files{display:grid;gap:24px;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))}
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


class _Raw(NamedTuple):
    """A listed board file to stream whole: an open descriptor and its size."""

    handle: int
    size: int


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
    if count < 1024:
        return f"{count} bytes"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KiB"
    return f"{count / (1024 * 1024):.1f} MiB"


def _model(text: str, upcoming: str) -> str:
    """A recorded model, and under it the override the next spawn takes, if any."""

    shown = _mono(text) if text else _faded("no model")
    return shown + (f"<small>next spawn {_mono(upcoming)}</small>" if upcoming else "")


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


def _flagged(value: Any) -> bool:
    try:
        assert_no_secret(value)
    except FoilError:
        return True
    return False


def _secret_name(word: str) -> bool:
    name = word.lstrip("-").replace("-", "_").lower()
    return _flagged({name: ""}) or _SECRET_NAME.search(name) is not None


def _secret_value(word: str) -> bool:
    # The mail scan matches prefixes such as "bearer ", which need the space.
    return _flagged(word) or _flagged(f"{word} x")


def _argv(command: list[str]) -> str:
    """Launch argv as written, with credential-shaped values hidden (best effort)."""

    shown: list[str] = []
    hide_next = False
    for token in command:
        words = [word for word in re.split(r"[\s=:]+", token) if word]
        if hide_next or any(_secret_value(word) for word in words):
            text = "[hidden]"
        elif ("=" in token or ":" in token) and any(_secret_name(word) for word in words):
            name, _sep, value = token.partition("=")
            simple = _secret_name(name) and not re.search(r"[\s:]", token)
            text = f"{name}=[hidden]" if simple else "[hidden]"
        else:
            text = _USERINFO.sub(r"\1[hidden]@", token)
        shown.append(shlex.quote(text) if not text or any(c.isspace() for c in text) else text)
        hide_next = token.startswith("-") and "=" not in token and _secret_name(token)
    return " ".join(shown)


def _fleets_page(root: Path, base: str) -> str:
    def fleets() -> str:
        rows = []
        for fleet in _fleet_ids(root):
            link = f'<a class="mono" href="{base}/fleets/{quote(fleet)}">{escape(fleet)}</a>'
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
        _crumbs([("Projects", "/"), (_project_name(root), None)])
        + f'<div><h1>Fleets</h1><p class="muted">{escape(HELP)}</p></div>'
        + _section("Fleets", _guard(fleets))
        + _section("Lessons", _guard(lessons))
    )


def _settings_page(root: Path, base: str) -> str:
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
        _crumbs([("Projects", "/"), (_project_name(root), base), ("Settings", None)])
        + "<h1>Settings</h1>"
        + _section("Roles", _guard(roles))
        + _section("Harnesses", harness_box, note=note)
        + _section("Default mapping", _guard(mapping))
    )


def _next_entries(root: Path, fleet: str, registry: dict) -> dict[str, str]:
    """Per role, the pair the next spawn takes when an override changes it (F3)."""

    try:
        rows = fleet_overrides(root, fleet)
        maps = read_maps(root)
    except Exception:
        return {}
    found = {}
    for role in dict.fromkeys(item["role"] for item in rows):
        entries = [row for row in maps if row["role"] == role]
        if not entries:
            continue
        stored = sum(seat["template"] == role for seat in registry["seats"].values())
        upcoming = spawn_entry(root, role, entries, stored, fleet)
        if entry_label(upcoming) != entry_label(select_harness(entries, stored)):
            found[role] = entry_label(upcoming)
    return found


def _attach(registry: dict, row: dict | None = None) -> str | None:
    session = str(registry.get("tmux_session") or "")
    if not session:
        return None
    # Always quoted: zsh expands an unquoted "=word" to the path of a command.
    quoted = "'" + ("=" + session).replace("'", "'\\''") + "'"
    command = f"tmux attach -t {quoted}"
    if row is not None and row["owned"]:
        command += f" \\; select-window -t {shlex.quote(row['window_id'])}"
    return command


def _utf8(path: Path) -> bool:
    try:
        str(path).encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


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
            if name.startswith(".") or not stat.S_ISREG(info.st_mode) or not _utf8(path):
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
    raw = f'<a href="{escape(f"{here}/raw?open={quote(rel)}")}">Raw</a>'
    if size > RAW_CAP:
        raw = _faded(f"No raw view over {_size(RAW_CAP)}.")
    return (
        f'<p class="bar"><a href="{escape(here)}">&larr; Back</a>{_mono(rel)}'
        f"{raw}{cut}</p><pre>{escape(text)}</pre>"
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
        if any(rel == opened for rel, _bytes in group):
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
        + _section("Board", _guard(lambda: inner(board, "No files.", False)), meta=meta(board))
        + _section("Posts", _guard(lambda: inner(posts, "No posts.", True)), meta=meta(posts))
        + "</div>"
    )


def _fleet_page(root: Path, fleet: str, opened: str, base: str) -> str:
    here = f"{base}/fleets/{quote(fleet)}"
    try:
        registry = load_registry(root, fleet)
        rows = seat_rows(root, registry)
        attach, failure = _command(_attach(registry)), None
    except Exception as exc:
        registry, rows, failure = None, [], exc
        attach = f"<span>Attach{_tip('Attach')}</span>{_failed(exc)}"

    def overrides() -> str:
        rows = fleet_overrides(root, fleet)
        noted = any(item["note"] for item in rows)
        cells = [
            [_mono(item["role"]),
             _mono(item["id"]) if item["id"] else _faded("default mapping"),
             _mono(item["model"]) if item["model"] else _faded("no model")]
            + ([escape(item["note"])] if noted else [])
            for item in rows
        ]
        return _table(
            ["Role", "Harness", "Model"] + (["Note"] if noted else []),
            cells,
            "No overrides. The next spawn uses the default mapping.",
        )

    def agents() -> str:
        if registry is None:
            return _failed(failure or Exception())
        upcoming = _next_entries(root, fleet, registry)
        cells = []
        for row in rows:
            model = _model(row["model"], upcoming.get(row["template"], ""))
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
        _crumbs([("Projects", "/"), (_project_name(root), base), (fleet, None)])
        + f'<div class="facts">{attach}'
        + f"<span>Workspace{_tip('Workspace')}</span>{workspace}</div>"
        + _section("Overrides", _guard(overrides))
        + _section("Agents", _guard(agents), meta=f"{len(rows)} {noun}")
        + _board_sections(root, fleet, opened, here)
    )


def _lines(value: str) -> int:
    return int(value) if value.isdecimal() and 1 <= int(value) <= MAX_LINES else 40


def _seat_page(root: Path, fleet: str, name: str, lines: int, base: str) -> str:
    registry = load_registry(root, fleet)
    if not SAFE_ID.fullmatch(name) or name not in registry["seats"]:
        raise _Missing(f"No seat named {name} in fleet {fleet}.")
    row = seat_rows(root, registry, only=name, owned=True)[0]
    upcoming = _next_entries(root, fleet, registry).get(row["template"], "")
    launched = f"{row['harness']} / {row['model']}" if row["model"] else row["harness"]
    facts = [
        ("Seat", _mono(name)), ("Role", _mono(row["template"])),
        ("Description", escape(row["description"])), ("State", _state(row["state"])),
        ("Harness / model", _model(launched, upcoming)), ("Branch", _branch(row)),
    ]
    fleet_link = f"{base}/fleets/{quote(fleet)}"
    here = f"{fleet_link}/seats/{quote(name)}"
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
        _crumbs(
            [("Projects", "/"), (_project_name(root), base), (fleet, fleet_link), (name, None)]
        )
        + f'<div class="box">{_table([label for label, _ in facts], [[v for _, v in facts]])}</div>'
        + f'<div class="facts">{_command(_attach(registry, row))}</div>'
        + _section("Pane", pane_box, meta=meta)
    )


class _Project(NamedTuple):
    """One folder in the store, and its checkout when that resolves back to it."""

    key: str
    name: str
    checkout: str
    root: Path | None


def _projects() -> list[_Project]:
    directory = store_root() / "projects"
    if directory.is_symlink() or not directory.is_dir():
        return []
    found = []
    for folder in sorted(directory.iterdir()):
        if not _KEY.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
            continue
        if not (folder / "project.json").is_file():
            continue
        found.append(_project(folder))
    return found


def _project(folder: Path) -> _Project:
    checkout = project_record(folder).get("path", "")
    root = None
    with suppress(Exception):
        if checkout and Path(checkout).is_dir():
            candidate = Path(checkout)
            if foil_root(candidate).resolve() == folder.resolve():
                root = candidate
    return _Project(folder.name, Path(checkout).name or folder.name, checkout, root)


def _projects_page() -> str:
    def projects() -> str:
        rows = []
        for item in _projects():
            if item.root is None:
                name = _mono(item.name)
                fleets = _faded("checkout gone or moved")
            else:
                name = f'<a class="mono" href="/projects/{quote(item.key)}">{escape(item.name)}</a>'
                fleets = escape(str(len(_fleet_ids(item.root))))
            checkout = _mono(item.checkout) if item.checkout else _faded("not recorded")
            rows.append([name, checkout, fleets])
        return _table(["Project", "Checkout", "Fleets"], rows, "No projects. Run foil init.")

    return (
        f'<div><h1>Projects</h1><p class="muted">{escape(PROJECTS)}</p></div>'
        + _section("Projects", _guard(projects))
    )


def _route(target: str) -> tuple[int, str, tuple[str, str] | None] | _Raw:
    """A page as (status, body, project name and link), or a listed file to stream whole."""

    parts = urlsplit(target)
    path = [unquote(item) for item in parts.path.split("/") if item]
    query = parse_qs(parts.query)
    opened = query.get("open", [""])[0]
    home: tuple[str, str] | None = None
    try:
        if not path:
            return 200, _projects_page(), None
        if len(path) < 2 or path[0] != "projects" or not _KEY.fullmatch(path[1]):
            raise _Missing("No such page.")
        folder = store_root() / "projects" / path[1]
        if folder.is_symlink() or not (folder / "project.json").is_file():
            raise _Missing(f"No project named {path[1]} on this machine.")
        project = _project(folder)
        if project.root is None:
            raise _Missing(f"The checkout of {project.name} is gone or moved. Run foil init in it.")
        root, base, rest = project.root, f"/projects/{quote(project.key)}", path[2:]
        home = (project.name, base)
        if not rest:
            return 200, _fleets_page(root, base), home
        if rest == ["settings"]:
            return 200, _settings_page(root, base), home
        if len(rest) < 2 or rest[0] != "fleets":
            raise _Missing("No such page.")
        if rest[1] not in _fleet_ids(root):
            raise _Missing(f"No fleet named {rest[1]} in this project.")
        fleet = rest[1]
        if len(rest) == 2:
            return 200, _fleet_page(root, fleet, opened, base), home
        if rest[2:] == ["raw"]:
            if opened not in {rel for rel, _bytes in _board_files(root, fleet)}:
                raise _Missing("No such file on this board.")
            handle = _open_listed(root, fleet, opened)
            size = os.fstat(handle).st_size
            if size > RAW_CAP:
                os.close(handle)
                cap = f"foil: the raw view stops at {_size(RAW_CAP)}; this file is {_size(size)}"
                return 413, _failed(FoilError(cap)), home
            return _Raw(handle, size)
        if len(rest) == 4 and rest[2] == "seats":
            lines = _lines(query.get("lines", [""])[0])
            return 200, _seat_page(root, fleet, rest[3], lines, base), home
        raise _Missing("No such page.")
    except _Missing as exc:
        back = f'<a href="{escape(home[1])}">Back to fleets</a>' if home else (
            '<a href="/">Back to projects</a>'
        )
        return 404, f'<p class="empty">{escape(str(exc))} {back}</p>', home
    except Exception as exc:
        return 500, _failed(exc), home


def _document(home: tuple[str, str] | None, body: str, nonce: str) -> bytes:
    title = f"{home[0]} · Foil" if home else "Projects · Foil"
    links = (
        f'<a class="name mono" href="{escape(home[1])}">{escape(home[0])}</a>'
        f'<a class="link" href="{escape(home[1])}/settings">Settings</a>'
        if home
        else ""
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<link rel="icon" href="data:,"><title>{escape(title)}</title>'
        f"<style>{_CSS}</style></head><body><header>"
        '<a class="name" href="/">Foil</a>'
        f"{links}</header>"
        f'<main>{body}</main><script nonce="{nonce}">{_SCRIPT}</script></body></html>'
    ).encode()


def _host_name(host: str) -> str:
    match = re.fullmatch(r"(\[::1\]|[A-Za-z0-9.-]+)(?::\d{1,5})?", host)
    return match.group(1).lower() if match else ""


def tailnet() -> tuple[str, str] | None:
    """This machine's tailnet name and owner login, when Tailscale runs here untagged."""

    binary = shutil.which("tailscale")
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, "status", "--json"], capture_output=True, text=True, timeout=5, check=False
        )
        status = json.loads(result.stdout)
        node = status["Self"]
        name = str(node["DNSName"]).rstrip(".").lower()
        login = str(status["User"][str(node["UserID"])]["LoginName"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    running = result.returncode == 0 and status.get("BackendState") == "Running"
    if not running or node.get("Tags") or not _host_name(name) or not login.isascii():
        return None
    return name, login


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    timeout = DEADLINE

    def version_string(self) -> str:
        return "foil"

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
        name = _host_name(host)
        # serve routes on SNI and copies the client's Host, so a forwarded request
        # can claim a loopback name.
        if name in LOOPBACK:
            proxied = any(field in self.headers for field in PROXIED)
            return not proxied and (origin is None or origin == f"http://{host}")
        node = self.server.tailnet
        if node is None or name != node[0]:
            return False
        logins = self.headers.get_all("Tailscale-User-Login") or []
        forwarded = self.headers.get_all("X-Forwarded-Host") or []
        return (
            logins == [node[1]]
            and forwarded == [host]
            and origin in (None, f"https://{host}", f"http://{host}")
        )

    def do_GET(self) -> None:
        if not self._allowed():
            self._send(403, b"")
            return
        if not self.server.slots.acquire(blocking=False):
            self._send(503, b"")
            return
        try:
            with cached_common_dirs(), time_budget(DEADLINE):
                result = _route(self.path)
                if not isinstance(result, _Raw):
                    nonce = secrets.token_urlsafe(16)
                    page = _document(result[2], result[1], nonce)
        except Exception:
            self._send(500, b"")
            return
        finally:
            self.server.slots.release()
        if isinstance(result, _Raw):
            self._raw(result)
            return
        policy = (
            f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
            "img-src data:; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self._send(result[0], page, policy=policy)

    do_HEAD = do_GET

    def _raw(self, raw: _Raw) -> None:
        handle, size = raw
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

    def __init__(self, port: int, node: tuple[str, str] | None = None) -> None:
        self.tailnet = node
        self.slots = threading.BoundedSemaphore(SLOTS)
        self.connections = threading.BoundedSemaphore(CONNECTIONS)
        super().__init__(("127.0.0.1", port), _Handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.connections.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A failed connection prints nothing."""


def _answers(port: int) -> bool:
    """Something already listens there. SO_REUSEADDR alone may not say so on macOS."""

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _bind(port: int, node: tuple[str, str] | None) -> _Server | None:
    if _answers(port):
        return None
    try:
        return _Server(port, node)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return None
        raise FoilError(f"foil: could not listen on 127.0.0.1:{port}") from None


def open_server(port: str | None, *, base: int = BASE_PORT) -> _Server:
    """Bind the first free port from ``base``, or exactly ``port``."""

    if port is not None and not (
        port.isdecimal() and port.isascii() and 1024 <= int(port) <= 65535
    ):
        raise FoilError("foil: invalid port")
    node = tailnet()
    if port is None:
        server = None
        for candidate in range(base, base + PORT_SPAN):
            server = _bind(candidate, node)
            if server is not None:
                break
        if server is None:
            raise FoilError(f"foil: no free port in {base}-{base + PORT_SPAN - 1}")
    else:
        server = _bind(int(port), node)
        if server is None:
            raise FoilError(f"foil: port {int(port)} is in use")
    return server


def serve(port: str | None, *, base: int = BASE_PORT) -> int:
    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    server = None
    try:
        server = open_server(port, base=base)
        print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # A second Ctrl-C or SIGTERM during close does not print a traceback.
        interrupt = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if server is not None:
            server.server_close()
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, previous)
    return 0
