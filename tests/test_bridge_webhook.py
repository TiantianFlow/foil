"""A webhook is a wake. The body is needs_you and a count, and the client pulls."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from foil_bridge import mcp, push, webhook
from foil_bridge.server import open_server

from foil import master
from foil.bridge_cmd import bridge_root, read_desk_token
from foil.errors import FoilError
from foil.project import foil_root
from foil.tmux import TmuxController
from tests.test_answer import _question
from tests.test_bridge_home import _key, _master_mail, _short_repo
from tests.test_bridge_http import _origin, _request
from tests.test_bridge_mcp import _message, _post, _result
from tests.test_bridge_push import ASK, CHAT, GOAL, MAIL, WHY

T0 = 1_700_000_000
ROOT = Path(__file__).parents[1]
ASKS = [ASK, WHY, GOAL, "Which reading?", "reading"]


class _Capture(ThreadingHTTPServer):
    rows: list[tuple[str, dict[str, str], bytes]]
    code: int
    location: str


class _Posted(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        headers = {key.lower(): value for key, value in self.headers.items()}
        server: _Capture = self.server  # type: ignore[assignment]
        server.rows.append((self.path, headers, body))
        self.send_response(server.code)
        if server.location:
            self.send_header("Location", server.location)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _endpoint() -> Iterator[_Capture]:
    server = _Capture(("127.0.0.1", 0), _Posted)
    server.rows = []
    server.code = 204
    server.location = ""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _url(server: _Capture, path: str = "/hook") -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args: True)


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _body(server: _Capture, index: int = 0) -> bytes:
    path, headers, body = server.rows[index]
    assert path == "/hook"
    assert headers["user-agent"] == "foil-bridge"
    assert headers["content-type"] == "application/json"
    assert "authorization" not in headers
    return body


def _absent(blob: bytes, phrases: list[str]) -> None:
    text = blob.decode()
    for phrase in phrases:
        assert phrase not in text


def test_a_webhook_is_off_until_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _alive(monkeypatch)
    with _endpoint() as server, _short_repo(monkeypatch) as repo:
        _question(repo)
        assert webhook.view() == {"enabled": False, "urls": []}
        assert webhook.deliver(now=T0) is None and server.rows == []
        webhook.update({"urls": [_url(server)]})
        assert webhook.deliver(now=T0) is None and server.rows == []
        stored = json.loads((bridge_root() / "webhook" / "settings").read_text(encoding="utf-8"))
        assert stored["enabled"] is False


def test_a_question_is_a_count_and_the_body_has_no_fleet_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    with _endpoint() as server, _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        webhook.update({"enabled": True, "urls": [_url(server)]})
        assert push.deliver(now=T0) is None
        shown = webhook.deliver(now=T0)
        assert shown == {"kind": "needs_you", "count": 1}
        raw = _body(server)
        assert raw == b'{"kind":"needs_you","count":1}'
        _absent(raw, [*ASKS, key, "main", "lead"])
        stored = (bridge_root() / "webhook" / "state").read_text(encoding="utf-8")
        _absent(stored.encode(), [ASK, WHY, GOAL])
        assert f"q/{key}/main/reading" in stored
        webhook.deliver(now=T0 + 59)
        assert len(server.rows) == 1
        webhook.deliver(now=T0 + 60)
        assert len(server.rows) == 1
        assert push.settings() == dict.fromkeys(push.KINDS, False)


def test_a_new_event_is_one_count_of_what_is_current(monkeypatch: pytest.MonkeyPatch) -> None:
    _alive(monkeypatch)
    with _endpoint() as server, _short_repo(monkeypatch) as repo:
        _question(repo)
        webhook.update({"enabled": True, "urls": [_url(server)]})
        webhook.deliver(now=T0)
        assert _body(server) == b'{"kind":"needs_you","count":1}'
        status = foil_root(repo) / "fleets" / "main" / "board" / "status.md"
        text = status.read_text(encoding="utf-8")
        extra = (
            "  - id: other\n    ask: Other ask?\n    options: [a]\n    owner: human\n"
        )
        status.write_text(
            text.replace("  - id: theirs", extra + "  - id: theirs"), encoding="utf-8"
        )
        webhook.deliver(now=T0 + 60)
        assert _body(server, 1) == b'{"kind":"needs_you","count":2}'
        _absent(_body(server, 1), ["Other ask?", "Which reading?"])


def test_chat_and_a_running_fleet_send_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    with _endpoint() as server, _short_repo(monkeypatch) as repo:
        from tests.test_bridge_push import _fleet, _mail, _status

        fleet = _fleet(repo)
        _status(fleet / "board", "---\ncontract: status/v1\nstate: working\n---\n")
        _mail(fleet / "board", "user", "20261122T120000Z-master-0123abcd.md", MAIL)
        _master_mail("master", CHAT)
        webhook.update({"enabled": True, "urls": [_url(server)]})
        assert webhook.deliver(now=T0) is None
        assert server.rows == []


def test_one_url_failing_does_not_mark_the_other(monkeypatch: pytest.MonkeyPatch) -> None:
    _alive(monkeypatch)
    with _endpoint() as good, _endpoint() as bad, _short_repo(monkeypatch) as repo:
        bad.code = 500
        _question(repo)
        webhook.update({"enabled": True, "urls": [_url(good), _url(bad)]})
        webhook.deliver(now=T0)
        assert len(good.rows) == 1 and len(bad.rows) == 1
        assert good.rows[0][2] == bad.rows[0][2] == b'{"kind":"needs_you","count":1}'
        webhook.deliver(now=T0 + 60)
        assert len(good.rows) == 1 and len(bad.rows) == 2
        assert bad.rows[1][2] == b'{"kind":"needs_you","count":1}'


def test_a_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    _alive(monkeypatch)
    with _endpoint() as first, _endpoint() as second, _short_repo(monkeypatch) as repo:
        _question(repo)
        first.code = 302
        first.location = _url(second, "/gone")
        webhook.update({"enabled": True, "urls": [_url(first)]})
        assert webhook.deliver(now=T0) is None
        assert len(first.rows) == 1 and second.rows == []
        assert webhook.deliver(now=T0 + 60) is None
        assert len(first.rows) == 2 and second.rows == []


def test_a_bridge_gap_is_a_count_without_turning_push_on() -> None:
    with _endpoint() as server:
        url = _url(server)
        webhook.update({"enabled": True, "urls": [url]})
        assert webhook.deliver() is None and server.rows == []
        stamp = int(time.time()) - 121
        (bridge_root() / "webhook" / "heartbeat").write_text(
            json.dumps({"t": stamp}) + "\n", encoding="utf-8"
        )
        assert webhook.deliver(now=time.time()) == {"kind": "needs_you", "count": 1}
        assert _body(server) == b'{"kind":"needs_you","count":1}'
        state = json.loads((bridge_root() / "webhook" / "state").read_text(encoding="utf-8"))
        assert state["sent"][url] == ["down/bridge"]
        assert state["gap"] is False
        assert push.settings() == dict.fromkeys(push.KINDS, False)
        webhook.deliver(now=time.time() + 60)
        assert len(server.rows) == 1


def test_turning_on_does_not_inherit_a_stale_stamp() -> None:
    folder = bridge_root() / "webhook"
    folder.mkdir(parents=True)
    (folder / "heartbeat").write_text(json.dumps({"t": 1}) + "\n", encoding="utf-8")
    (folder / "state").write_text(
        json.dumps({"sent": {}, "last": -1, "gap": True}) + "\n", encoding="utf-8"
    )
    with _endpoint() as server:
        webhook.update({"enabled": True, "urls": [_url(server)]})
        assert webhook.deliver() is None and server.rows == []
        state = json.loads((folder / "state").read_text(encoding="utf-8"))
        assert state["gap"] is False


def test_the_master_down_is_only_a_count(monkeypatch: pytest.MonkeyPatch) -> None:
    with _endpoint() as server:
        webhook.update({"enabled": True, "urls": [_url(server)]})
        window = master.directory() / "run" / "window.json"
        window.parent.mkdir(parents=True)
        window.write_text(
            json.dumps({"schema_version": 1, "window_id": "@1"}) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(master, "_alive", lambda record: True)
        assert webhook.deliver(now=T0) is None and server.rows == []
        monkeypatch.setattr(master, "_alive", lambda record: False)
        assert webhook.deliver(now=T0) == {"kind": "needs_you", "count": 1}
        assert _body(server) == b'{"kind":"needs_you","count":1}'


def test_a_bad_setting_is_refused_and_a_stored_list_drops_what_is_not_an_endpoint() -> None:
    with _endpoint() as server:
        url = _url(server)
        saved = webhook.update({"enabled": False, "urls": [url]})
        assert saved == {"enabled": False, "urls": [url]}
        path = bridge_root() / "webhook" / "settings"
        before = path.read_text(encoding="utf-8")
        assert _mode(path) == 0o600
        assert _mode(path.parent) == 0o700
        rejected = [
            {},
            {"enabled": "yes"},
            {"on": True},
            {"urls": ["http://10.0.0.1/hook"]},
            {"urls": ["http://user:x@127.0.0.1/hook"]},
            {"urls": [url, url]},
            {"urls": [f"http://127.0.0.1:{port}/h" for port in range(1, 10)]},
            {"urls": "http://127.0.0.1/hook"},
        ]
        for body in rejected:
            with pytest.raises(FoilError, match="webhook setting is invalid"):
                webhook.update(body)
            assert path.read_text(encoding="utf-8") == before
        eight = [f"http://127.0.0.1:{port}/h" for port in range(1, 9)]
        assert webhook.update({"urls": eight})["urls"] == eight
        path.write_text(
            json.dumps({"enabled": True, "urls": [url, "http://10.0.0.1/x", "nope", url]}) + "\n",
            encoding="utf-8",
        )
        assert webhook.settings() == {"enabled": True, "urls": [url]}


def test_a_symlink_is_left_in_place(tmp_path: Path) -> None:
    folder = bridge_root() / "webhook"
    folder.mkdir(parents=True)
    target = tmp_path / "other"
    target.write_text("{}\n", encoding="utf-8")
    path = folder / "settings"
    path.symlink_to(target)
    with pytest.raises(FoilError, match="refusing symlink"):
        webhook.update({"enabled": True})
    assert path.is_symlink()
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_the_route_saves_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    server = open_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        desk = read_desk_token()
        status, body = _request(port, "GET", "/v1/webhook", token=desk)
        assert status == 200 and json.loads(body) == {"enabled": False, "urls": []}
        status, _ = _request(port, "POST", "/v1/webhook", body={"enabled": True})
        assert status == 401
        status, _ = _request(
            port, "POST", "/v1/webhook", token=desk, body={"enabled": True}
        )
        assert status == 403
        status, body = _request(
            port,
            "POST",
            "/v1/webhook",
            token=desk,
            headers=_origin(port),
            body={"enabled": True, "extra": True},
        )
        assert status == 400
        assert json.loads(body)["error"] == "foil: webhook setting is invalid"
        hook = "http://127.0.0.1:9/hook"
        status, body = _request(
            port,
            "POST",
            "/v1/webhook",
            token=desk,
            headers=_origin(port),
            body={"enabled": False, "urls": [hook]},
        )
        assert status == 200 and json.loads(body) == {"enabled": False, "urls": [hook]}
    finally:
        server.stopped.set()
        server.watch.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _local_inbox() -> tuple[str, str]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "foil_bridge", "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines = [
        _message(
            "initialize",
            protocolVersion="2025-11-25",
            capabilities={},
            clientInfo={"name": "local", "version": "0"},
        ),
        _message("notifications/initialized", request_id=None),
        _message("tools/call", 2, name="inbox", arguments={}),
    ]
    payload = "".join(json.dumps(line) + "\n" for line in lines)
    try:
        out, err = proc.communicate(payload, timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    assert proc.returncode == 0, err
    found = [json.loads(line) for line in out.splitlines() if line.strip()]
    assert len(found) == 2, err
    return found[0]["result"]["instructions"], found[1]["result"]["content"][0]["text"]


def test_a_local_and_a_remote_client_pull_what_the_webhook_only_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guide's check: stdio on this machine, POST /mcp with the bearer, then a wake.

    The lead's window is not running, so the wake counts the question and that seat.
    """

    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _endpoint() as hook, _short_repo(monkeypatch) as repo:
        _question(repo)
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            desk = read_desk_token()
            status, body = _request(
                port,
                "POST",
                "/v1/webhook",
                token=desk,
                headers=_origin(port),
                body={"enabled": True, "urls": [_url(hook)]},
            )
            assert status == 200 and json.loads(body)["enabled"] is True
            deadline = time.time() + 8
            while time.time() < deadline and not hook.rows:
                time.sleep(0.05)
            assert hook.rows, "webhook did not arrive"
            raw = _body(hook)
            assert raw == b'{"kind":"needs_you","count":2}'
            _absent(raw, [*ASKS, "lead is dead", _key(repo)])
            instructions, local = _local_inbox()
            assert instructions == mcp.INSTRUCTIONS
            assert "Which reading?" in local
            status, body = _post(
                port,
                _message("tools/call", 2, name="inbox", arguments={}),
                token=desk,
            )
            assert status == 200
            remote = _result(body)["content"][0]["text"]
            assert remote == local
            assert "reading" in remote
        finally:
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def test_the_guide_and_the_skill_say_what_a_client_is() -> None:
    requirements = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    guide = (ROOT / "docs" / "mcp.md").read_text(encoding="utf-8")
    skill = (ROOT / "docs" / "mcp-client.md").read_text(encoding="utf-8")
    assert mcp.INSTRUCTIONS in " ".join(requirements.split())
    assert "foil-bridge" in guide and "`mcp`" in guide
    assert "tailscale serve" in guide
    assert "/mcp" in guide and "/v1/webhook" in guide
    assert "needs_you" in guide
    assert "human's voice" in skill
    assert "not the master" in skill
    assert "talk_to_master" in skill and "needs_you" in skill
    assert "kill" in skill and "archive" in skill
