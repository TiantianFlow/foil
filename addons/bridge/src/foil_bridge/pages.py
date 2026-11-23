"""The desk page, the home, the offline page, the manifest, and the icon."""

from __future__ import annotations

import html
import json
import struct
import zlib
from importlib.resources import files
from typing import Any
from urllib.parse import quote, urlencode

_ICONS: dict[int, bytes] = {}


def asset(name: str) -> bytes:
    return (files("foil_bridge") / "web" / name).read_bytes()


def desk_html(token: str) -> bytes:
    page = asset("desk.html").decode()
    return page.replace("__TOKEN__", quote(token, safe="")).encode()


def home_html(token: str) -> bytes:
    page = asset("home.html").decode()
    return page.replace("__TOKEN__", quote(token, safe="")).encode()


def _href(token: str, target: str, level: str = "") -> str:
    query = {"target": target, "token": token}
    if level:
        query["level"] = level
    return "/evidence?" + urlencode(query)


def _anchor(token: str, target: str, label: str, level: str = "") -> str:
    address = html.escape(_href(token, target, level), quote=True)
    return f'<a href="{address}">{html.escape(label)}</a>'


def _crumbs(model: dict[str, Any], token: str) -> str:
    parts = []
    for crumb in model["crumbs"]:
        target = crumb["target"]
        if target == "/":
            address = "/?" + urlencode({"token": token})
        elif target:
            address = _href(token, target)
        else:
            parts.append(html.escape(crumb["label"]))
            continue
        parts.append(
            f'<a href="{html.escape(address, quote=True)}">{html.escape(crumb["label"])}</a>'
        )
    return f'<p class="crumbs">{" / ".join(parts)}</p>'


def _meter(totals: dict[str, int]) -> str:
    return f'<p class="mono">in {totals["input"]} out {totals["output"]}</p>'


def _shell(token: str, body: str) -> bytes:
    style = quote(token, safe="")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Foil</title>
<link rel="stylesheet" href="desk.css?token={style}">
</head>
<body>
<main>
<p class="mark">Foil</p>
{body}
</main>
</body>
</html>
""".encode(errors="replace")


def evidence_html(model: dict[str, Any], token: str) -> bytes:
    """One read-only evidence page. Text in ``model`` is already masked."""

    kind = model["kind"]
    if kind == "fleets":
        inner = _fleets_html(model, token)
    elif kind == "fleet":
        inner = _fleet_html(model, token)
    elif kind == "file":
        inner = _file_html(model)
    else:
        inner = _seat_html(model, token)
    note = html.escape(model["note"])
    return _shell(token, _crumbs(model, token) + inner + f'<p class="quiet">{note}</p>')


def _fleets_html(model: dict[str, Any], token: str) -> str:
    rows = model["fleets"]
    if not rows:
        listing = '<p>No fleet.</p>'
    else:
        items = []
        for row in rows:
            link = _anchor(token, row["target"], row["fleet"])
            if row["rank"] is None:
                detail = "No inbox item."
            else:
                detail = f"rank {row['rank']} {row['kind']}: {row['title']}"
            items.append(f"<li>{link}<span>{html.escape(detail)}</span></li>")
        listing = "<ul>" + "".join(items) + "</ul>"
    return f"<h1>{html.escape(model['title'])}</h1>{listing}"


def _fleet_html(model: dict[str, Any], token: str) -> str:
    state = model["state"] or "—"
    if model["paused"]:
        state = f"{state}, fleet paused"
    decisions = _lines(model["decisions"])
    journal = _lines(model["journal"])
    questions = _questions(model["questions"], token)
    seats = _seats(model["seats"], token)
    files = _files(model["files"], token)
    return (
        f"<h1>{html.escape(model['title'])}</h1>"
        f"<h2>Goal</h2><p>{html.escape(model['goal'] or 'None.')}</p>"
        f"<h2>State</h2><p>{html.escape(state)}</p>"
        f"{_fleet_controls(model, token)}"
        f"<h2>Decisions</h2>{decisions}"
        f"<h2>Journal</h2>{journal}"
        f"<h2>Questions</h2>{questions}"
        f"<h2>Seats</h2>{seats}"
        f"<h2>Tokens</h2>{_meter(model['tokens'])}"
        f"<h2>Board</h2>{files}"
    )


def _button(
    label: str, op: str, *, confirm: bool = False, role: str = "", seat: str = ""
) -> str:
    parts = [f' data-op="{html.escape(op, quote=True)}"']
    if role:
        parts.append(f' data-role="{html.escape(role, quote=True)}"')
    if seat:
        parts.append(f' data-seat="{html.escape(seat, quote=True)}"')
    if confirm:
        parts.append(' data-confirm="yes"')
    return f'<button type="button"{"".join(parts)}>{html.escape(label)}</button>'


def _control_open(project: str, fleet: str) -> str:
    project_attr = html.escape(project, quote=True)
    fleet_attr = html.escape(fleet, quote=True)
    return (
        f'<section id="controls" data-project="{project_attr}" data-fleet="{fleet_attr}">'
        "<h2>Controls</h2>"
        '<p class="quiet">A tap runs as you. Kill, archive, and anything this '
        "fleet's mandate does not cover ask first.</p>"
    )


def _control_script(token: str) -> str:
    script = quote(token, safe="")
    return (
        '<p id="control-error" class="quiet"></p></section>'
        f'<script src="controls.js?token={script}" type="module"></script>'
    )


def _fleet_controls(model: dict[str, Any], token: str) -> str:
    project = model["crumbs"][1]["label"]
    fleet = model["crumbs"][2]["label"]
    if model["paused"]:
        toggle = _button("Resume fleet", "resume-fleet", confirm=bool(model["resume_confirm"]))
    else:
        toggle = _button("Pause", "pause", confirm=bool(model["pause_confirm"]))
    roles = model["roles"]
    if not roles:
        listing = "<p>None.</p>"
    else:
        items = []
        for row in roles:
            if row["note"]:
                detail = html.escape(row["note"])
                button = ""
            else:
                shown = row["model"] or "harness default"
                detail = html.escape(f"{row['harness']} / {shown}")
                button = _button(
                    f"Spawn {row['role']}",
                    "spawn",
                    role=row["role"],
                    confirm=bool(row["confirm"]),
                )
            items.append(f"<li>{html.escape(row['role'])} <span>{detail}</span> {button}</li>")
        listing = "<ul>" + "".join(items) + "</ul>"
    goal = (
        '<p><label>Fleet <input id="goal-fleet" maxlength="128"></label> '
        '<label>Goal <input id="goal-task" maxlength="4000"></label> '
        + _button("Start a new goal", "fleet")
        + "</p>"
    )
    return (
        _control_open(project, fleet)
        + toggle
        + "<h2>Spawn</h2>"
        + listing
        + "<h2>New goal</h2>"
        + goal
        + _control_script(token)
    )


def _seat_controls(model: dict[str, Any], token: str) -> str:
    chat = model["chat"]
    buttons = "".join(
        _button(row["label"], row["op"], confirm=bool(row["confirm"]), seat=chat["seat"])
        for row in model["actions"]
    )
    return _control_open(chat["project"], chat["fleet"]) + buttons + _control_script(token)


def _lines(rows: list[str]) -> str:
    if not rows:
        return "<p>None.</p>"
    return "<ul>" + "".join(f"<li>{html.escape(row)}</li>" for row in rows) + "</ul>"


def _questions(rows: list[dict[str, Any]], token: str) -> str:
    if not rows:
        return "<p>None.</p>"
    blocks = []
    for row in rows:
        links = "".join(
            " " + _anchor(token, item["target"], item["path"] or item["target"])
            for item in row["links"]
        )
        blocks.append(f"<li>{html.escape(row['title'])}{links}</li>")
    return "<ul>" + "".join(blocks) + "</ul>"


def _seats(rows: list[dict[str, Any]], token: str) -> str:
    if not rows:
        return "<p>None.</p>"
    items = []
    for row in rows:
        link = _anchor(token, row["target"], row["name"])
        branch = row["branch"] or "no branch"
        totals = row["tokens"]
        detail = (
            f"{row['template']} {row['state']} {branch} "
            f"in {totals['input']} out {totals['output']}"
        )
        items.append(f"<li>{link}<span>{html.escape(detail)}</span></li>")
    return "<ul>" + "".join(items) + "</ul>"


def _files(rows: list[dict[str, str]], token: str) -> str:
    if not rows:
        return "<p>None.</p>"
    items = "".join(f"<li>{_anchor(token, row['target'], row['path'])}</li>" for row in rows)
    return "<ul>" + items + "</ul>"


def _file_html(model: dict[str, Any]) -> str:
    body = model["file"]
    if body["error"]:
        shown = f"<p>{html.escape(body['error'])}</p>"
    else:
        extra = "<p>truncated</p>" if body["truncated"] else ""
        shown = (
            f'<p class="quiet">{body["size"]} bytes</p>'
            f'<pre class="tail">{html.escape(body["text"])}</pre>'
            f"{extra}"
        )
    return f"<h1>{html.escape(model['title'])}</h1>{shown}"


def _seat_html(model: dict[str, Any], token: str) -> str:
    target = model["target"]
    current = model["level"]
    levels = []
    for level in model["levels"]:
        if level == current:
            levels.append(html.escape(level))
        else:
            levels.append(_anchor(token, target, level, level))
    conversation = model["conversation"]
    source = conversation.get("source") or "none"
    format_id = conversation.get("format") or "unknown"
    parts = [
        f"<h1>{html.escape(model['title'])}</h1>",
        f'<p class="crumbs">{html.escape(model["seat"]["template"])} '
        f'{html.escape(model["seat"]["state"])}</p>',
        _seat_controls(model, token),
        f'<p class="crumbs">{" ".join(levels)}</p>',
        f'<p class="mono">{html.escape(format_id)} {html.escape(source)}</p>',
    ]
    if source == "pane":
        parts.append("<p>Live pane.</p>")
        text = ""
        events = conversation.get("events") or []
        if events:
            text = events[0].get("text") or ""
        parts.append(f'<pre class="tail">{html.escape(text)}</pre>')
    elif model["missing"]:
        parts.append(f"<p>{html.escape(model['missing'])}</p>")
    else:
        lines = "".join(f"<li>{html.escape(line)}</li>" for line in model["lines"])
        parts.append(f"<ul>{lines}</ul>" if lines else "<p>None.</p>")
    if conversation.get("omitted"):
        parts.append(f"<p>omitted {conversation['omitted']}</p>")
    if conversation.get("truncated"):
        parts.append("<p>truncated</p>")
    parts.append("<h2>Tokens</h2>" + _meter(conversation["tokens"]))
    chat = model["chat"]
    project = html.escape(chat["project"], quote=True)
    fleet = html.escape(chat["fleet"], quote=True)
    seat = html.escape(chat["seat"], quote=True)
    script = quote(token, safe="")
    parts.append(
        f'<section id="chat" data-project="{project}" data-fleet="{fleet}" data-seat="{seat}">'
        "<h2>Chat</h2>"
        '<p id="chat-note" class="quiet"></p>'
        '<div id="questions"></div>'
        '<ol id="thread" class="thread"></ol>'
        '<form id="say"><label for="message">Message</label>'
        '<textarea id="message" name="message" rows="3"></textarea>'
        '<button type="submit">Send</button>'
        '<p id="say-error" class="quiet"></p></form></section>'
        f'<script src="seat.js?token={script}" type="module"></script>'
    )
    diff = model["diff"]
    parts.append("<h2 id=\"diff\">Diff</h2>")
    if diff["base"] and diff["branch"]:
        parts.append(
            f'<p class="mono">{html.escape(diff["base"])}...{html.escape(diff["branch"])}</p>'
        )
    if diff["text"]:
        parts.append(f'<pre class="tail">{html.escape(diff["text"])}</pre>')
    if diff["note"]:
        parts.append(f"<p>{html.escape(diff['note'])}</p>")
    return "".join(parts)


def manifest(token: str) -> bytes:
    quoted = quote(token, safe="")
    body = {
        "name": "Foil Bridge",
        "short_name": "Bridge",
        "start_url": f"/?token={quoted}",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f3ecdf",
        "theme_color": "#f3ecdf",
        "icons": [
            {
                "src": f"/icon-192.png?token={quoted}",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": f"/icon-512.png?token={quoted}",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
        ],
    }
    return json.dumps(body).encode()


def pair_page(code: str, nonce: str) -> bytes:
    """The phone page. The code is spent by the script's POST, not by opening this page."""

    payload = json.dumps(code)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Foil Bridge</title>
</head>
<body>
<main>
<p>Pairing this phone with the machine.</p>
<p id="status"></p>
</main>
<script nonce="{nonce}">
const code = {payload};
const status = document.querySelector("#status");
fetch("/v1/pair", {{
  method: "POST",
  headers: {{"Content-Type": "application/json"}},
  body: JSON.stringify({{code}})
}}).then((response) => {{
  if (!response.ok) {{
    status.textContent = "This code was already used.";
    return null;
  }}
  return response.json();
}}).then((body) => {{
  if (!body) return;
  localStorage.setItem("foil-bridge-token", body.token);
  location.replace("/?token=" + encodeURIComponent(body.token));
}}).catch(() => {{
  status.textContent = "This machine can't be reached. Check Tailscale.";
}});
</script>
</body>
</html>
""".encode()


def icon_png(size: int) -> bytes:
    """A copper diamond on paper. Generated, not a copied mark."""

    cached = _ICONS.get(size)
    if cached is not None:
        return cached
    paper = (243, 236, 223, 255)
    copper = (141, 79, 36, 255)
    center = (size - 1) / 2
    outer = size * 0.38
    inner = size * 0.30
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            distance = abs(x - center) + abs(y - center)
            raw.extend(copper if inner <= distance <= outer else paper)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    _ICONS[size] = image
    return image
