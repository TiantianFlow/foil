"""The desk terminal: a PTY in a worktree Foil owns, never a seat's pane."""

from __future__ import annotations

import os
import shlex
import socket
import subprocess
import threading
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from foil_bridge import terminal
from foil_bridge.server import open_server
from foil_bridge.terminal import accept_key, attach_command, owned_worktree
from playwright.sync_api import Browser, expect, sync_playwright

from foil.bridge_cmd import read_desk_token
from foil.errors import FoilError
from foil.lifecycle import TmuxController
from foil.project import foil_root
from foil.tmux import ProbeResult, ProbeState
from tests.test_bridge_evidence import _alive, _registry, _seat
from tests.test_bridge_home import _key, _short_repo
from tests.test_bridge_http import _request, _serving

_GUID_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
_GUID_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


@pytest.fixture(autouse=True)
def _reset_slots():
    _wait_slot()
    terminal._OPEN = 0
    yield
    _wait_slot()


def test_accept_key_matches_rfc6455() -> None:
    assert accept_key(_GUID_KEY) == _GUID_ACCEPT


def test_attach_command_is_text() -> None:
    assert attach_command("s") == "tmux attach -t '=s'"
    assert attach_command("s", "@4") == "tmux attach -t '=s' \\; select-window -t @4"
    assert attach_command("s", "pane") == "tmux attach -t '=s'"
    assert attach_command("") == ""
    assert attach_command("bad session") == ""


def test_upgrade_without_a_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        status, _headers, body = _handshake(port, "", "/tmp", upgrade=True)
        assert status == 401 and body == b""


def test_a_path_outside_foil_worktrees_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SHELL", "/bin/true")
    monkeypatch.setattr(TmuxController, "matches_window", _no_tmux)
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        stray = owned.parent / "stray"
        stray.mkdir()
        link = owned.parent / "linked"
        link.symlink_to(owned, target_is_directory=True)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = _token()
            for path in (repo, tmp_path, stray, link, owned / ".." / ".."):
                status, _headers, body = _handshake(port, token, str(path))
                assert status == 403 and body == b"", path
            status, _headers, body = _handshake(port, token, "relative/path")
            assert status == 403 and body == b""
            status, _headers, body = _handshake(port, token, str(owned), origin=False)
            assert status == 403 and body == b""
            status, _headers, body = _handshake(port, token, str(owned), key="YQ==")
            assert status == 400 and body == b""
            status, _headers, body = _handshake(port, token, str(owned), version="12")
            assert status == 400 and body == b""
            status, headers, _rest = _handshake(port, token, str(owned))
            assert status == 101
            assert headers["sec-websocket-accept"] == accept_key(_GUID_KEY)


def test_a_symlink_on_the_store_chain_is_not_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        assert owned_worktree(str(owned)) == owned
        seats = owned.parent
        moved = seats.with_name("worktrees-real")
        seats.rename(moved)
        seats.symlink_to(moved, target_is_directory=True)
        assert owned_worktree(str(moved / owned.name)) is None
        seats.unlink()
        moved.rename(seats)
        checkout = _seat("implementer-1", "")
        checkout["worktree"] = str(repo.resolve())
        _registry(repo, "main", {"implementer-1": checkout})
        assert owned_worktree(str(repo.resolve())) is None


def test_the_shell_runs_in_the_worktree_and_not_in_tmux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script, report = _interactive_shell(tmp_path)
    monkeypatch.setenv("SHELL", str(script))
    opened: list[list[str]] = []
    real = subprocess.Popen

    def _popen(argv, *args, **kwargs):
        command = list(argv)
        if command != [str(script)]:
            return real(argv, *args, **kwargs)
        assert Path(command[0]).name != "tmux"
        env = kwargs["env"]
        assert "FOIL_SEAT_ID" not in env
        assert "TMUX" not in env
        assert "TMUX_PANE" not in env
        assert env["TERM"] == "dumb"
        assert env["PAGER"] == "cat"
        assert env["GIT_PAGER"] == "cat"
        opened.append(command)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr("foil_bridge.terminal.subprocess.Popen", _popen)
    monkeypatch.setattr(subprocess, "run", _refuse_tmux(subprocess.run))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        monkeypatch.setenv("FOIL_SEAT_ID", "seat-9")
        monkeypatch.setenv("TMUX", "/tmp/tmux-0")
        monkeypatch.setenv("TMUX_PANE", "%1")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            client = _open(port, _token(), owned)
            output = _until(client, b"ready ")
            assert str(owned).encode() in output
            facts = _facts(report)
            assert facts["pwd"] == str(owned)
            assert facts["seat"] == ""
            assert facts["tmux"] == ""
            assert facts["term"] == "dumb"
            assert facts["pager"] == "cat"
            assert facts["gitpager"] == "cat"
            pid = int(facts["pid"])
            child = int(facts["child"])
            assert opened == [[str(script)]]
            assert _cwd(pid) == str(owned)
            names = _names(pid)
            assert "FOIL_SEAT_ID" not in names
            assert "TMUX" not in names
            assert "TMUX_PANE" not in names
            client.send(b"hello\r")
            assert b"got hello" in _until(client, b"got hello")
            client.close()
            _gone(pid)
            _gone(child)


def test_a_shell_that_exits_does_not_leave_its_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "exit-shell"
    report = tmp_path / "exit-report"
    script.write_text(
        "#!/bin/sh\n"
        "sleep 30 &\n"
        f"printf '%s\\n' \"$!\" > {shlex.quote(str(report))}\n"
        "printf 'ready\\n'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            _until(client, b"ready")
            child = int(report.read_text(encoding="utf-8").strip())
            _gone(child)
            client.close()


def test_a_paste_larger_than_the_pty_buffer_arrives_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "count-shell"
    script.write_text(
        "#!/bin/sh\n"
        "n=0\n"
        "while IFS= read -r line; do\n"
        "  n=$((n + 1))\n"
        "  if [ \"$line\" = END ]; then\n"
        "    printf 'count:%s\\n' \"$n\"\n"
        "    break\n"
        "  fi\n"
        "done\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))
    lines = 3000
    payload = b"abcdefghij\r" * lines + b"END\r"
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            client.send(payload)
            output = _until(client, f"count:{lines + 1}".encode(), timeout=8)
            assert f"count:{lines + 1}".encode() in output
            client.close()


def test_a_full_pty_still_ends_when_the_socket_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script, report = _sleeper(tmp_path)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            pid = _reported(report)
            client.sock.settimeout(2)
            for _ in range(500):
                client.send(b"x" * 200 + b"\r")
            client.close()
            _gone(pid)


def test_stopping_the_bridge_ends_the_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script, report = _sleeper(tmp_path)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            pid = _reported(report)
            server.shutdown()
            _gone(pid)
            client.close()


def test_interrupt_reaches_the_foreground_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "sleep-shell"
    report = tmp_path / "sleep-report"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$$\" > {shlex.quote(str(report))}\n"
        "printf 'ready\\n'\n"
        "sleep 30\n"
        "printf 'finished\\n'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            _until(client, b"ready")
            pid = _reported(report)
            client.send(b"\x03")
            _gone(pid)
            client.close()


def test_a_swapped_symlink_does_not_move_the_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script, report = _interactive_shell(tmp_path)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        moved = owned.with_name("implementer-1-moved")
        elsewhere = owned.parent / "elsewhere"
        elsewhere.mkdir()
        real_open = os.open
        swapped = False

        def _swap_open(path, flags, *args, **kwargs):
            nonlocal swapped
            fd = real_open(path, flags, *args, **kwargs)
            if not swapped and Path(path) == owned and flags & os.O_DIRECTORY:
                swapped = True
                owned.rename(moved)
                owned.symlink_to(elsewhere, target_is_directory=True)
            return fd

        monkeypatch.setattr("foil_bridge.terminal.os.open", _swap_open)
        with _serving(monkeypatch) as server:
            client = _open(server.server_address[1], _token(), owned)
            _until(client, b"ready ")
            assert _facts(report)["pwd"] == str(moved)
            client.close()


def test_the_ninth_terminal_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(terminal, "LIMIT", 1)
    script = tmp_path / "sleep-shell"
    script.write_text("#!/bin/sh\nexec sleep 60\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = _token()
            first = _open(port, token, owned)
            status, _headers, body = _handshake(port, token, str(owned))
            assert status == 403 and body == b""
            first.close()
            _wait_slot()
            again = _open(port, token, owned)
            again.close()


def test_the_page_shows_the_path_and_the_attach_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _alive(monkeypatch)
    monkeypatch.setattr(
        "foil_bridge.transcript.peek_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FoilError("foil: no transcript")),
    )
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo, window="@4")
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = _token()
            status, body = _page(port, token, f"projects/{key}/fleets/main")
            assert status == 200
            fleet = unescape(body.decode())
            assert "tmux attach -t '=s'" in fleet
            assert "select-window" not in fleet
            assert "Copy attach command" in fleet
            status, body = _page(port, token, f"projects/{key}/fleets/main/seats/implementer-1")
            assert status == 200
            text = unescape(body.decode())
            assert str(owned) in text
            assert "Open terminal" in text
            assert "Nothing is typed into the seat." in text
            assert "tmux attach -t '=s' \\; select-window -t @4" in text
            quiet = _seat("quiet", "")
            _registry(
                repo,
                "main",
                {"implementer-1": _recorded(owned, "@4"), "quiet": quiet},
            )
            status, body = _page(port, token, f"projects/{key}/fleets/main/seats/quiet")
            assert status == 200
            quiet_page = unescape(body.decode())
            assert "This seat has no worktree Foil owns." in quiet_page
            assert "Open terminal" not in quiet_page
            assert "tmux attach -t '=s'" in quiet_page
            assert "select-window" not in quiet_page


def test_select_window_stays_off_when_the_window_is_not_marked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *_a, **_k: False)
    monkeypatch.setattr(
        TmuxController,
        "probe",
        lambda *_a, **_k: ProbeResult(ProbeState.DEAD, False),
    )
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo, window="@4")
        key = _key(repo)
        with _serving(monkeypatch) as server:
            status, body = _page(
                server.server_address[1],
                _token(),
                f"projects/{key}/fleets/main/seats/implementer-1",
            )
            assert status == 200
            text = unescape(body.decode())
            assert "tmux attach -t '=s'" in text
            assert "select-window" not in text
            assert str(owned) in text


def test_a_missing_session_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _alive(monkeypatch)
    with _short_repo(monkeypatch) as repo:
        _registry(repo, "main", {"lead": _seat("lead", "")})
        directory = foil_root(repo) / "fleets" / "main" / "run"
        path = directory / "registry.json"
        path.write_text(path.read_text(encoding="utf-8").replace('"s"', '""'), encoding="utf-8")
        key = _key(repo)
        with _serving(monkeypatch) as server:
            status, body = _page(server.server_address[1], _token(), f"projects/{key}/fleets/main")
            assert status == 200
            assert b"No session yet." in body
            assert b"Copy attach command" not in body


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def test_the_terminal_sits_beside_the_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, browser: Browser
) -> None:
    _alive(monkeypatch)
    monkeypatch.setattr(
        "foil_bridge.transcript.peek_text",
        lambda *_args, **_kwargs: "hello from the seat\n",
    )
    script, report = _interactive_shell(tmp_path)
    monkeypatch.setenv("SHELL", str(script))
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        owned = _worktree(repo, window="@4")
        key = _key(repo)
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = _token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            target = f"projects/{key}/fleets/main/seats/implementer-1"
            page.goto(f"{origin}/evidence?{urlencode({'target': target, 'token': token})}")
            talk = page.locator(".talk").bounding_box()
            term = page.locator("#term").bounding_box()
            assert talk is not None and term is not None
            assert term["x"] >= talk["x"] + talk["width"] - 2
            page.set_viewport_size({"width": 390, "height": 800})
            talk = page.locator(".talk").bounding_box()
            term = page.locator("#term").bounding_box()
            assert talk is not None and term is not None
            assert term["y"] >= talk["y"] + talk["height"] - 2
            page.set_viewport_size({"width": 1280, "height": 800})
            expect(page.locator("#term-path")).to_contain_text(str(owned))
            page.get_by_role("button", name="Copy attach command").click()
            expect(page.get_by_role("button", name="Copied")).to_be_visible()
            copied = page.evaluate("navigator.clipboard.readText()")
            assert copied == "tmux attach -t '=s' \\; select-window -t @4"
            page.get_by_role("button", name="Open terminal").click()
            screen = page.locator("#term-screen")
            expect(screen).to_be_visible()
            expect(screen).to_contain_text(str(owned))
            screen.click()
            page.keyboard.type("hello")
            page.keyboard.press("Enter")
            expect(screen).to_contain_text("got hello")
            facts = _facts(report)
            page.get_by_role("button", name="Close").click()
            expect(screen).to_be_hidden()
            _gone(int(facts["pid"]))
            _gone(int(facts["child"]))
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def _token() -> str:
    token = read_desk_token()
    assert token is not None
    return token


def _worktree(repo: Path, *, window: str = "") -> Path:
    path = foil_root(repo) / "worktrees" / "implementer-1"
    path.mkdir(parents=True)
    _registry(repo, "main", {"implementer-1": _recorded(path.resolve(), window)})
    return path.resolve()


def _recorded(path: Path, window: str) -> dict[str, str]:
    seat = _seat("implementer-1", "")
    seat["worktree"] = str(path)
    seat["window_id"] = window
    return seat


def _sleeper(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "sleep-shell"
    report = tmp_path / "sleep-report"
    script.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$$\" > {shlex.quote(str(report))}\n"
        "exec sleep 60\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, report


def _reported(path: Path, timeout: float = 3) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return int(path.read_text(encoding="utf-8").strip())
        time.sleep(0.05)
    raise AssertionError("no pid")


def _interactive_shell(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "shell"
    report = tmp_path / "report"
    script.write_text(
        "#!/bin/sh\n"
        "sleep 30 &\n"
        "child=$!\n"
        "printf 'pid:%s\\npwd:%s\\nseat:%s\\ntmux:%s\\nterm:%s\\n"
        "pager:%s\\ngitpager:%s\\nchild:%s\\n' "
        '"$$" "$PWD" "$FOIL_SEAT_ID" "$TMUX" "$TERM" "$PAGER" "$GIT_PAGER" "$child" > '
        f"{shlex.quote(str(report))}\n"
        "printf 'ready %s\\n' \"$PWD\"\n"
        "IFS= read -r line\n"
        "printf 'got %s\\n' \"$line\"\n"
        "while :; do sleep 1; done\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, report


def _facts(path: Path) -> dict[str, str]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split(":", 1)
        rows[key] = value
    return rows


def _handshake(
    port: int,
    token: str,
    path: str,
    *,
    upgrade: bool = True,
    origin: bool = True,
    key: str = _GUID_KEY,
    version: str = "13",
) -> tuple[int, dict[str, str], bytes]:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        query = urlencode({"token": token, "path": path})
        lines = [f"GET /v1/terminal?{query} HTTP/1.1", f"Host: 127.0.0.1:{port}"]
        if upgrade:
            lines.extend(
                [
                    "Upgrade: websocket",
                    "Connection: Upgrade",
                    f"Sec-WebSocket-Key: {key}",
                    f"Sec-WebSocket-Version: {version}",
                ]
            )
        if origin:
            lines.append(f"Origin: http://127.0.0.1:{port}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        return _read_response(sock)
    finally:
        sock.close()


class _Client:
    def __init__(self, sock: socket.socket, rest: bytes) -> None:
        self.sock = sock
        self.buf = bytearray(rest)

    def send(self, payload: bytes, opcode: int = 2) -> None:
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        else:
            header = bytes((0x80 | opcode, 0x80 | 126)) + length.to_bytes(2, "big")
        self.sock.sendall(header + mask + masked)

    def frame(self) -> tuple[int, bytes] | None:
        if not self._fill(2):
            return None
        first, second = self.buf[0], self.buf[1]
        offset = 2
        length = second & 0x7F
        if length == 126:
            if not self._fill(4):
                return None
            length = int.from_bytes(self.buf[2:4], "big")
            offset = 4
        if not self._fill(offset + length):
            return None
        payload = bytes(self.buf[offset : offset + length])
        del self.buf[: offset + length]
        return first & 0x0F, payload

    def close(self) -> None:
        self.sock.close()

    def _fill(self, count: int) -> bool:
        while len(self.buf) < count:
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                return False
            if not chunk:
                return False
            self.buf += chunk
        return True


def _open(port: int, token: str, path: Path) -> _Client:
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    query = urlencode({"token": token, "path": str(path)})
    message = (
        f"GET /v1/terminal?{query} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {_GUID_KEY}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Origin: http://127.0.0.1:{port}\r\n"
        "\r\n"
    )
    sock.sendall(message.encode())
    status, headers, rest = _read_response(sock)
    assert status == 101, status
    assert headers["sec-websocket-accept"] == accept_key(_GUID_KEY)
    return _Client(sock, rest)


def _read_response(sock: socket.socket) -> tuple[int, dict[str, str], bytes]:
    data = bytearray()
    sock.settimeout(5)
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    head, _, rest = bytes(data).partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    status = int(lines[0].split()[1])
    headers = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    if status == 101:
        return status, headers, rest
    length = int(headers.get("content-length", "0"))
    while len(rest) < length:
        chunk = sock.recv(length - len(rest))
        if not chunk:
            break
        rest += chunk
    return status, headers, rest[:length]


def _until(client: _Client, needle: bytes, timeout: float = 3) -> bytes:
    data = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.sock.settimeout(max(0.1, deadline - time.monotonic()))
        frame = client.frame()
        if frame is None:
            break
        opcode, payload = frame
        if opcode in {1, 2}:
            data += payload
            if needle in data:
                return bytes(data)
        if opcode == 8:
            break
    raise AssertionError(bytes(data))


def _page(port: int, token: str, target: str) -> tuple[int, bytes]:
    return _request(port, "GET", "/evidence?target=" + quote(target, safe="/"), token=token)


def _cwd(pid: int) -> str:
    return os.readlink(f"/proc/{pid}/cwd")


def _names(pid: int) -> set[str]:
    raw = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    return {part.split(b"=", 1)[0].decode() for part in raw if part}


def _gone(pid: int, timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    raise AssertionError(pid)


def _wait_slot(timeout: float = 3) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if terminal._OPEN == 0:
            return
        time.sleep(0.05)
    raise AssertionError(terminal._OPEN)


def _refuse_tmux(real):
    def _run(argv, *args, **kwargs):
        command = argv if isinstance(argv, (list, tuple)) else [str(argv)]
        if command and Path(str(command[0])).name == "tmux":
            raise AssertionError("tmux was used")
        return real(argv, *args, **kwargs)

    return _run


def _no_tmux(*_args, **_kwargs):
    raise AssertionError("tmux was used")
