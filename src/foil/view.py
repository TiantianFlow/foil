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
import time
from collections.abc import Callable
from contextlib import suppress
from html import escape
from http.server import BaseHTTPRequestHandler
from importlib.resources import files
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

from foil.errors import FoilError, assert_no_secret
from foil.lifecycle import peek_text, seat_rows
from foil.memory import read_lessons
from foil.presets import (
    _catalog,
    _packaged_catalog,
    _parse_maps,
    _parse_preset,
    _parse_roles,
    description_of,
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
    "Open one to see its fleets, settings, and seats."
)
HELP = (
    "One project shares roles, harnesses, and the default mapping. Each fleet has its own "
    "seats, board, posts, and overrides. Progress is status.md."
)
SETTINGS = (
    "Shared by every fleet in this project. A fleet's overrides.toml can replace a role's "
    "mapping list in that fleet only."
)
GLOBAL_SETTINGS = (
    "The global layer, shared by every project on this machine. A project file replaces "
    "what it names. A fleet's overrides.toml replaces one role's list in that fleet only."
)
_FLEET_NONE = (
    "None. Spawn uses each role's project pair list when that role has one, "
    "otherwise the global list."
)
FOOTER = "This viewer does not type into a pane and does not decide progress."
TIPS = {
    "Lessons": "Project lessons. They outlive fleets and are copied into a seat at the next "
    "spawn or resume. States are proposed, accepted, rejected, and superseded. This is not "
    "the fleet override list.",
    "Roles": "A role is a description, a persona, a worktree flag, and a permission. The "
    "description is the first line of the persona.",
    "Permission": "auto asks the harness to skip approval prompts. ask stops the seat at its "
    "first approval prompt.",
    "Worktree": "yes means each seat of this role gets its own git worktree on a new branch, "
    "so its edits do not touch your checkout or other seats. Killing the seat keeps the "
    "branch.",
    "Harnesses": "How one agent CLI is launched, and the model ids its list names. Spawn does "
    "not refuse a model that is absent from the list. A file harnesses/<id>.toml replaces "
    "that one id.",
    "Installed": "Whether this viewer found the command on the PATH it was started with, and "
    "where. A seat looks the command up on the PATH of the foil command that launched it, so "
    "a viewer started elsewhere, such as a service, can differ.",
    "Launch command": "The argv foil runs to start a seat, with placeholders as written. A "
    "value that looks like a credential shows as [hidden].",
    "Default mapping": "The shared default in mapping.toml: for each role, one ordered list of "
    "harness and model pairs, shown as pair cards. The rotation is on the pair, never on the "
    "harness or the model alone. An empty model shows harness default: no --model is passed, "
    "so the CLI uses its own default. Resume keeps the pair recorded on the seat.",
    "Entries, in order": "Each entry is one card, harness on top and model below. The next "
    "spawn of a role takes entry ((seats stored for that role, including killed) mod "
    "(number of entries)) + 1.",
    "Attach": "The session name stored for this fleet. The viewer does not type into the pane.",
    "Workspace": "The checkout recorded for the project. Every fleet on this repository "
    "shares it. Seat worktrees are separate branches.",
    "Overrides": "This fleet's overrides.toml. A role's entries, in order, replace the pair "
    "list below this fleet. The Rotation line marks the next seat from this fleet's stored "
    "count. An empty model is harness default. A model-only row on a multi-entry role is "
    "refused at spawn (F3). A running seat keeps its last launch. Resume does not read "
    "this file.",
    "Projects": "Each project's recorded checkout. A project whose checkout is gone, or now "
    "belongs to another project folder, has no link until foil init runs in that checkout.",
    "Last activity": "When a file on this fleet's board last changed.",
    "Agents": "Every seat stored for this fleet, grouped by state. A dead window can still be "
    "peeked. A killed seat stays listed and its name stays reserved until foil seat archive "
    "frees it. An archived seat is not listed.",
    "State": "alive: the window exists and the process is running. dead: the window is gone "
    "or the process has exited, and the seat was not killed. killed: stopped with foil seat "
    "kill.",
    "Harness / model": "The pair this seat was launched with: the agent CLI and the model "
    "passed to it. Resume launches the same pair. A second line shows the pair the next "
    "spawn of that role takes when this fleet's overrides change it.",
    "Branch": "The git branch of this seat's worktree. A role without a worktree has no "
    "branch. Killing the seat keeps the branch.",
    "status.md": "The lead's report for this fleet, including the checklist, shown as written.",
    "Board": "This fleet's shared folder. Mail is to one seat. Notes are scratch and wake "
    "nobody. tasks/ is the brief. results/ is the report. The viewer shows the files as "
    "written.",
    "Posts": "Threads under this fleet's board/room/. A post wakes every other alive seat in "
    "this fleet. A reply stays in that thread. A decline is a file and types nothing. A "
    "hand-written file there wakes nobody. Status still comes from status.md.",
    "Pane": "Raw tmux capture. The viewer does not decide that the seat is busy, blocked, or "
    "waiting on a dialog.",
}
FILTERS = {
    "": ("Not killed", ("dead", "alive")),
    "alive": ("Alive", ("alive",)),
    "dead": ("Dead", ("dead",)),
    "killed": ("Killed", ("killed",)),
    "all": ("All", ("dead", "alive", "killed")),
}
STATUS_LINES = 15
_CSS = """
:root{--bg:#fff;--fg:#1f1f1f;--muted:#737373;--soft:#f5f5f5;--line:#e5e5e5;--ok:#10b981;
--bad:#dc2626;--warn:#d97706;--warn-soft:#fffbeb;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{--bg:#171717;--fg:#fafafa;--muted:#a3a3a3;
--soft:#262626;--line:#333;--bad:#f87171;--warn:#fbbf24;--warn-soft:#2a2210}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,system-ui,sans-serif}a{color:inherit}
:focus-visible{outline:2px solid var(--fg);outline-offset:2px}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
header{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);
display:flex;align-items:center;max-width:72rem;margin:0 auto;padding:0 24px;height:44px}
header nav{flex:1;min-width:0}header>a.tab{margin-left:auto;text-decoration:none;
color:var(--muted);padding:2px 8px;border-radius:6px;white-space:nowrap}
header>a.tab[aria-current]{background:var(--soft);color:var(--fg);font-weight:600}
header ol{margin:0;padding:0;height:44px;display:flex;gap:8px;
align-items:center;list-style:none;overflow-x:auto;white-space:nowrap}
header li+li:before{content:"/";color:var(--muted);margin-right:8px}
header a{text-decoration:none;color:var(--muted)}header [aria-current]{color:var(--fg);
font-weight:600}header a.tab{padding:2px 8px;border-radius:6px}
header li.tab:before{content:none}header a.tab[aria-current]{background:var(--soft)}
main{max-width:72rem;margin:0 auto;padding:24px;display:grid;gap:32px;
grid-template-columns:minmax(0,1fr)}footer{max-width:72rem;margin:0 auto;padding:0 24px 24px;
font-size:12px;color:var(--muted)}@media (max-width:640px){main{padding:16px}
.facts{grid-template-columns:100px minmax(0,1fr)}
header ol{padding:0 16px}}
h1{font-size:20px;margin:0}h1.mono{font-size:17px}
h2{font-size:14px;margin:0 0 8px;display:flex;gap:6px;
align-items:center;flex-wrap:wrap}h3{font-size:12px;margin:0;padding:6px 12px;
color:var(--muted);background:var(--soft);border-top:1px solid var(--line)}
.box>h3:first-child{border-top:0}.muted{color:var(--muted)}h2 .muted{font-weight:400}
.box{border:1px solid var(--line);border-radius:6px;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px 12px;
vertical-align:top;border-top:1px solid var(--line)}th{border-top:0;font-weight:500;
color:var(--muted);font-size:13px;white-space:nowrap}td small{display:block;
color:var(--muted);font-size:12px}td .mono,.nowrap{white-space:nowrap}
pre{margin:0;padding:12px 16px;white-space:pre;overflow-x:auto;background:var(--soft)}
pre.wrap{white-space:pre-wrap;overflow-wrap:anywhere}
.facts{display:grid;grid-template-columns:130px minmax(0,1fr);gap:12px;align-items:center;
margin:0}.facts dd{margin:0}.facts code,li code{overflow-wrap:anywhere;min-width:0}
.facts>span,.facts dt{font-size:12px;color:var(--muted);display:flex;gap:6px}
.cmd{display:flex;gap:8px;align-items:center;border:1px solid var(--line);
border-radius:6px;background:var(--soft);padding:4px 4px 4px 12px}.cmd code{flex:1}
button{font:inherit;font-size:12px;border:0;border-radius:4px;padding:4px 8px;
background:none;color:var(--muted);cursor:pointer}button:hover{color:var(--fg);
background:var(--line)}
.files{display:grid;gap:24px;grid-template-columns:repeat(auto-fit,minmax(min(320px,100%),1fr))}
ul{list-style:none;margin:0;padding:0}.box li a{display:flex;gap:8px;padding:8px 12px;
text-decoration:none;border-top:1px solid var(--line)}.box li:first-child a{border-top:0}
.box li a:hover{background:var(--soft)}.box li a span{margin-left:auto;color:var(--muted);
font-size:12px;white-space:nowrap}.box li a code{min-width:0;overflow-wrap:anywhere}
.bar{margin:0;padding:6px 12px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 8px}.chip{border:1px solid var(--line);
border-radius:6px;padding:2px 10px;font-size:12px;text-decoration:none;color:var(--muted)}
.chip[aria-current]{background:var(--fg);color:var(--bg);border-color:var(--fg)}
.pairs{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0;padding:0;
list-style:none}.pairs li{border:1px solid var(--line);border-radius:6px;padding:1px 8px;
background:var(--soft);white-space:nowrap}.pairs li.next{background:var(--bg);
border-color:var(--fg)}.pairs li+li{position:relative;margin-left:16px}
.pairs li+li:before{content:"\\2192";color:var(--muted);position:absolute;left:-16px}
.pairs b{font-size:11px;font-weight:600;margin-left:6px}
.cards{display:flex;gap:8px;flex-wrap:wrap;align-items:stretch}
.card{display:inline-flex;flex-direction:column;border:1px solid var(--line);
border-radius:6px;padding:4px 8px;background:var(--soft);min-width:5rem}
.card b{font-weight:600}.card small{color:var(--muted)}
.layers{display:flex;gap:8px;align-items:center;margin:0;color:var(--muted);font-size:12px}
.layers span+span:before{content:"\\2192";margin-right:8px}
.pairs i{font-style:normal;color:var(--muted);font-size:11px;margin-right:4px}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;
background:var(--muted)}.alive{background:var(--ok)}.dead{background:var(--warn)}
.killed{background:var(--bad)}.agents td:first-child{width:46%}.agents td:nth-child(2){width:11%}
.agents td:nth-child(3){width:23%}tr.group th{background:var(--soft);border-top:1px solid
var(--line);font-size:12px}tr.attention th{background:var(--warn-soft);color:var(--fg)}
tr.attention td{background:var(--warn-soft)}details.killed-seats{border-top:1px solid
var(--line);background:none}details.killed-seats>summary{cursor:pointer;padding:6px 12px;
font-size:12px;color:var(--muted);background:var(--soft)}
.err{color:var(--bad)}.empty{margin:0;padding:20px;text-align:center;color:var(--muted)}
.tip{position:relative;display:inline-block;vertical-align:middle;margin-left:4px}
.tip summary{list-style:none;
cursor:pointer;color:var(--muted);font-size:11px;font-weight:400;border:1px solid
var(--muted);border-radius:50%;width:16px;height:16px;line-height:14px;text-align:center;
position:relative}.tip summary:after{content:"";position:absolute;inset:-5px}
.tip summary::-webkit-details-marker{display:none}
.tip p{position:fixed;z-index:3;width:min(18rem,calc(100vw - 16px));margin:0;padding:12px;
font-size:12px;font-weight:400;line-height:1.5;color:var(--fg);background:var(--bg);
white-space:normal;text-align:left;border:1px solid var(--line);border-radius:6px;
box-shadow:0 4px 12px #0002}
@media (max-width:640px){table.agents thead{display:none}table.agents tr{display:grid;
grid-template-columns:minmax(0,1fr) auto;border-top:1px solid var(--line)}
table.agents td,table.agents th{border:0;padding:4px 12px;width:auto}
table.agents tr.group,table.agents tr.group th{display:block}
table.agents td:nth-child(even){text-align:right}}
"""
_SCRIPT = """document.querySelectorAll('button[data-copy]').forEach(b=>b.addEventListener(
'click',async()=>{try{await navigator.clipboard.writeText(b.dataset.copy);
b.textContent='Copied'}catch(e){b.textContent='Copy failed'}
setTimeout(()=>{b.textContent='Copy'},1600)}))
const tips=()=>document.querySelectorAll('details.tip[open]')
document.querySelectorAll('details.tip').forEach(d=>d.addEventListener('toggle',()=>{
if(!d.open)return;const r=d.firstChild.getBoundingClientRect(),p=d.lastChild
p.style.top=r.bottom+6+'px';p.style.left=Math.max(8,Math.min(r.left,innerWidth-p.offsetWidth-8))+'px'}))
document.addEventListener('click',e=>tips().forEach(d=>{if(!d.contains(e.target))d.open=false}))
document.addEventListener('keydown',e=>{if(e.key==='Escape')tips().forEach(d=>{d.open=false})})
addEventListener('scroll',()=>tips().forEach(d=>{d.open=false}))"""


class _Missing(Exception):
    """A fleet, seat, or file this project does not have."""


class _Raw(NamedTuple):
    """A listed board file to stream whole: an open descriptor and its size."""

    handle: int
    size: int


class _Page(NamedTuple):
    """A page body, the breadcrumbs after the project, and whether Settings is open."""

    body: str
    trail: list[tuple[str, str | None]]
    settings: bool = False


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


def _label(title: str) -> str:
    return escape(title) + (_tip(title) if title in TIPS else "")


def _section(
    title: str, inner: str, *, meta: str = "", note: str = "", anchor: str = "", tip: str = ""
) -> str:
    extra = f' <span class="muted">{meta}</span>' if meta else ""
    footer = f'<p class="muted">{escape(note)}</p>' if note else ""
    ident = f' id="{anchor}"' if anchor else ""
    key = tip or title
    label = escape(title) + (_tip(key) if key in TIPS else "")
    heading = f"<h2>{label}{extra}</h2>"
    return f'<section{ident}>{heading}<div class="box">{inner}</div>{footer}</section>'


def _head(headers: list[str]) -> str:
    return "<thead><tr>" + "".join(f"<th>{_label(item)}</th>" for item in headers) + "</tr></thead>"


def _rows(rows: list[list[str]]) -> str:
    return "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)


def _table(headers: list[str], rows: list[list[str]], empty: str = "None.") -> str:
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>'
    return f"<table>{_head(headers)}<tbody>{_rows(rows)}</tbody></table>"


def _mono(text: str) -> str:
    return f'<span class="mono">{escape(text)}</span>'


def _faded(text: str) -> str:
    return f'<span class="muted">{escape(text)}</span>'


def _branch(row: dict) -> str:
    return _mono(row["branch"]) if row["branch"] else _faded("no branch")


def _state(state: str) -> str:
    return f'<span class="nowrap"><span class="dot {escape(state)}"></span>{escape(state)}</span>'


def _ago(mtime: float) -> str:
    """A relative time, with the local timestamp on hover."""

    seconds = max(0, int(time.time() - mtime))
    shown = "just now"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            shown = f"{seconds // size} {unit} ago"
            break
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
    return f'<span class="nowrap" title="{stamp}">{shown}</span>'


def _command(command: str | None) -> str:
    if command is None:
        value = _faded("No session yet. This fleet has not started.")
    else:
        value = (
            f'<div class="cmd"><code>{escape(command)}</code>'
            f'<button type="button" data-copy="{escape(command)}">Copy</button></div>'
        )
    return f"<span>Attach{_tip('Attach')}</span>{value}"


def _size(count: int) -> str:
    if count < 1024:
        return f"{count} bytes"
    if count < 1024 * 1024:
        return f"{count / 1024:.1f} KiB"
    return f"{count / (1024 * 1024):.1f} MiB"


def _pair(text: str) -> str:
    """A recorded harness/model label as `harness / model`."""

    harness, _sep, model = text.partition("/")
    shown = model if model else "harness default"
    return _mono(f"{harness} / {shown}")


def _model(row: dict, upcoming: str) -> str:
    """The recorded pair, and under it the pair the next spawn takes, if an override changes it."""

    shown = _pair(f"{row['harness']}/{row['model']}" if row["model"] else row["harness"])
    return shown + (f"<small>next spawn {_pair(upcoming)}</small>" if upcoming else "")


def _badge(layer: str) -> str:
    return f'<span class="badge">{escape(layer)}</span>'


def _card(entry: dict[str, str]) -> str:
    """One pair: harness on top, model below. An empty model is harness default."""

    harness = entry["id"] or "default harness"
    model = entry["model"] or "harness default"
    return (
        f'<span class="card"><b>{escape(harness)}</b><small>{escape(model)}</small></span>'
    )


def _rotation(entries: list[dict[str, str]], upcoming: int | None = None) -> str:
    """Pair cards in order. A fleet marks the next seat; settings do not."""

    cards = []
    for index, entry in enumerate(entries):
        card = _card(entry)
        if index == upcoming:
            card = card.replace('class="card"', 'class="card next"', 1)
            card = card.replace("</span>", "<b>next</b></span>", 1)
        cards.append(card)
    return 'Rotation <span class="cards">' + "".join(cards) + "</span>"


def _text_file(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _id_set(path: Path) -> set[str]:
    text = _text_file(path)
    if text is None:
        return set()
    try:
        return {key for key in _catalog(text) if key != "fake"}
    except FoilError:
        return set()


def _harness_layer(
    root: Path, harness_id: str, project_list: set[str], store_list: set[str]
) -> str:
    folder = foil_root(root) / "harnesses" / f"{harness_id}.toml"
    if folder.is_file() and not folder.is_symlink():
        return "project"
    if harness_id in project_list:
        return "project"
    store = store_root() / "harnesses" / f"{harness_id}.toml"
    if store.is_file() and not store.is_symlink():
        return "global"
    if harness_id in store_list:
        return "global"
    return "packaged"


def _preset_cells(preset: dict) -> tuple[str, str, str]:
    env = preset["env"]
    names = f"<small>env: {escape(', '.join(env))}</small>" if env else ""
    found = shutil.which(preset["command"][0])
    installed = f"yes<small>{escape(found)}</small>" if found else _faded("no")
    models = escape(", ".join(preset["models"])) if preset["models"] else _faded("none")
    launch = f"<code>{escape(_argv(preset['command']))}</code>{names}"
    return installed, f"<code>{models}</code>", launch


def _global_harnesses() -> tuple[list[tuple[str, dict, str]], list[tuple[str, str]]]:
    """Eligible harnesses for the global page, as (id, preset, badge)."""

    chosen = {key: value for key, value in _packaged_catalog().items() if key != "fake"}
    layer = {key: "packaged" for key in chosen}
    skipped: list[tuple[str, str]] = []
    root = store_root()
    listed: dict = {}
    text = _text_file(root / "harnesses.toml")
    if text is not None:
        try:
            listed = _catalog(text)
        except FoilError as exc:
            skipped.append(("harnesses.toml", str(exc).removeprefix("foil: ")))
    for key, value in listed.items():
        if key == "fake":
            continue
        chosen[key] = value
        layer[key] = "global"
    directory = root / "harnesses"
    if directory.is_dir() and not directory.is_symlink():
        for path in sorted(directory.glob("*.toml")):
            harness_id = path.stem
            if (
                path.is_symlink()
                or not path.is_file()
                or harness_id == "fake"
                or SAFE_ID.fullmatch(harness_id) is None
            ):
                continue
            try:
                chosen[harness_id] = _parse_preset(path.read_text(encoding="utf-8"), harness_id)
            except FoilError as exc:
                skipped.append((f"harnesses/{harness_id}.toml", str(exc).removeprefix("foil: ")))
                continue
            layer[harness_id] = "global"
    return [(key, chosen[key], layer[key]) for key in sorted(chosen)], skipped


def _by_role(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["role"], []).append(row)
    return grouped


def _checkout(root: Path) -> str:
    return project_record(foil_root(root)).get("path") or str(root)


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


def _fleets_page(root: Path, base: str) -> _Page:
    def fleets() -> str:
        rows = []
        for fleet in _fleet_ids(root):
            link = f'<a class="mono" href="{base}/fleets/{quote(fleet)}">{escape(fleet)}</a>'
            try:
                states = [row["state"] for row in seat_rows(root, load_registry(root, fleet))]
                files = _board_files(root, fleet)
            except Exception as exc:
                rows.append([link, f'<span class="err">{escape(_error(exc))}</span>', "", "", ""])
                continue
            counts = [str(states.count(state)) for state in ("alive", "dead", "killed")]
            latest = _ago(max(item[2] for item in files)) if files else _faded("no files")
            rows.append([link, *counts, latest])
        return _table(["Fleet", "Alive", "Dead", "Killed", "Last activity"], rows, "No fleets.")

    def lessons() -> str:
        rows = [
            [_mono(str(item.get("id", ""))), escape(str(item.get("state", ""))),
             escape(" ".join(str(item.get("text", "")).split()))]
            for item in read_lessons(root, all_lessons=True)
        ]
        return _table(["Id", "State", "Text"], rows, "No lessons.")

    body = (
        f'<div><h1>Fleets</h1><p class="muted">{escape(HELP)}</p></div>'
        + _section("Fleets", _guard(fleets))
        + _section("Lessons", _guard(lessons))
    )
    return _Page(body, [])


def _packaged_personas(names: set[str]) -> list[dict[str, str]]:
    """Packaged personas with no role, used when no persona directory was written."""

    packaged = files("foil").joinpath("defaults", "personas")
    found = []
    for path in packaged.iterdir():
        if path.name.endswith(".md") and path.name[:-3] not in names:
            found.append(
                {"name": path.name[:-3],
                 "description": description_of(path.read_text(encoding="utf-8"))}
            )
    return sorted(found, key=lambda item: item["name"])


def _personas(root: Path, loaded: list, written: list[dict[str, str]]) -> list[dict[str, str]]:
    """Personas with no role: a written layer when one exists, else the package."""

    if written or (store_root() / "personas").is_dir():
        return written
    if (foil_root(root) / "personas").is_dir():
        return written
    return _packaged_personas({name for name, _row in loaded})


def _persona_badge(root: Path | None, name: str) -> str:
    """The layer that owns this persona file. A missing global file is packaged."""

    if root is not None:
        project = foil_root(root) / "personas" / f"{name}.md"
        if project.is_file() and not project.is_symlink():
            return "project"
    stored = store_root() / "personas" / f"{name}.md"
    if stored.is_file() and not stored.is_symlink():
        return "global"
    return "packaged"


def _persona_rows(root: Path | None, personas: list[dict[str, str]]) -> list[list[str]]:
    return [
        [_mono(f"+ {item['name']}") + _badge(_persona_badge(root, item["name"])),
         escape(item["description"]), _faded("no role"), ""]
        for item in personas
    ]


def _layers() -> str:
    names = "".join(f"<span>{name}</span>" for name in ("Packaged", "Global", "Project", "Fleet"))
    return f'<p class="layers">{names}</p>'


def _role_badge(root: Path | None, role: str) -> str:
    """The layer that won this role: a project file, else the global file."""

    if root is not None:
        text = _text_file(foil_root(root) / "roles.toml")
        if text is not None:
            try:
                if any(item["id"] == role for item in _parse_roles(text)):
                    return "project"
            except FoilError:
                return "project"
    return "global" if _text_file(store_root() / "roles.toml") is not None else "packaged"


def _map_badge(root: Path | None, role: str) -> str:
    if root is not None:
        text = _text_file(foil_root(root) / "mapping.toml")
        if text is not None:
            try:
                if any(item["role"] == role for item in _parse_maps(text)):
                    return "project"
            except FoilError:
                return "project"
    return "global"


def _settings_page(root: Path, base: str) -> _Page:
    def roles() -> str:
        loaded, written = roster_rows(root)
        personas = _personas(root, loaded, written)
        rows = [
            [_mono(name) + _badge(_role_badge(root, name)), escape(row["description"]),
             escape(row["permission"]), "yes" if row["worktree"] else _faded("no")]
            if row is not None
            else [_mono(name) + _badge("invalid"), '<span class="err">invalid template</span>',
                  "", ""]
            for name, row in loaded
        ]
        rows += _persona_rows(root, personas)
        return _table(["Role", "Description", "Permission", "Worktree"], rows)

    skipped: list[tuple[str, str]] = []

    def harnesses() -> str:
        ids, found = preset_ids(root)
        skipped.extend(found)
        project_list = _id_set(foil_root(root) / "harnesses.toml")
        store_list = _id_set(store_root() / "harnesses.toml")
        rows = []
        for harness_id in ids:
            name = _mono(harness_id) + _badge(
                _harness_layer(root, harness_id, project_list, store_list)
            )
            try:
                preset = load_preset(root, harness_id)
            except Exception as exc:
                rows.append([name, _faded("invalid"), "", escape(_error(exc))])
                continue
            installed, models, launch = _preset_cells(preset)
            rows.append([name, installed, models, launch])
        return _table(["Id", "Installed", "Model ids", "Launch command"], rows)

    def mapping() -> str:
        rows = [
            [_mono(role) + _badge(_map_badge(root, role)), _rotation(entries)]
            for role, entries in _by_role(read_maps(root)).items()
        ]
        return _table(["Role", "Entries, in order"], rows, "No rows.")

    harness_box = _guard(harnesses)
    note = "; ".join(f"{path} skipped: {reason}" for path, reason in skipped)
    body = (
        f'<div><h1>Project settings</h1>{_layers()}'
        f'<p class="muted">{escape(SETTINGS)}</p></div>'
        + _section("Roles", _guard(roles))
        + _section("Harnesses", harness_box, note=note)
        + _section("Default mapping", _guard(mapping))
    )
    return _Page(body, [], settings=True)


def _global_settings_page() -> _Page:
    """``/settings`` is the global layer and needs no project."""

    skipped: list[tuple[str, str]] = []

    def roles() -> str:
        text = _text_file(store_root() / "roles.toml")
        found = _parse_roles(text) if text is not None else []
        rows = [
            [_mono(item["id"]) + _badge("global" if text is not None else "packaged"),
             escape(str(item.get("description") or "")),
             escape(str(item.get("permission") or "auto")),
             "yes" if item.get("worktree") else _faded("no")]
            for item in found
        ]
        named = {str(item["id"]) for item in found}
        personas = []
        seen: set[str] = set()
        packaged = Path(str(files("foil").joinpath("defaults", "personas")))
        for directory in (store_root() / "personas", packaged):
            if directory.is_symlink() or not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                name = path.name[:-3]
                if (
                    name in named
                    or name in seen
                    or path.is_symlink()
                    or not path.is_file()
                ):
                    continue
                seen.add(name)
                personas.append(
                    {"name": name, "description": description_of(path.read_text(encoding="utf-8"))}
                )
        personas.sort(key=lambda item: item["name"])
        rows += _persona_rows(None, personas)
        return _table(["Role", "Description", "Permission", "Worktree"], rows, "No rows.")

    def harnesses() -> str:
        found, notes = _global_harnesses()
        skipped.extend(notes)
        rows = []
        for harness_id, preset, layer in found:
            installed, models, launch = _preset_cells(preset)
            rows.append([_mono(harness_id) + _badge(layer), installed, models, launch])
        return _table(["Id", "Installed", "Model ids", "Launch command"], rows)

    def mapping() -> str:
        text = _text_file(store_root() / "mapping.toml")
        maps = _parse_maps(text) if text is not None else []
        rows = [
            [_mono(role) + _badge("global"), _rotation(entries)]
            for role, entries in _by_role(maps).items()
        ]
        return _table(["Role", "Entries, in order"], rows, "No rows.")

    harness_box = _guard(harnesses)
    note = "; ".join(f"{path} skipped: {reason}" for path, reason in skipped)
    body = (
        f'<div><h1>Settings</h1>{_layers()}'
        f'<p class="muted">{escape(GLOBAL_SETTINGS)}</p></div>'
        + _section("Roles", _guard(roles))
        + _section("Harnesses", harness_box, note=note)
        + _section("Default mapping", _guard(mapping))
    )
    return _Page(body, [], settings=True)


def _project_mapped(root: Path) -> set[str]:
    """Roles the project mapping names. A project file replaces those roles whole."""

    text = _text_file(foil_root(root) / "mapping.toml")
    if text is None:
        return set()
    try:
        return {row["role"] for row in _parse_maps(text)}
    except FoilError:
        return set()


def _stored(registry: dict, role: str) -> int:
    return sum(seat["template"] == role for seat in registry["seats"].values())


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
        stored = _stored(registry, role)
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


def _board_files(root: Path, fleet: str) -> list[tuple[str, int, float]]:
    """Each listed board file as (path, size, modified time), status.md first."""

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
            found.append((path.relative_to(board).as_posix(), info.st_size, info.st_mtime))
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


def _read_listed(root: Path, fleet: str, rel: str) -> tuple[str, int]:
    """The first 1 MiB of a listed file as text, and the file's size."""

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
    return data.decode("utf-8", errors="replace"), size


def _folder(rel: str) -> str:
    """The group a board file is listed under: its thread, mail box, or top folder."""

    parts = rel.split("/")
    if parts[0] in ("room", "mail") and len(parts) > 2:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else "board"


def _file_page(root: Path, fleet: str, rel: str, here: str) -> _Page:
    files = _board_files(root, fleet)
    listed = [item for item in files if item[0] == rel]
    if not listed:
        raise _Missing("No such file on this board.")
    siblings = [item[0] for item in files if _folder(item[0]) == _folder(rel)]
    anchor = "status" if rel == "status.md" else "posts" if rel.startswith("room/") else "board"
    back = f"{here}#{anchor}"
    links = [f'<a href="{escape(back)}">&larr; Back to fleet</a>']
    at = siblings.index(rel)
    for step, name in ((-1, "Previous"), (1, "Next")):
        if 0 <= at + step < len(siblings):
            target = f"{here}?open={quote(siblings[at + step])}"
            links.append(f'<a href="{escape(target)}" title="{escape(siblings[at + step])}">'
                         f"{name}</a>")
    try:
        text, size = _read_listed(root, fleet, rel)
    except Exception as exc:
        inner = f'<p class="bar">{" ".join(links)}</p>{_failed(exc)}'
    else:
        cut = _faded(f"Showing the first 1 MiB of {_size(size)}.") if size > PREVIEW else ""
        raw = f'<a href="{escape(f"{here}/raw?open={quote(rel)}")}">Raw</a>'
        if size > RAW_CAP:
            raw = _faded(f"No raw view over {_size(RAW_CAP)}.")
        meta = f"modified {_ago(listed[0][2])} · {_size(size)}"
        links.append(raw)
        inner = (
            f'<p class="bar">{" ".join(links)}<span class="muted">{meta}</span>{cut}</p>'
            f'<pre class="wrap">{escape(text)}</pre>'
        )
    body = f'<div><h1 class="mono">{escape(rel)}</h1></div><div class="box">{inner}</div>'
    return _Page(body, [(fleet, here), *[(part, None) for part in rel.split("/")]])


def _file_rows(files: list[tuple[str, int, float]], here: str, folder: str) -> str:
    """Rows under one group heading, each named by its path below that group."""

    cut = 0 if folder == "board" else len(folder) + 1
    items = [
        f'<li><a href="{escape(here + "?open=" + quote(rel))}" title="{escape(rel)}">'
        f"<code>{escape(rel[cut:])}</code><span>{_ago(mtime)} · {_size(size)}</span></a></li>"
        for rel, size, mtime in files
    ]
    return f"<ul>{''.join(items)}</ul>"


def _grouped(files: list[tuple[str, int, float]], here: str, newest: bool) -> str:
    groups: dict[str, list[tuple[str, int, float]]] = {}
    for item in files:
        groups.setdefault(_folder(item[0]), []).append(item)
    order = sorted(groups, key=lambda key: -max(f[2] for f in groups[key])) if newest else groups
    return "".join(f"<h3>{escape(key)}/</h3>{_file_rows(groups[key], here, key)}" for key in order)


def _status(root: Path, fleet: str, files: list[tuple[str, int, float]], here: str) -> str:
    found = [item for item in files if item[0] == "status.md"]
    if not found:
        return _section("status.md", '<p class="empty">No status.md yet.</p>', anchor="status")
    try:
        text, _size_bytes = _read_listed(root, fleet, "status.md")
    except Exception as exc:
        return _section("status.md", _failed(exc), anchor="status")
    lines = text.splitlines()
    link = f'<a href="{escape(here)}?open=status.md">Open</a>'
    more = ""
    if len(lines) > STATUS_LINES:
        more = f'<p class="bar muted">Showing {STATUS_LINES} of {len(lines)} lines. {link}</p>'
    shown = escape("\n".join(lines[:STATUS_LINES]))
    meta = f"modified {_ago(found[0][2])} · {link}"
    return _section(
        "status.md", f'<pre class="wrap">{shown}</pre>{more}', meta=meta, anchor="status"
    )


def _board_sections(root: Path, fleet: str, here: str) -> str:
    try:
        files = _board_files(root, fleet)
    except Exception as exc:
        return _section("Board", _failed(exc)) + _section("Posts", _failed(exc))
    board = [item for item in files if not item[0].startswith("room/")]
    posts = [item for item in files if item[0].startswith("room/")]

    def meta(group: list) -> str:
        return f"{len(group)} {'file' if len(group) == 1 else 'files'}"

    def listing(group: list, empty: str, newest: bool) -> str:
        return _grouped(group, here, newest) if group else f'<p class="empty">{empty}</p>'

    return (
        _guard(lambda: _status(root, fleet, files, here))
        + _section("Posts", _guard(lambda: listing(posts, "No posts.", True)),
                   meta=meta(posts), anchor="posts")
        + _section("Board", _guard(lambda: listing(board, "No files.", False)),
                   meta=meta(board), anchor="board")
    )


def _ordered(rows: list[dict]) -> list[dict]:
    """Dead first, then alive, then killed; in each, the lead, then by role and name."""

    def key(row: dict) -> tuple:
        number = re.search(r"(\d+)$", row["name"])
        return (("dead", "alive", "killed").index(row["state"]), row["template"] != "lead",
                row["template"], int(number.group(1)) if number else 0, row["name"])

    return sorted(rows, key=key)


def _agents(rows: list[dict], upcoming: dict[str, str], here: str, state: str) -> str:
    shown = FILTERS[state][1]
    chips = ""
    for value, (label, states) in FILTERS.items():
        target = f"{here}?state={value}#agents" if value else f"{here}#agents"
        current = ' aria-current="true"' if value == state else ""
        count = sum(row["state"] in states for row in rows)
        chips += f'<a class="chip" href="{escape(target)}"{current}>{label} {count}</a>'

    def line(row: dict) -> str:
        link = escape(f"{here}/seats/{quote(row['name'])}")
        description = f"<small>{escape(row['description'])}</small>" if row["description"] else ""
        cells = [
            f'<a class="mono" href="{link}">{escape(row["name"])}</a>{description}',
            _state(row["state"]), _model(row, upcoming.get(row["template"], "")), _branch(row),
        ]
        return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"

    head = _head(["Seat", "State", "Harness / model", "Branch"])
    groups = [("dead", "Needs attention", ' class="group attention"'),
              ("alive", "Running", ' class="group"')]
    body = ""
    for name, title, cls in groups:
        members = [row for row in rows if row["state"] == name]
        if name in shown and members:
            body += f'<tr{cls}><th colspan="4">{title} · {len(members)}</th></tr>'
            body += "".join(line(row) for row in members)
    killed = [row for row in rows if row["state"] == "killed"]
    table = f'<table class="agents">{head}<tbody>{body}</tbody></table>' if body else ""
    if killed and "killed" in shown:
        rows_html = "".join(line(row) for row in killed)
        summary = f"Killed · {len(killed)}. Names stay reserved until foil seat archive."
        opened = " open" if state in ("killed", "all") else ""
        table += (
            f'<details class="killed-seats"{opened}><summary>{escape(summary)}</summary>'
            f'<table class="agents"><tbody>{rows_html}</tbody></table></details>'
        )
    elif killed:
        table += (f'<p class="bar muted">{len(killed)} killed not shown. '
                  f'<a href="{escape(here)}?state=all#agents">Show all</a></p>')
    if not table:
        table = '<p class="empty">No seats in this state.</p>' if rows else (
            '<p class="empty">No seats.</p>')
    return f'<p class="chips">{chips}</p>' + f'<div class="box">{table}</div>'


def _fleet_page(root: Path, fleet: str, query: dict, base: str) -> _Page:
    here = f"{base}/fleets/{quote(fleet)}"
    opened = query.get("open", [""])[0]
    if opened:
        return _file_page(root, fleet, opened, here)
    state = query.get("state", [""])[0]
    state = state if state in FILTERS else ""
    try:
        registry = load_registry(root, fleet)
        rows = _ordered(seat_rows(root, registry))
        attach, failure = _command(_attach(registry)), None
    except Exception as exc:
        registry, rows, failure = None, [], exc
        attach = f"<span>Attach{_tip('Attach')}</span>{_failed(exc)}"

    def overrides() -> str:
        found = fleet_overrides(root, fleet)
        if not found:
            return _table(["Role", "Entries, in order"], [], _FLEET_NONE)
        mapped = _project_mapped(root)
        lines = []
        for role, entries in _by_role(found).items():
            count = _stored(registry, role) if registry is not None else None
            mark = None if count is None else count % len(entries)
            notes = "; ".join(item["note"] for item in entries if item["note"])
            note = f"<small>{escape(notes)}</small>" if notes else ""
            href = f"{base}/settings" if role in mapped else "/settings"
            name = f'<a href="{escape(href)}">{_mono(role)}</a>'
            lines.append([name, _rotation(entries, mark) + note])
        return _table(["Role", "Entries, in order"], lines, _FLEET_NONE)

    def agents() -> str:
        if registry is None:
            return f'<div class="box">{_failed(failure or Exception())}</div>'
        return _agents(rows, _next_entries(root, fleet, registry), here, state)

    noun = "seat" if len(rows) == 1 else "seats"
    workspace = _guard(lambda: f"<code>{escape(_checkout(root))}</code>")
    body = (
        f'<section id="agents"><h2>{_label("Agents")} <span class="muted">{len(rows)} '
        f"{noun}</span></h2>{_guard(agents)}</section>"
        + _board_sections(root, fleet, here)
        + _section("Fleet overrides", _guard(overrides), tip="Overrides")
        + f'<div class="facts">{attach}'
        + f"<span>Workspace{_tip('Workspace')}</span>{workspace}</div>"
    )
    return _Page(body, [(fleet, None)])


def _lines(value: str) -> int:
    return int(value) if value.isdecimal() and 1 <= int(value) <= MAX_LINES else 40


def _seat_page(root: Path, fleet: str, name: str, lines: int, base: str) -> _Page:
    registry = load_registry(root, fleet)
    if not SAFE_ID.fullmatch(name) or name not in registry["seats"]:
        raise _Missing(f"No seat named {name} in fleet {fleet}.")
    row = seat_rows(root, registry, only=name, owned=True)[0]
    upcoming = _next_entries(root, fleet, registry).get(row["template"], "")
    fleet_link = f"{base}/fleets/{quote(fleet)}"
    here = f"{fleet_link}/seats/{quote(name)}"
    order = [item["name"] for item in _ordered(seat_rows(root, registry))]
    at = order.index(name)
    steps = [
        f'<a href="{escape(fleet_link)}/seats/{quote(order[at + step])}">{label} '
        f"{escape(order[at + step])}</a>"
        for step, label in ((-1, "&larr;"), (1, "&rarr;"))
        if 0 <= at + step < len(order)
    ]
    facts = [
        ("Role", _mono(row["template"])), ("Description", escape(row["description"])),
        ("State", _state(row["state"])), ("Harness / model", _model(row, upcoming)),
        ("Branch", _branch(row)),
    ]
    listed = "".join(f"<dt>{_label(label)}</dt><dd>{value}</dd>" for label, value in facts)
    loaded = time.strftime("%H:%M:%S")
    try:
        pane = peek_text(root, name, lines=lines, fleet=fleet)
    except Exception as exc:
        back = ""
        if lines > 40:
            shorter = max(40, lines // 2)
            back = f' <a href="{escape(here)}?lines={shorter}">Show {shorter} lines</a>'
        pane_box, meta = f'<p class="empty err">{escape(_error(exc))}{back}</p>', ""
    else:
        links = [f'<a href="{escape(here)}?lines={lines}">Refresh</a>',
                 '<a href="#latest">Jump to latest</a>']
        if lines < MAX_LINES:
            links.append(f'<a href="{escape(here)}?lines={min(MAX_LINES, lines * 2)}">More</a>')
        pane_box = (
            f'<p class="bar"><span class="muted">Loaded at {loaded}</span>{" ".join(links)}</p>'
            f'<pre aria-label="Captured pane text">{escape(pane)}</pre><span id="latest"></span>'
        )
        meta = f"{len(pane.splitlines())} lines"
    body = (
        f'<div><h1 class="mono">{escape(name)}</h1>'
        f'<p class="bar muted" style="padding:0">{" ".join(steps)}</p></div>'
        + f'<dl class="facts">{listed}</dl>'
        + f'<div class="facts">{_command(_attach(registry, row))}</div>'
        + _section("Pane", pane_box, meta=meta)
    )
    return _Page(body, [(fleet, fleet_link), (name, None)])


class _Project(NamedTuple):
    """One folder in the store, and its checkout when that resolves back to it."""

    key: str
    name: str
    checkout: str
    root: Path | None


def _store() -> Path | None:
    directory = store_root() / "projects"
    return None if directory.is_symlink() or not directory.is_dir() else directory


def _listed_folder(folder: Path) -> bool:
    return (
        _KEY.fullmatch(folder.name) is not None
        and not folder.is_symlink()
        and folder.is_dir()
        and (folder / "project.json").is_file()
    )


def _projects() -> list[_Project]:
    directory = _store()
    if directory is None:
        return []
    return [_project(folder) for folder in sorted(directory.iterdir()) if _listed_folder(folder)]


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
            checkout = f"<code>{escape(item.checkout)}</code>" if item.checkout else _faded(
                "not recorded")
            rows.append([name, checkout, fleets])
        return _table(["Project", "Checkout", "Fleets"], rows, "No projects. Run foil init.")

    return (
        f'<div><h1>Projects</h1><p class="muted">{escape(PROJECTS)}</p></div>'
        + _section("Projects", _guard(projects))
    )


_Result = tuple[int, str, tuple[str, str] | None, list, bool]


def _route(target: str) -> _Result | _Raw:
    """A page as (status, body, project name and link, trail, settings), or a file to stream."""

    parts = urlsplit(target)
    path = [unquote(item) for item in parts.path.split("/") if item]
    query = parse_qs(parts.query)
    home: tuple[str, str] | None = None
    trail: list = []
    try:
        if not path:
            return 200, _projects_page(), None, [], False
        if path == ["settings"]:
            page = _global_settings_page()
            return 200, page.body, None, [], page.settings
        if len(path) < 2 or path[0] != "projects" or not _KEY.fullmatch(path[1]):
            raise _Missing("No such page.")
        directory = _store()
        folder = directory / path[1] if directory is not None else None
        if folder is None or not _listed_folder(folder):
            raise _Missing(f"No project named {path[1]} on this machine.")
        project = _project(folder)
        if project.root is None:
            raise _Missing(f"The checkout of {project.name} is gone or moved. Run foil init in it.")
        root, base, rest = project.root, f"/projects/{quote(project.key)}", path[2:]
        home = (project.name, base)
        page: _Page
        if not rest:
            page = _fleets_page(root, base)
        elif rest == ["settings"]:
            page = _settings_page(root, base)
        elif len(rest) < 2 or rest[0] != "fleets":
            raise _Missing("No such page.")
        elif rest[1] not in _fleet_ids(root):
            raise _Missing(f"No fleet named {rest[1]} in this project.")
        elif len(rest) == 2:
            trail = [(rest[1], f"{base}/fleets/{quote(rest[1])}")]
            page = _fleet_page(root, rest[1], query, base)
        elif rest[2:] == ["raw"]:
            fleet, opened = rest[1], query.get("open", [""])[0]
            if opened not in {item[0] for item in _board_files(root, fleet)}:
                raise _Missing("No such file on this board.")
            handle = _open_listed(root, fleet, opened)
            size = os.fstat(handle).st_size
            if size > RAW_CAP:
                os.close(handle)
                cap = f"foil: the raw view stops at {_size(RAW_CAP)}; this file is {_size(size)}"
                return 413, _failed(FoilError(cap)), home, [(fleet, None)], False
            return _Raw(handle, size)
        elif len(rest) == 4 and rest[2] == "seats":
            trail = [(rest[1], f"{base}/fleets/{quote(rest[1])}")]
            lines = _lines(query.get("lines", [""])[0])
            page = _seat_page(root, rest[1], rest[3], lines, base)
        else:
            raise _Missing("No such page.")
        return 200, page.body, home, page.trail, page.settings
    except _Missing as exc:
        back = f'<a href="{escape(home[1])}">Back to fleets</a>' if home else (
            '<a href="/">Back to projects</a>'
        )
        return 404, f'<p class="empty">{escape(str(exc))} {back}</p>', home, trail, False
    except Exception as exc:
        return 500, _failed(exc), home, trail, False


def _document(result: _Result, nonce: str) -> bytes:
    _code, body, home, trail, settings = result
    crumbs = [("Foil", "/")]
    if home:
        crumbs.append((home[0], home[1]))
    crumbs += trail
    if len(crumbs) == 2 and not settings and home:
        crumbs[-1] = (home[0], None)
    items = []
    for index, (label, href) in enumerate(crumbs):
        current = index == len(crumbs) - 1 and not settings
        mark = ' aria-current="page"' if current else ""
        cls = ' class="mono"' if index else ""
        if href and not current:
            items.append(f'<li><a{cls} href="{escape(href)}">{escape(label)}</a></li>')
        else:
            items.append(f"<li><span{cls}{mark}>{escape(label)}</span></li>")
    title = " · ".join([label for label, _href in crumbs[1:]][::-1] + ["Foil"])
    settings_href = f"{home[1]}/settings" if home else "/settings"
    current = ' aria-current="page"' if settings else ""
    link = (
        f'<a class="tab" href="{escape(settings_href)}"{current}>Settings</a>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<link rel="icon" href="data:,"><title>{escape(title)}</title>'
        f'<style>{_CSS}</style></head><body><header><nav aria-label="Breadcrumb"><ol>'
        f"{''.join(items)}</ol></nav>{link}</header>"
        f"<main>{body}</main><footer>{escape(FOOTER)}</footer>"
        f'<script nonce="{nonce}">{_SCRIPT}</script></body></html>'
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
                    page = _document(result, nonce)
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

    @staticmethod
    def _discard(request: Any) -> None:
        """Read what the client already sent, so closing is not a reset."""

        try:
            request.setblocking(False)
            while request.recv(CHUNK):
                pass
        except OSError:
            pass

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.connections.acquire(blocking=False):
            # Unread request bytes make a plain close a reset. Discard them first.
            self._discard(request)
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
