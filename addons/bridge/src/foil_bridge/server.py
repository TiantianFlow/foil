"""Loopback page. A token is required on every request; the tailnet check is extra."""

from __future__ import annotations

import base64
import binascii
import contextlib
import errno
import json
import os
import re
import secrets
import signal
import threading
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingTCPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from foil.bridge_cmd import bridge_port, ensure_desk_token
from foil.errors import FoilError
from foil.project import store_root
from foil_bridge import auth, chat, controls, evidence, home, push, tailnet, terminal
from foil_bridge.pages import (
    asset,
    desk_html,
    evidence_html,
    home_html,
    icon_png,
    manifest,
    pair_page,
)
from foil_bridge.qr import svg
from foil_bridge.watch import Watcher

_ID = re.compile(r"[A-Za-z0-9._/-]{1,512}")
_SHELL = {
    "/app.js": ("app.js", "text/javascript", "max-age=0"),
    "/sw.js": ("sw.js", "text/javascript", "max-age=0"),
    "/desk.css": ("desk.css", "text/css", "max-age=0"),
    "/home.js": ("home.js", "text/javascript", "max-age=0"),
    "/seat.js": ("seat.js", "text/javascript", "max-age=0"),
    "/term.js": ("term.js", "text/javascript", "max-age=0"),
    "/controls.js": ("controls.js", "text/javascript", "max-age=0"),
    "/offline": ("offline.html", "text/html", "max-age=0"),
}
_PAGE_POLICY = (
    "default-src 'none'; style-src 'self'; script-src 'self'; img-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _websocket(headers: object) -> bool:
    upgrade = str(getattr(headers, "get", lambda *_args: "")("Upgrade", ""))
    connection = str(getattr(headers, "get", lambda *_args: "")("Connection", ""))
    return upgrade.lower() == "websocket" and "upgrade" in connection.lower()


def _websocket_key(key: str) -> bool:
    try:
        return len(base64.b64decode(key, validate=True)) == 16
    except (binascii.Error, ValueError):
        return False


def host_name(host: str) -> str:
    match = re.fullmatch(r"(\[::1\]|[A-Za-z0-9.-]+)(?::\d{1,5})?", host)
    return match.group(1).lower() if match else ""


def interval() -> float:
    raw = os.environ.get("FOIL_BRIDGE_INTERVAL", "").strip()
    try:
        value = float(raw) if raw else 1.0
    except ValueError:
        return 1.0
    if 0 < value <= 60:
        return value
    return 1.0


def item_path(root: Path, text: str) -> Path | None:
    if (
        not _ID.fullmatch(text)
        or text.startswith("/")
        or text == "bridge"
        or text.startswith("bridge/")
    ):
        return None
    if ".." in text.split("/"):
        return None
    path = root / text
    if path.is_symlink():
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root.resolve()):
        return None
    return path


class BridgeServer(ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    root: Path
    node: tuple[str, str] | None
    watch: Watcher
    stopped: threading.Event

    def shutdown(self) -> None:
        terminal.end_sessions()
        super().shutdown()


class Handler(BaseHTTPRequestHandler):
    server: BridgeServer
    protocol_version = "HTTP/1.1"

    def version_string(self) -> str:
        return "foil-bridge"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """No access log. A token must not be printed."""

    def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
        """Nothing about a request is printed."""

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        except Exception:
            self._send(500, b"")

    def _route(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = unquote(parsed.path) or "/"
        query = parse_qs(parsed.query)
        if path == "/pair" and method in {"GET", "HEAD"}:
            self._pair_get(query, method)
            return
        if path == "/v1/pair" and method == "POST":
            self._pair_post()
            return
        identity = self._identity(query)
        if identity is None:
            self._send(401, b"")
            return
        if not self._host_ok():
            self._send(403, b"")
            return
        if method == "POST" and not self._origin_ok():
            self._send(403, b"")
            return
        self._authed(method, path, identity)

    def _pair_get(self, query: Mapping[str, list[str]], method: str) -> None:
        code = (query.get("code") or [""])[-1]
        if not auth.code_pending(code):
            self._send(401, b"")
            return
        if not self._host_ok():
            self._send(403, b"")
            return
        nonce = secrets.token_urlsafe(16)
        policy = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            f"script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'"
        )
        body = b"" if method == "HEAD" else pair_page(code, nonce)
        self._send(200, body, "text/html", policy=policy, force=True)

    def _pair_post(self) -> None:
        body = self._json()
        code = body.get("code") if isinstance(body, dict) else ""
        if not isinstance(code, str) or not auth.code_pending(code):
            self._send(401, b"")
            return
        if not self._host_ok() or not self._origin_ok():
            self._send(403, b"")
            return
        if not auth.consume(code):
            self._send(401, b"")
            return
        self._json_ok(auth.new_device())

    def _authed(self, method: str, path: str, identity: auth.Identity) -> None:
        if path == "/v1/terminal":
            self._terminal()
            return
        if path == "/" and method in {"GET", "HEAD"}:
            self._send(200, home_html(identity.token), "text/html", policy=_PAGE_POLICY)
            return
        if path == "/desk" and method in {"GET", "HEAD"}:
            self._send(200, desk_html(identity.token), "text/html", policy=_PAGE_POLICY)
            return
        if path == "/evidence" and method in {"GET", "HEAD"}:
            self._evidence(identity.token)
            return
        if path == "/manifest.webmanifest" and method in {"GET", "HEAD"}:
            self._send(
                200, manifest(identity.token), "application/manifest+json", cache="max-age=0"
            )
            return
        if path in _SHELL and method in {"GET", "HEAD"}:
            name, kind, cache = _SHELL[path]
            if name.endswith(".html"):
                policy = _PAGE_POLICY
            elif name == "sw.js":
                policy = "default-src 'none'; connect-src 'self'"
            else:
                policy = ""
            self._send(200, asset(name), kind, policy=policy, cache=cache)
            return
        if path == "/icon-192.png" and method in {"GET", "HEAD"}:
            self._send(200, icon_png(192), "image/png", cache="max-age=0")
            return
        if path == "/icon-512.png" and method in {"GET", "HEAD"}:
            self._send(200, icon_png(512), "image/png", cache="max-age=0")
            return
        if path == "/v1/events" and method in {"GET", "HEAD"}:
            self._events(method)
            return
        if path == "/v1/items" and method in {"GET", "HEAD"}:
            self._json_ok(self.server.watch.items())
            return
        if path.startswith("/v1/items/") and method in {"GET", "HEAD"}:
            self._one_item(path[len("/v1/items/") :])
            return
        if path == "/v1/home" and method in {"GET", "HEAD"}:
            self._call(home.snapshot)
            return
        if path == "/v1/send" and method == "POST":
            self._send_message()
            return
        if path == "/v1/chat" and method in {"GET", "HEAD"}:
            self._chat()
            return
        if path == "/v1/chat" and method == "POST":
            self._post_chat()
            return
        if path == "/v1/answer" and method == "POST":
            self._answer()
            return
        if path == "/v1/act" and method == "POST":
            self._act()
            return
        if path == "/v1/control" and method == "POST":
            self._control()
            return
        if path == "/v1/push" and method in {"GET", "HEAD"}:
            self._call(lambda: push.view(push.owner_id(identity.kind, identity.id)))
            return
        if path == "/v1/push" and method == "POST":
            self._subscribe(identity)
            return
        if path == "/v1/push/settings" and method == "POST":
            self._push_settings()
            return
        if path == "/v1/push/card" and method in {"GET", "HEAD"}:
            self._push_card()
            return
        if path == "/v1/session" and method in {"GET", "HEAD"}:
            if identity.kind == "desk":
                self._json_ok({"kind": "desk"})
            else:
                self._json_ok({"kind": "device", "id": identity.id})
            return
        if path == "/v1/devices" and method in {"GET", "HEAD"}:
            self._json_ok(auth.device_rows())
            return
        if path == "/v1/pairing" and method == "POST":
            if not self._discard():
                self._send(401, b"")
                return
            if identity.kind != "desk":
                self._send(403, b"")
                return
            self._json_ok(auth.mint(self.server.node))
            return
        if (
            path.startswith("/v1/pairing/")
            and path.endswith("/qr.svg")
            and method in {"GET", "HEAD"}
        ):
            self._qr(path, identity)
            return
        if path.startswith("/v1/devices/") and path.endswith("/revoke") and method == "POST":
            if not self._discard():
                self._send(401, b"")
                return
            identifier = path[len("/v1/devices/") : -len("/revoke")]
            self._revoke(identity, identifier)
            return
        if method not in {"GET", "HEAD", "POST"}:
            self._send(405, b"")
            return
        self._send(404, b"")

    def _terminal(self) -> None:
        if self.command != "GET" or not _websocket(self.headers):
            self._send(404, b"")
            return
        if not self._origin_ok():
            self._send(403, b"")
            return
        key = self.headers.get("Sec-WebSocket-Key", "")
        if self.headers.get("Sec-WebSocket-Version") != "13" or not _websocket_key(key):
            self._send(400, b"")
            return
        raw = (parse_qs(urlsplit(self.path).query).get("path") or [""])[-1]
        directory = terminal.owned_worktree(raw)
        if directory is None or not terminal.acquire():
            self._send(403, b"")
            return
        try:
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", terminal.accept_key(key))
            self.end_headers()
            self.close_connection = True
            terminal.session(self.rfile, self.wfile, self.connection, directory)
        except Exception:
            self.close_connection = True
        finally:
            terminal.release()

    def _qr(self, path: str, identity: auth.Identity) -> None:
        if identity.kind != "desk":
            self._send(403, b"")
            return
        code = path[len("/v1/pairing/") : -len("/qr.svg")]
        if not auth.code_pending(code):
            self._send(404, b"")
            return
        node = self.server.node
        text = f"https://{node[0]}/pair?code={code}" if node is not None else code
        self._send(200, svg(text).encode(), "image/svg+xml")

    def _revoke(self, identity: auth.Identity, identifier: str) -> None:
        result = auth.revoke(identity, identifier)
        if result == "ok":
            push.drop(identifier)
            self._json_ok({"id": identifier, "revoked": True})
            return
        if result == "forbidden":
            self._send(403, b"")
            return
        self._send(404, b"")

    def _one_item(self, text: str) -> None:
        path = item_path(self.server.root, text)
        if path is None or not path.is_file():
            self._send(404, b"")
            return
        try:
            mtime = path.stat().st_mtime_ns // 1_000_000_000
        except OSError:
            self._send(404, b"")
            return
        self._json_ok({"id": text, "mtime": mtime})

    def _events(self, method: str) -> None:
        if method == "HEAD":
            self._send(200, b"", "text/event-stream")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        cursor = self.server.watch.generation
        try:
            while not self.server.stopped.is_set():
                items, cursor = self.server.watch.wait(cursor, 15)
                if self.server.stopped.is_set():
                    break
                if not items:
                    self.wfile.write(b":\n\n")
                else:
                    for item in items:
                        payload = json.dumps({"id": item}, separators=(",", ":")).encode()
                        self.wfile.write(b"data: " + payload + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            return

    def _identity(self, query: Mapping[str, list[str]]) -> auth.Identity | None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:].strip()
        else:
            values = query.get("token") or []
            token = values[-1] if values else ""
        if not token:
            return None
        return auth.identify(token)

    def _host_ok(self) -> bool:
        host = self.headers.get("Host") or ""
        name = host_name(host)
        if name in tailnet.LOOPBACK:
            return not any(field in self.headers for field in tailnet.PROXIED)
        node = self.server.node
        if node is None or name != node[0]:
            return False
        logins = self.headers.get_all("Tailscale-User-Login") or []
        forwarded = self.headers.get_all("X-Forwarded-Host") or []
        return logins == [node[1]] and forwarded == [host]

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        host = self.headers.get("Host") or ""
        if not origin or not host:
            return False
        name = host_name(host)
        if name in tailnet.LOOPBACK:
            return origin == f"http://{host}"
        node = self.server.node
        if node is not None and name == node[0]:
            return origin in {f"https://{host}", f"http://{host}"}
        return False

    def _discard(self) -> bool:
        raw = self.headers.get("Content-Length", "0")
        if not raw.isascii() or not raw.isdecimal():
            self.close_connection = True
            return False
        length = int(raw)
        if length > 4096:
            self.close_connection = True
            return False
        if length:
            self.rfile.read(length)
        return True

    def _length(self, limit: int) -> int | None:
        raw = self.headers.get("Content-Length", "0")
        if not raw.isascii() or not raw.isdecimal():
            return None
        length = int(raw)
        if length > limit:
            self.close_connection = True
            return None
        return length

    def _json(self, limit: int = 4096) -> dict[str, Any] | None:
        length = self._length(limit)
        if length is None:
            return None
        try:
            value = json.loads(self.rfile.read(length).decode() or "null")
        except (UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict):
            return None
        return value

    def _call(self, function) -> None:
        try:
            body = function()
        except FoilError as exc:
            self._error(exc)
            return
        self._json_ok(body)

    def _error(self, exc: FoilError) -> None:
        self._send(400, json.dumps({"error": str(exc)}).encode(), "application/json")

    def _chat(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        project = (query.get("project") or [""])[-1]
        fleet = (query.get("fleet") or [""])[-1]
        seat = (query.get("seat") or [""])[-1]
        self._call(lambda: chat.thread(project, fleet, seat))

    def _post_chat(self) -> None:
        if self._length(64 * 1024) is None:
            self._error(FoilError("foil: message text is too long"))
            return
        body = self._json(64 * 1024)
        if not isinstance(body, dict):
            self._error(FoilError("foil: message text is empty"))
            return
        project, fleet, seat, text = (
            body.get("project"),
            body.get("fleet"),
            body.get("seat"),
            body.get("text"),
        )
        if not all(isinstance(item, str) for item in (project, fleet, seat, text)):
            self._error(FoilError("foil: message text is empty"))
            return
        self._call(lambda: chat.post(project, fleet, seat, text) or {"ok": True})

    def _send_message(self) -> None:
        if self._length(64 * 1024) is None:
            self._error(FoilError("foil: message text is too long"))
            return
        body = self._json(64 * 1024)
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            self._error(FoilError("foil: message text is empty"))
            return
        self._call(lambda: home.post_message(text) or {"ok": True})

    def _answer(self) -> None:
        body = self._json()
        if not isinstance(body, dict):
            self._error(FoilError("foil: answer text is empty"))
            return
        project, fleet, question_id, text = (
            body.get("project"),
            body.get("fleet"),
            body.get("id"),
            body.get("text"),
        )
        if not all(isinstance(item, str) for item in (project, fleet, question_id, text)):
            self._error(FoilError("foil: answer text is empty"))
            return
        self._call(lambda: home.post_answer(project, fleet, question_id, text) or {"ok": True})

    def _subscribe(self, identity: auth.Identity) -> None:
        body = self._json(8192)
        if not isinstance(body, dict):
            self._error(FoilError("foil: push subscription is invalid"))
            return
        owner = push.owner_id(identity.kind, identity.id)
        self._call(lambda: push.save_subscription(owner, body))

    def _push_settings(self) -> None:
        body = self._json()
        if not isinstance(body, dict):
            self._error(FoilError("foil: push setting is invalid"))
            return
        self._call(lambda: push.update_settings(body))

    def _push_card(self) -> None:
        card = (parse_qs(urlsplit(self.path).query).get("id") or [""])[-1]
        described = push.describe_card(card)
        if described is None:
            self._send(404, b"")
            return
        self._json_ok(described)

    def _act(self) -> None:
        body = self._json()
        name = body.get("id") if isinstance(body, dict) else None
        if not isinstance(name, str) or not isinstance(body, dict):
            self._error(FoilError("foil: unknown card"))
            return
        if "confirm" in body and not isinstance(body["confirm"], bool):
            self._error(FoilError("foil: unknown card"))
            return
        confirm = body.get("confirm") is True
        self._call(lambda: home.run_card(name, confirm=confirm) or {"ok": True})

    def _control(self) -> None:
        if self._length(64 * 1024) is None:
            self._error(FoilError("foil: task text is too long"))
            return
        body = self._json(64 * 1024)
        if not isinstance(body, dict):
            self._error(FoilError("foil: control is invalid"))
            return
        if "id" in body:
            self._call(lambda: home.run_control_card(body) or {"ok": True})
            return
        self._call(lambda: controls.apply(body) or {"ok": True})

    def _evidence(self, token: str) -> None:
        query = parse_qs(urlsplit(self.path).query)
        target = (query.get("target") or [""])[-1]
        level = (query.get("level") or ["summary"])[-1]
        try:
            model = evidence.page(target, level)
            body = None if model is None else evidence_html(model, token)
        except (OSError, UnicodeDecodeError, UnicodeEncodeError, ValueError):
            self._send(404, b"")
            return
        if body is None:
            self._send(404, b"")
            return
        self._send(200, body, "text/html", policy=_PAGE_POLICY)

    def _json_ok(self, body: object) -> None:
        self._send(200, json.dumps(body).encode(), "application/json")

    def _send(
        self,
        code: int,
        payload: bytes,
        kind: str = "text/plain",
        *,
        policy: str = "",
        cache: str = "no-store",
        force: bool = False,
    ) -> None:
        if self.command == "HEAD" and not force:
            payload = b""
        self.send_response(code)
        if kind.startswith("image/") and kind != "image/svg+xml":
            self.send_header("Content-Type", kind)
        else:
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", policy or "default-src 'none'")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if payload and self.command != "HEAD":
            self.wfile.write(payload)


def open_server(port: int | None = None) -> BridgeServer:
    """Bind ``127.0.0.1`` only. ``port`` 0 asks the OS for a free port."""

    ensure_desk_token()
    bind = bridge_port() if port is None else port
    try:
        server = BridgeServer(("127.0.0.1", bind), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise FoilError(f"foil: port {bind} is in use") from None
        raise FoilError(f"foil: could not listen on 127.0.0.1:{bind}") from None
    server.root = store_root()
    server.node = tailnet.tailnet()
    server.stopped = threading.Event()
    server.watch = Watcher(server.root, interval())
    server.watch.start()
    server.push_thread = threading.Thread(
        target=_push_loop, args=(server.stopped,), name="foil-bridge-push", daemon=True
    )
    server.push_thread.start()
    if not server.watch.ready.wait(5):
        server.stopped.set()
        server.watch.stop()
        server.server_close()
        server.push_thread.join(timeout=2)
        raise FoilError("foil: bridge watcher did not start")
    return server


def _push_loop(stopped: threading.Event) -> None:
    while not stopped.is_set():
        with contextlib.suppress(Exception):
            push.deliver()
        stopped.wait(interval())


def serve() -> int:
    def stop(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, stop)
    server = None
    try:
        server = open_server()
        bound = server.server_address[1]
        print(f"foil-bridge: http://127.0.0.1:{bound}/", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if server is not None:
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            server.push_thread.join(timeout=2)
        signal.signal(signal.SIGTERM, previous)
    return 0
