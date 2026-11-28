"""Loopback auth, pairing, and the id-only change stream."""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import pytest
from foil_bridge import auth
from foil_bridge.qr import svg
from foil_bridge.server import open_server
from foil_bridge.watch import diff

from foil.bridge_cmd import read_desk_token
from foil.cli import main
from foil.project import store_root

NODE = ("box.tail1234.ts.net", "owner@github")
SENTINEL = "SENTINEL-BRIDGE-BYTES"
OUTSIDE = "SENTINEL-OUTSIDE-BYTES"


@contextmanager
def _serving(
    monkeypatch: pytest.MonkeyPatch, node: tuple[str, str] | None = None
) -> Iterator[object]:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: node)
    server = open_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.stopped.set()
        server.watch.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    body: object | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    header = dict(headers or {})
    payload = None
    if token:
        header["Authorization"] = f"Bearer {token}"
    if body is not None:
        payload = json.dumps(body).encode()
        header.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=payload, headers=header)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


def _origin(port: int) -> dict[str, str]:
    return {"Origin": f"http://127.0.0.1:{port}"}


def test_no_token_is_refused_including_forged_tailnet_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        assert server.server_address[0] == "127.0.0.1"
        forged = {
            "Tailscale-User-Login": NODE[1],
            "X-Forwarded-For": "203.0.113.8",
            "X-Forwarded-Host": NODE[0],
            "Tailscale-Funnel-Request": "true",
        }
        status, body = _request(port, "GET", "/", headers=forged)
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/", token=read_desk_token(), headers=forged)
        assert status == 403 and body == b""
        status, body = _request(port, "GET", "/", token=read_desk_token())
        assert status == 200 and b"The conversation with the master" in body
        status, body = _request(port, "GET", "/desk", token=read_desk_token())
        assert status == 200 and b"Show a code" in body
        status, body = _request(port, "GET", "/v1/terminal")
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/v1/terminal", token=read_desk_token())
        assert status == 404 and body == b""


def test_tailnet_owner_is_extra_and_still_needs_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serving(monkeypatch, NODE) as server:
        port = server.server_address[1]
        host = {"Host": NODE[0]}
        owner = {
            **host,
            "Tailscale-User-Login": NODE[1],
            "X-Forwarded-Host": NODE[0],
        }
        status, body = _request(port, "GET", "/", headers=owner)
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/", token=read_desk_token(), headers=owner)
        assert status == 200
        wrong = {**owner, "Tailscale-User-Login": "someone-else"}
        status, _body = _request(port, "GET", "/", token=read_desk_token(), headers=wrong)
        assert status == 403


def test_pairing_code_works_once(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        status, _body = _request(port, "POST", "/v1/pairing", token=desk)
        assert status == 403
        status, _body = _request(
            port, "POST", "/v1/pairing", token=desk, headers={"Origin": "http://127.0.0.1:1"}
        )
        assert status == 403
        status, body = _request(port, "POST", "/v1/pairing", token=desk, headers=_origin(port))
        assert status == 200
        minted = json.loads(body)
        code = minted["code"]
        assert minted["url"] == ""
        status, page = _request(port, "GET", f"/pair?code={code}")
        assert status == 200 and code.encode() in page
        assert auth.code_pending(code)
        status, _body = _request(
            port,
            "POST",
            "/v1/pair",
            body={"code": code},
            headers={**_origin(port), "X-Forwarded-For": "203.0.113.9"},
        )
        assert status == 403
        assert auth.code_pending(code)
        status, body = _request(
            port, "POST", "/v1/pair", body={"code": code}, headers=_origin(port)
        )
        assert status == 200
        device = json.loads(body)
        assert auth.code_pending(code) is False
        status, _body = _request(
            port, "POST", "/v1/pair", body={"code": code}, headers=_origin(port)
        )
        assert status == 401
        status, _body = _request(
            port, "POST", "/v1/pairing", token=device["token"], headers=_origin(port)
        )
        assert status == 403
        status, listed = _request(port, "GET", "/v1/devices", token=desk)
        assert status == 200 and device["id"].encode() in listed
        status, _body = _request(
            port,
            "POST",
            f"/v1/devices/{device['id']}/revoke",
            token=desk,
            headers=_origin(port),
        )
        assert status == 200
        status, listed = _request(port, "GET", "/v1/devices", token=desk)
        assert device["id"].encode() not in listed


def test_manifest_worker_and_offline_page(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        token = read_desk_token()
        assert token is not None
        status, body = _request(port, "GET", "/manifest.webmanifest", token=token)
        assert status == 200
        manifest = json.loads(body)
        assert manifest["name"] == "Foil Bridge"
        assert manifest["short_name"] == "Bridge"
        assert manifest["display"] == "standalone"
        assert manifest["start_url"] == f"/?token={quote(token, safe='')}"
        sizes = {icon["sizes"]: icon["src"] for icon in manifest["icons"]}
        assert set(sizes) == {"192x192", "512x512"}
        assert all(f"token={quote(token, safe='')}" in src for src in sizes.values())
        status, icon = _request(port, "GET", "/icon-192.png", token=token)
        assert status == 200 and icon.startswith(b"\x89PNG")
        status, worker = _request(port, "GET", "/sw.js", token=token)
        assert status == 200 and b"fetch" in worker and b"/offline" in worker
        status, page = _request(port, "GET", "/offline", token=token)
        assert status == 200
        assert b"This machine can't be reached. Check Tailscale." in page
        drawn = svg(f"https://{NODE[0]}/pair?code=23456789")
        assert drawn.startswith("<svg") and 'fill="#241c14"' in drawn


def test_events_name_the_path_and_not_the_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOIL_BRIDGE_INTERVAL", "0.05")
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        token = read_desk_token()
        assert token is not None
        root = store_root()
        outside = tmp_path / "outside.txt"
        outside.write_text(OUTSIDE, encoding="utf-8")
        (root / "linked.txt").symlink_to(outside)
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        try:
            target = "/v1/events?token=" + quote(token, safe="")
            sock.sendall(
                f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode()
            )
            folder = root / "projects" / "demo"
            folder.mkdir(parents=True)
            (folder / "note.txt").write_text(SENTINEL, encoding="utf-8")
            (root / "bridge" / "desk").touch()
            sock.settimeout(0.3)
            buf = b""
            deadline = time.time() + 3
            while time.time() < deadline and b"projects/demo/note.txt" not in buf:
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()
        assert b'data: {"id":"projects/demo/note.txt"}' in buf
        assert SENTINEL.encode() not in buf
        assert b"bridge/desk" not in buf
        assert OUTSIDE.encode() not in buf
        status, body = _request(port, "GET", "/v1/items", token=token)
        assert status == 200
        assert b"projects/demo/note.txt" in body
        assert b"linked.txt" not in body
        assert SENTINEL.encode() not in body
        assert OUTSIDE.encode() not in body
        status, body = _request(port, "GET", "/v1/items/projects/demo/note.txt", token=token)
        row = json.loads(body)
        assert status == 200 and set(row) == {"id", "mtime"}
        assert row["id"] == "projects/demo/note.txt"
        status, body = _request(port, "GET", "/v1/items/linked.txt", token=token)
        assert status == 404 and OUTSIDE.encode() not in body


def test_doctor_is_clear_when_the_page_answers_401(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    binary = tmp_path / "bin" / "foil-bridge"
    binary.parent.mkdir()
    binary.write_text("#!/bin/sh\nprintf 'foil-bridge 0.1.0\\n'\n", encoding="utf-8")
    binary.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", str(binary.parent))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        monkeypatch.setenv("FOIL_BRIDGE_PORT", str(port))
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                status, _body = _request(port, "GET", "/")
            except OSError:
                time.sleep(0.02)
                continue
            if status == 401:
                break
        else:
            raise AssertionError("the page did not answer")
        assert main(["bridge", "install"]) == 0
        capsys.readouterr()
        assert main(["doctor"]) == 0
        assert capsys.readouterr().out.splitlines() == [
            "bridge: installed",
            "unit: ok",
            "token: ok",
            "listen: up",
            "tailscale: not installed",
            "master: not started",
            "runners: none",
            "push: off",
        ]


def test_a_large_change_is_one_star() -> None:
    current = {f"files/{index}.txt": 1 for index in range(501)}
    assert diff({}, current) == ["*"]
