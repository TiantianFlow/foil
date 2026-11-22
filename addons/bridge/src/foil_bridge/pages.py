"""The desk page, the home, the offline page, the manifest, and the icon."""

from __future__ import annotations

import html
import json
import struct
import zlib
from importlib.resources import files
from urllib.parse import quote

_ICONS: dict[int, bytes] = {}


def asset(name: str) -> bytes:
    return (files("foil_bridge") / "web" / name).read_bytes()


def desk_html(token: str) -> bytes:
    page = asset("desk.html").decode()
    return page.replace("__TOKEN__", quote(token, safe="")).encode()


def home_html(token: str) -> bytes:
    page = asset("home.html").decode()
    return page.replace("__TOKEN__", quote(token, safe="")).encode()


def evidence_html(described: dict[str, str], token: str) -> bytes:
    """A resolved target. The file itself stays where it is."""

    kind = described["kind"]
    if kind == "file":
        heading = "A file in this fleet."
    elif kind == "fleet":
        heading = "A fleet on this machine."
    else:
        heading = "A project on this machine."
    where = described["project"]
    if described["fleet"]:
        where = f"{where} / {described['fleet']}"
    if described["path"]:
        where = f"{where} / {described['path']}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Foil</title>
<link rel="stylesheet" href="desk.css?token={quote(token, safe="")}">
</head>
<body>
<main>
<p class="mark">Foil</p>
<h1>Evidence</h1>
<p>{html.escape(heading)}</p>
<p class="mono">{html.escape(where)}</p>
<p class="quiet">The file itself is not on this page.</p>
</main>
</body>
</html>
""".encode()


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
