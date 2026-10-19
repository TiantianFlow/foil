"""`foil view` (F37): a read-only page over real sockets, with tmux stood in."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from foil import view
from foil.cli import main
from foil.errors import FoilError
from foil.project import foil_root
from foil.store import SCHEMA_VERSION
from foil.tmux import ProbeResult, ProbeState, TmuxController, TmuxError, TmuxTarget
from tests.test_slice_b import _repo

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def _serving(repo: Path, port: str | None = None) -> Iterator[int]:
    server = view.open_server(repo, port, base=_free_port())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _get(
    port: int,
    path: str = "/",
    *,
    host: str | None = "",
    headers: dict[str, str] | None = None,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    lines = [f"{method} {path} HTTP/1.0"]
    if host is not None:
        lines.append(f"Host: {host or f'127.0.0.1:{port}'}")
    lines += [f"{key}: {value}" for key, value in (headers or {}).items()]
    with socket.create_connection(("127.0.0.1", port), timeout=15) as client:
        client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        data = b""
        while chunk := client.recv(65536):
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    status = int(head.split()[1])
    fields = dict(line.split(": ", 1) for line in head.decode().split("\r\n")[1:] if ": " in line)
    return status, fields, body


def _page(port: int, path: str = "/") -> str:
    status, _fields, body = _get(port, path)
    assert status == 200, (path, status, body[:200])
    return body.decode()


def _seat(name: str, *, template: str = "", state: str = "", window: str = "@1", **extra: str):
    record = {
        "name": name,
        "template": template or ("lead" if name == "lead" else name.rsplit("-", 1)[0]),
        "harness": "claude",
        "model": "",
        "window_id": window,
        "state": state,
        "worktree": "",
        "branch": "",
        "session_id": "",
    }
    record.update(extra)
    return record


def _registry(repo: Path, *seats: dict, session: str = "foil-demo-1", fleet: str = "main") -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "fleet_id": "fleet",
        "tmux_session": session,
        "lead": "lead",
        "seats": {seat["name"]: seat for seat in seats},
    }
    path = foil_root(repo) / "fleets" / fleet / "run" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _windows(
    monkeypatch: pytest.MonkeyPatch, owned: dict[str, str], exited: frozenset[str] = frozenset()
) -> None:
    """Stand in for tmux: ``owned`` maps a window id to the seat it still carries.

    A window in ``exited`` still carries its seat, but its process has exited.
    """

    def probe(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> ProbeResult:
        window = target.window_id or ""
        if owned.get(window) != seat_id:
            return ProbeResult(ProbeState.DEAD, False)
        state = ProbeState.DEAD if window in exited else ProbeState.ALIVE
        return ProbeResult(state, True, target)

    monkeypatch.setattr(TmuxController, "probe", probe)
    monkeypatch.setattr(TmuxController, "capture_pane", lambda self, window, lines: "$ ready\n")


def _board(repo: Path, fleet: str = "main") -> Path:
    return foil_root(repo) / "fleets" / fleet / "board"


# Refusals and ports


def test_view_inside_the_fleet_refuses_before_it_binds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path, monkeypatch)
    capsys.readouterr()

    def bind(*_args: object) -> None:
        raise AssertionError("bound a socket")

    monkeypatch.setattr(socket.socket, "bind", bind)
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(["view"]) == 1
    assert capsys.readouterr().err == "foil: view is outside the fleet only\n"


def test_an_empty_seat_id_is_outside_the_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    called: list[tuple[Path, str | None]] = []
    monkeypatch.setattr("foil.view.serve", lambda root, port: called.append((root, port)) or 0)
    monkeypatch.setenv("FOIL_SEAT_ID", "")
    assert main(["view", "--port", "9999"]) == 0
    assert called == [(repo, "9999")]


def test_the_default_port_moves_past_a_held_one_and_an_explicit_port_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        base = held.getsockname()[1]
        server = view.open_server(repo, None, base=base)
        try:
            assert server.server_address[1] != base
            assert base < server.server_address[1] < base + view.PORT_SPAN
        finally:
            server.server_close()
        with pytest.raises(FoilError, match=f"^foil: port {base} is in use$"):
            view.open_server(repo, str(base))


def test_a_full_range_and_invalid_ports_are_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "_answers", lambda port: True)
    with pytest.raises(FoilError, match="^foil: no free port in 20000-20049$"):
        view.open_server(repo, None, base=20000)
    for value in ("80", "0", "1023", "70000", "abc", "-1", "٣٠٠٠"):
        with pytest.raises(FoilError, match="^foil: invalid port$"):
            view.open_server(repo, value)


def test_a_restart_rebinds_the_same_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    port = str(_free_port())
    with _serving(repo, port) as bound:
        _page(bound)
    with _serving(repo, port) as bound:
        assert str(bound) == port


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT])
def test_the_command_prints_one_line_and_exits_zero_when_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop: signal.Signals
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    env.pop("FOIL_SEAT_ID", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "foil", "view", "--port", str(port)],
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline() == f"http://127.0.0.1:{port}/\n"
        assert "Fleets" in _page(port)
    finally:
        process.send_signal(stop)
        out, err = process.communicate(timeout=10)
    assert process.returncode == 0
    assert (out, err) == ("", "")


# Access rules


def test_only_loopback_hosts_and_matching_origins_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port:
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", "localhost:8000", "[::1]:8000"):
            assert _get(port, host=host)[0] == 200, host
        for host in ("evil.example", f"evil.example:{port}", "127.0.0.1.evil.example", None):
            status, _fields, body = _get(port, host=host)
            assert (status, body) == (403, b""), host
        own = {"Origin": f"http://127.0.0.1:{port}"}
        assert _get(port, headers=own)[0] == 200
        for origin in ("http://evil.example", "null", f"http://localhost:{port}"):
            status, _fields, body = _get(port, headers={"Origin": origin})
            assert (status, body) == (403, b""), origin
        for method in ("POST", "PUT", "DELETE"):
            assert _get(port, method=method)[0::2] == (405, b"")
        status, fields, body = _get(port, method="HEAD")
        assert (status, body) == (200, b"")
        status, fields, body = _get(port)
        assert fields["Cross-Origin-Resource-Policy"] == "same-origin"
        assert fields["X-Content-Type-Options"] == "nosniff"
        assert fields["Cache-Control"] == "no-store"
        assert fields["Referrer-Policy"] == "no-referrer"
        policy = fields["Content-Security-Policy"]
        assert policy.startswith("default-src 'none'") and "frame-ancestors 'none'" in policy
        nonce = policy.split("'nonce-")[1].split("'")[0]
        assert f'<script nonce="{nonce}">'.encode() in body


def test_an_idle_connection_does_not_hold_the_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port, socket.create_connection(("127.0.0.1", port)):
        started = time.monotonic()
        _page(port)
        # Well under the 10 s socket timeout the idle connection would hold.
        assert time.monotonic() - started < 5


def test_a_client_that_drips_headers_is_cut_at_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "DEADLINE", 1)
    with _serving(repo) as port, socket.create_connection(("127.0.0.1", port)) as slow:
        slow.sendall(b"GET / HTTP/1.0\r\n")
        started = time.monotonic()
        with pytest.raises(OSError):
            # One header line every 0.4 s keeps each read under the socket timeout.
            for _ in range(20):
                time.sleep(0.4)
                slow.sendall(b"X-Slow: 1\r\n")
        # The loop would run 8 s, and each read stays under the 10 s socket timeout.
        assert time.monotonic() - started < 6
        slow.settimeout(1)
        try:
            answer = slow.recv(100)
        except OSError:
            answer = b""
        assert answer == b""


def test_past_the_cap_a_request_gets_an_empty_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "SLOTS", 0)
    with _serving(repo) as port:
        assert _get(port)[0::2] == (503, b"")


def test_past_the_connection_cap_a_connection_is_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "CONNECTIONS", 1)
    with _serving(repo) as port, socket.create_connection(("127.0.0.1", port)):
        time.sleep(0.3)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as second:
            second.sendall(f"GET / HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            assert second.recv(100) == b""


# Settings


def test_settings_show_the_three_lists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    folder = foil_root(repo)
    (folder / "mapping.toml").write_text(
        (folder / "mapping.toml").read_text(encoding="utf-8")
        + '\n[[map]]\nrole = "implementer"\nharness = "codex"\nmodel = "gpt-5.5"\n',
        encoding="utf-8",
    )
    (folder / "harnesses" / "secretive.toml").write_text(
        'id = "secretive"\ncommand = ["no-such-cli", "--api-key", "AKIAEXAMPLE", '
        '"--auth-token=abc", "sk-live", "two words", "{prompt}"]\nsession_id = "none"\n'
        'env = ["SECRETIVE_HOME"]\nmodels = []\n',
        encoding="utf-8",
    )
    (folder / "harnesses" / "broken.toml").write_text("id = [", encoding="utf-8")
    with _serving(repo) as port:
        page = _page(port, "/settings")
    assert "You make the change the task asks for" in page
    assert "+ researcher" in page and "no role" in page
    launch = page.split("secretive</span>")[1].split("</tr>")[0]
    hidden = "--api-key [hidden] --auth-token=[hidden] [hidden] &#x27;two words&#x27; {prompt}"
    assert f"no-such-cli {hidden}" in launch
    assert "AKIA" not in page and "sk-live" not in page and "abc" not in launch
    assert "env: SECRETIVE_HOME" in launch and "<td><span class=\"muted\">no</span>" in launch
    assert "broken</span>" in page and "invalid" in page.split("broken</span>")[1].split("</tr>")[0]
    assert "fake" not in page.split("<h2>Harnesses")[1].split("</section>")[0]
    rows = page.split("<h2>Default mapping")[1]
    order = [rows.index(text) for text in ("lead", "implementer", "reviewer", "gpt-5.5")]
    assert order == sorted(order)
    assert "no model" in rows


def test_an_invalid_roles_file_leaves_the_rest_of_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    (foil_root(repo) / "roles.toml").write_text("[[role]\n", encoding="utf-8")
    with _serving(repo) as port:
        page = _page(port, "/settings")
    roles = page.split("<h2>Roles")[1].split("</section>")[0]
    assert 'class="empty err">foil: invalid template</p>' in roles
    assert "claude --model" in page


# Fleet page


def test_overrides_show_the_last_row_per_role_and_the_next_spawn_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
    assert "No overrides. The next spawn uses the default mapping." in page
    (foil_root(repo) / "fleets" / "main" / "overrides.toml").write_text(
        '[[override]]\nrole = "reviewer"\nmodel = "small"\n\n'
        '[[override]]\nrole = "reviewer"\nmodel = "big"\nnote = "harder review"\n\n'
        '[[override]]\nrole = "lead"\nmodel = ""\n',
        encoding="utf-8",
    )
    _registry(repo, _seat("lead"), _seat("reviewer-1", model="fast"))
    _windows(monkeypatch, {})
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
    assert "foil: invalid fleet memory" in page
    assert "reviewer-1" in page
    (foil_root(repo) / "fleets" / "main" / "overrides.toml").write_text(
        '[[override]]\nrole = "reviewer"\nmodel = "small"\n\n'
        '[[override]]\nrole = "reviewer"\nmodel = "big"\nnote = "harder review"\n',
        encoding="utf-8",
    )
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
    overrides = page.split("<h2>Overrides")[1].split("</section>")[0]
    assert "big" in overrides and "small" not in overrides and "harder review" in overrides
    agents = page.split("<h2>Agents")[1].split("</section>")[0]
    assert "next spawn <span class=\"mono\">big</span>" in agents
    reviewer = agents.split("reviewer-1</a>")[1].split("</tr>")[0]
    assert "fast" in reviewer


def test_no_next_spawn_line_when_the_override_matches_the_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    folder = foil_root(repo)
    text = (folder / "mapping.toml").read_text(encoding="utf-8")
    text = re.sub(r'(role = "reviewer"\nharness = "[^"]+")', r'\1\nmodel = "big"', text)
    assert 'model = "big"' in text
    (folder / "mapping.toml").write_text(text, encoding="utf-8")
    (folder / "fleets" / "main" / "overrides.toml").write_text(
        '[[override]]\nrole = "reviewer"\nmodel = "big"\n', encoding="utf-8"
    )
    _registry(repo, _seat("reviewer-1", model="big"))
    _windows(monkeypatch, {})
    with _serving(repo) as port:
        assert "<small>next spawn" not in _page(port, "/fleets/main")


def test_attach_uses_the_exact_session_and_a_verified_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port:
        assert "No session yet. This fleet has not started." in _page(port, "/fleets/main")
    _registry(
        repo,
        _seat("lead", window="@4"),
        _seat("implementer-1", window="@5", state="killed", branch="foil/implementer-1"),
        _seat("reviewer-1", window="@9"),
    )
    _windows(monkeypatch, {"@4": "lead", "@9": "lead"})
    with _serving(repo) as port:
        fleet = _page(port, "/fleets/main")
        lead = _page(port, "/fleets/main/seats/lead")
        killed = _page(port, "/fleets/main/seats/implementer-1")
        reused = _page(port, "/fleets/main/seats/reviewer-1")
    assert "<code>tmux attach -t &#x27;=foil-demo-1&#x27;</code>" in fleet
    assert "tmux attach -t &#x27;=foil-demo-1&#x27; \\; select-window -t @4</code>" in lead
    assert "$ ready" in lead
    for page in (killed, reused):
        assert "<code>tmux attach -t &#x27;=foil-demo-1&#x27;</code>" in page
        assert "select-window" not in page
        assert "$ ready" not in page
    assert "foil: seat &#x27;implementer-1&#x27; is killed" in killed
    assert "foil/implementer-1" in killed
    assert "foil: seat &#x27;reviewer-1&#x27; is dead" in reused


def test_every_seat_is_listed_with_state_and_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(
        repo,
        _seat("lead", window="@1"),
        _seat("implementer-1", window="@2", branch="foil/implementer-1", model="gpt-5.5"),
        _seat("implementer-2", window="@3", state="killed"),
    )
    _windows(monkeypatch, {"@1": "lead"})
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
        fleets = _page(port, "/")
    agents = page.split("<h2>Agents")[1].split("</section>")[0]
    for name, state in (("lead", "alive"), ("implementer-1", "dead"), ("implementer-2", "killed")):
        row = agents.split(f">{name}</a>")[1].split("</tr>")[0]
        assert f'<span class="dot {state}"></span>{state}' in row, name
    assert "foil/implementer-1" in agents and "gpt-5.5" in agents and "no branch" in agents
    assert "<td>1</td><td>1</td><td>1</td>" in fleets


def test_a_tmux_failure_during_peek_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {"@1": "lead"})

    def broken(self, window: str, lines: int) -> str:
        raise TmuxError("tmux capture-pane failed")

    monkeypatch.setattr(TmuxController, "capture_pane", broken)
    with _serving(repo) as port:
        page = _page(port, "/fleets/main/seats/lead?lines=160")
    assert "foil: could not peek seat" in page
    assert "?lines=80" in page


def test_pane_lines_follow_peek_and_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {"@1": "lead"})
    asked: list[int] = []

    def capture(self, window: str, lines: int) -> str:
        asked.append(lines)
        return "<script>alert(1)</script>\n"

    monkeypatch.setattr(TmuxController, "capture_pane", capture)
    with _serving(repo) as port:
        for value in ("", "80", "1280", "1281", "0", "x", "-5"):
            page = _page(port, f"/fleets/main/seats/lead?lines={value}")
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert" not in page
    assert asked == [40, 80, 1280, 40, 40, 40, 40]


def test_one_broken_fleet_leaves_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    broken = foil_root(repo) / "fleets" / "other" / "run"
    broken.mkdir(parents=True)
    (broken / "registry.json").write_text("{not json", encoding="utf-8")
    _windows(monkeypatch, {"@1": "lead"})
    with _serving(repo) as port:
        fleets = _page(port, "/")
        other = _page(port, "/fleets/other")
        assert _get(port, "/fleets/nope")[0] == 404
    assert '<a class="mono" href="/fleets/main">main</a></td><td>1</td>' in fleets
    assert "foil: invalid record" in fleets
    assert "foil: invalid record" in other and "No overrides." in other


def test_an_unexpected_failure_is_an_empty_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)

    def boom(root: Path, target: str) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(view, "_route", boom)
    capfd.readouterr()
    with _serving(repo) as port:
        assert _get(port)[0::2] == (500, b"")
        _get(port, method="BREW")
        with socket.create_connection(("127.0.0.1", port)) as client:
            client.sendall(b"\x16\x03\x01garbage\r\n\r\n")
            client.recv(100)
    assert capfd.readouterr() == ("", "")


# Board and posts


def test_board_and_posts_list_files_and_skip_links_and_dotfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    board = _board(repo)
    status_text = "---\nstate: working\n---\n<script>x</script>\n"
    (board / "status.md").write_text(status_text, encoding="utf-8")
    (board / "tasks" / "t1.md").write_text("brief", encoding="utf-8")
    (board / "notes" / ".scratch.md.123.tmp").write_text("temp", encoding="utf-8")
    (board / "notes" / "<b>odd&x.md").write_text("odd", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("do not show", encoding="utf-8")
    (board / "notes" / "link.md").symlink_to(outside)
    (board / "linked").symlink_to(tmp_path)
    for thread, names in (("t1", ["t1.md", "z-reply.md"]), ("t2", ["t2.md"])):
        (board / "room" / thread).mkdir()
        for name in names:
            (board / "room" / thread / name).write_text(f"{thread} {name}", encoding="utf-8")
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
        opened = _page(port, "/fleets/main?open=" + "notes/%3Cb%3Eodd%26x.md")
        status = _page(port, "/fleets/main?open=status.md")
        for target in ("notes/link.md", "../../project.json", "/etc/passwd", "linked/secret.txt"):
            assert "do not show" not in _page(port, f"/fleets/main?open={target}")
            assert _get(port, f"/fleets/main/raw?open={target}")[0] == 404
    listed = page.split("<h2>Board")[1].split("</section>")[0]
    assert listed.index("status.md") < listed.index("tasks/t1.md")
    assert ".tmp" not in page and "link.md" not in page and "linked" not in page
    assert "&lt;b&gt;odd&amp;x.md" in listed and "<b>odd" not in page
    posts = page.split("<h2>Posts")[1].split("</section>")[0]
    assert "<h3>t1</h3>" in posts and posts.index("<h3>t1</h3>") < posts.index("<h3>t2</h3>")
    assert "room/t1/z-reply.md" in posts and "3 files" in posts
    assert "<pre>odd</pre>" in opened
    assert "&lt;script&gt;x&lt;/script&gt;" in status and "<script>x" not in status


def test_a_swap_after_the_listing_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    board = _board(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("outside", encoding="utf-8")
    (board / "notes" / "a.md").write_text("inside", encoding="utf-8")
    assert view._board_files(repo, "main")
    (board / "notes" / "a.md").unlink()
    (board / "notes" / "a.md").symlink_to(outside / "a.md")
    with pytest.raises(FoilError, match="^foil: cannot read: notes/a.md$"):
        view._open_listed(repo, "main", "notes/a.md")
    (board / "notes" / "a.md").unlink()
    (board / "notes").rename(tmp_path / "notes-moved")
    (board / "notes").symlink_to(outside)
    with pytest.raises(FoilError, match="^foil: cannot read: notes/a.md$"):
        view._open_listed(repo, "main", "notes/a.md")
    (board / "notes").unlink()
    (board / "notes").mkdir()
    os.mkfifo(board / "notes" / "a.md")
    started = time.monotonic()
    with pytest.raises(FoilError, match="^foil: cannot read: notes/a.md$"):
        view._open_listed(repo, "main", "notes/a.md")
    assert time.monotonic() - started < 2
    fleets = foil_root(repo) / "fleets"
    (fleets / "main").rename(tmp_path / "main-moved")
    (fleets / "main").symlink_to(tmp_path / "main-moved")
    with pytest.raises(FoilError):
        view._open_listed(repo, "main", "notes/a.md")


def test_a_large_file_previews_one_mebibyte_and_raw_serves_it_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    payload = (b"0123456789abcdef" * 4096 + b"\n") * 20 + b"<script>x</script>"
    (_board(repo) / "notes" / "big.log").write_bytes(payload)
    with _serving(repo) as port:
        page = _page(port, "/fleets/main?open=notes/big.log")
        status, fields, body = _get(port, "/fleets/main/raw?open=notes/big.log")
    assert "Showing the first 1 MiB of 1.3 MiB." in page
    assert 'href="/fleets/main/raw?open=notes/big.log"' in page
    assert len(page) < len(payload) + 20000
    assert status == 200 and body == payload
    assert fields["Content-Type"] == "text/plain; charset=utf-8"
    assert "sandbox" in fields["Content-Security-Policy"]
    assert fields["X-Content-Type-Options"] == "nosniff"


def test_a_slow_raw_download_is_not_cut_at_the_header_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "DEADLINE", 1)
    payload = os.urandom(8 * 1024 * 1024)
    (_board(repo) / "notes" / "big.bin").write_bytes(payload)
    with _serving(repo) as port, socket.create_connection(("127.0.0.1", port)) as client:
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        request = f"GET /fleets/main/raw?open=notes/big.bin HTTP/1.0\r\nHost: 127.0.0.1:{port}"
        client.sendall(f"{request}\r\n\r\n".encode())
        started, data = time.monotonic(), b""
        while chunk := client.recv(262144):
            data += chunk
            time.sleep(0.05)
    assert time.monotonic() - started > 1
    assert data.partition(b"\r\n\r\n")[2] == payload


# Reads only


def _snapshot(folder: Path) -> dict[str, tuple[int, str]]:
    found = {}
    for path in sorted(folder.rglob("*")):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
        found[str(path.relative_to(folder))] = (info.st_mode, digest)
    return found


def test_crawling_every_page_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    assert main(["memory", "add", "Peek a new seat first."]) == 0
    _registry(repo, _seat("lead", window="@1"), _seat("implementer-1", state="killed"))
    (foil_root(repo) / "fleets" / "main" / "overrides.toml").write_text(
        '[[override]]\nrole = "lead"\nmodel = "big"\n', encoding="utf-8"
    )
    (_board(repo) / "status.md").write_text("state", encoding="utf-8")
    (_board(repo) / "room" / "t1").mkdir()
    (_board(repo) / "room" / "t1" / "t1.md").write_text("post", encoding="utf-8")
    # A fleet folder with nothing in it: a reader that created its board,
    # registry, or lock would leave files here.
    (foil_root(repo) / "fleets" / "fresh").mkdir()
    _windows(monkeypatch, {"@1": "lead"})
    stores = [Path(os.environ["FOIL_HOME"]), repo]
    before = [_snapshot(folder) for folder in stores]
    with _serving(repo) as port:
        pages = ["/", "/settings", "/fleets/main", "/fleets/main/seats/lead",
                 "/fleets/main/seats/implementer-1", "/nope", "/fleets/fresh",
                 "/fleets/fresh/seats/lead", "/fleets/fresh?open=status.md",
                 "/fleets/fresh/raw?open=status.md"]
        for path in pages:
            _get(port, path)
        listed = re.findall(r'href="/fleets/main\?open=([^"]+)"', _page(port, "/fleets/main"))
        assert len(listed) == 2
        for opened in listed:
            assert _get(port, f"/fleets/main?open={opened}")[0] == 200
            assert _get(port, f"/fleets/main/raw?open={opened}")[0] == 200
            assert _get(port, f"/fleets/main/raw?open={opened}", method="HEAD")[0] == 200
        assert "Peek a new seat first." in _page(port, "/")
        assert "No seats." in _page(port, "/fleets/fresh")
    assert [_snapshot(folder) for folder in stores] == before


def test_the_attach_command_parses_the_same_in_bash_and_zsh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@4"), session="foil-demo-1")
    _windows(monkeypatch, {"@4": "lead"})
    with _serving(repo) as port:
        page = _page(port, "/fleets/main/seats/lead")
    command = html.unescape(page.split("<code>")[1].split("</code>")[0])
    expected = "tmux\nattach\n-t\n=foil-demo-1\n;\nselect-window\n-t\n@4\n"
    shells = [shell for shell in ("bash", "zsh") if shutil.which(shell)]
    assert "bash" in shells
    for shell in shells:
        script = f'set -- {command}; printf "%s\\n" "$@"'
        result = subprocess.run([shell, "-c", script], capture_output=True, text=True)
        assert (result.stdout, result.stderr) == (expected, ""), shell


@pytest.mark.parametrize(
    ("command", "shown"),
    [
        (["env", "OPENAI_API_KEY=sk-abc", "codex"], "env [hidden] codex"),
        (["env", "MY_SECRET=hunter2", "codex"], "env MY_SECRET=[hidden] codex"),
        (["cli", "-H", "Authorization: Bearer abc123"], "cli -H [hidden]"),
        (["cli", "--header=Authorization: token abc123"], "cli [hidden]"),
        (["cli", "--token", "abc123", "--token=abc123"], "cli --token [hidden] --token=[hidden]"),
        (
            ["env", "GH_TOKEN=abc", "HF_TOKEN=hf_abc", "x"],
            "env GH_TOKEN=[hidden] HF_TOKEN=[hidden] x",
        ),
        (["cli", "--key", "abc", "--path", "/x"], "cli --key [hidden] --path /x"),
        (["git", "clone", "https://user:hunter2@host/x"], "git clone https://[hidden]@host/x"),
        (["sh", "-c", "ANTHROPIC_API_KEY=xyz claude"], "sh -c [hidden]"),
        (
            ["cli", "--password", "pw", "--model", "{model}"],
            "cli --password [hidden] --model {model}",
        ),
        (["cli", "a\tb", ""], "cli 'a\tb' ''"),
    ],
)
def test_launch_argv_hides_credentials_and_keeps_token_boundaries(
    command: list[str], shown: str
) -> None:
    assert view._argv(command) == shown


def test_a_file_name_that_is_not_utf8_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {"@1": "lead"})
    notes = os.fsencode(_board(repo) / "notes")
    with open(os.path.join(notes, b"bad\xff.md"), "wb") as handle:
        handle.write(b"x")
    (_board(repo) / "notes" / "good.md").write_text("ok", encoding="utf-8")
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
    assert "notes/good.md" in page and "notes/bad" not in page
    assert '<span class="dot alive"></span>alive' in page


def test_a_dead_seat_whose_window_remains_shows_its_pane_and_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@4"))
    _windows(monkeypatch, {"@4": "lead"}, exited=frozenset({"@4"}))
    with _serving(repo) as port:
        page = _page(port, "/fleets/main/seats/lead")
    assert '<span class="dot dead"></span>dead' in page
    assert "\\; select-window -t @4</code>" in page
    assert "$ ready" in page


def test_an_unknown_seat_is_a_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {})
    with _serving(repo) as port:
        for name in ("nobody", "..", "a%20b"):
            status, _fields, body = _get(port, f"/fleets/main/seats/{name}")
            assert status == 404, name
            assert b"Back to fleets" in body


def test_a_bind_error_other_than_in_use_is_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)

    def refuse(*_args: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(view, "_Server", refuse)
    monkeypatch.setattr(view, "_answers", lambda port: False)
    with pytest.raises(FoilError, match="^foil: could not listen on 127.0.0.1:20000$"):
        view.open_server(repo, None, base=20000)


def test_a_board_that_cannot_be_listed_is_one_line_in_each_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {"@1": "lead"})

    def broken(root: Path, fleet: str) -> list:
        raise FoilError("foil: refusing symlink")

    monkeypatch.setattr(view, "_board_files", broken)
    with _serving(repo) as port:
        page = _page(port, "/fleets/main")
    assert page.count("foil: refusing symlink") == 2
    assert '<span class="dot alive"></span>alive' in page


# Tailnet


NODE = ("box.tail1234.ts.net", "owner@github")


def test_the_tailnet_name_loads_only_for_the_owner_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "tailnet", lambda: NODE)
    owner = {"Tailscale-User-Login": NODE[1]}
    with _serving(repo) as port:
        for host in (NODE[0], f"{NODE[0]}:8443", NODE[0].upper()):
            assert _get(port, host=host, headers=owner)[0] == 200, host
        for origin in (f"https://{NODE[0]}", f"http://{NODE[0]}"):
            assert _get(port, host=NODE[0], headers={**owner, "Origin": origin})[0] == 200
        refused = [
            {},
            {"Tailscale-User-Login": "other@github"},
            {"Tailscale-User-Login": NODE[1].upper()},
            {**owner, "Origin": "https://evil.example"},
            {**owner, "Origin": "null"},
            {"Tailscale-Funnel-Request": "?1"},
        ]
        for headers in refused:
            status, _fields, body = _get(port, host=NODE[0], headers=headers)
            assert (status, body) == (403, b""), headers
        # Two values cannot be told apart from a forwarded one plus a forged one.
        twice = f"GET / HTTP/1.0\r\nHost: {NODE[0]}\r\nTailscale-User-Login: {NODE[1]}\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(f"{twice}Tailscale-User-Login: {NODE[1]}\r\n\r\n".encode())
            assert client.recv(100).split()[1] == b"403"
        # Another name on the tailnet, and loopback rules are unchanged.
        assert _get(port, host="other.tail1234.ts.net", headers=owner)[0] == 403
        assert _get(port)[0] == 200


def test_without_tailscale_the_tailnet_name_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "tailnet", lambda: None)
    with _serving(repo) as port:
        status, _fields, body = _get(port, host=NODE[0], headers={"Tailscale-User-Login": NODE[1]})
    assert (status, body) == (403, b"")


def _status(**changes: object) -> dict:
    status: dict = {
        "BackendState": "Running",
        "Self": {"DNSName": "Box.tail1234.ts.net.", "UserID": 42},
        "User": {"42": {"LoginName": "owner@github"}},
    }
    status.update(changes)
    return status


@pytest.mark.parametrize(
    ("output", "code", "expected"),
    [
        (json.dumps(_status()), 0, ("box.tail1234.ts.net", "owner@github")),
        (json.dumps(_status(BackendState="Stopped")), 0, None),
        (json.dumps(_status(Self={"DNSName": "box.ts.net.", "UserID": 42, "Tags": ["tag:x"]})),
         0, None),
        (json.dumps(_status(User={"42": {"LoginName": "ownér@github"}})), 0, None),
        (json.dumps(_status(User={})), 0, None),
        (json.dumps(_status()), 1, None),
        ("not json", 0, None),
    ],
)
def test_tailnet_reads_the_running_untagged_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, code: int, expected: object
) -> None:
    folder = tmp_path / "bin"
    folder.mkdir()
    (folder / "status.json").write_text(output, encoding="utf-8")
    script = folder / "tailscale"
    script.write_text(
        '#!/bin/sh\n[ "$*" = "status --json" ] || exit 9\n'
        f'cat "{folder}/status.json"\nexit {code}\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    assert view.tailnet() == expected


def test_no_tailscale_on_path_is_no_tailnet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert view.tailnet() is None


# Caps on one request


def test_a_file_over_the_raw_cap_has_no_raw_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(view, "RAW_CAP", 1024)
    (_board(repo) / "notes" / "small.log").write_bytes(b"x" * 1024)
    (_board(repo) / "notes" / "big.log").write_bytes(b"x" * 1025)
    with _serving(repo) as port:
        page = _page(port, "/fleets/main?open=notes/big.log")
        status, _fields, body = _get(port, "/fleets/main/raw?open=notes/big.log")
        small = _get(port, "/fleets/main/raw?open=notes/small.log")
    assert "/raw?open=" not in page and "No raw view over 1.0 KiB." in page
    assert status == 413 and b"x" * 1025 not in body
    assert b"the raw view stops at 1.0 KiB; this file is 1.0 KiB" in body
    assert small[0] == 200 and small[2] == b"x" * 1024


def _hung_tmux(tmp_path: Path) -> Path:
    script = tmp_path / "hung-tmux"
    script.write_text(f'#!/bin/sh\necho $$ >> "{tmp_path}/hung.pids"\nexec sleep 30\n')
    script.chmod(0o755)
    return script


def test_a_tmux_call_past_the_budget_is_stopped(tmp_path: Path) -> None:
    from foil.tmux import time_budget

    controller = TmuxController(str(_hung_tmux(tmp_path)))
    started = time.monotonic()
    with time_budget(0.5), pytest.raises(TmuxError):
        controller._run(["has-session"])
    with time_budget(0.5), pytest.raises(TmuxError):
        time.sleep(0.6)
        controller._run(["has-session"])
    assert time.monotonic() - started < 3


def test_a_hung_tmux_does_not_hold_a_build_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"), _seat("implementer-1", window="@2"))
    hung = TmuxController(str(_hung_tmux(tmp_path)))

    def probe(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> ProbeResult:
        hung._run(["list-windows"])
        raise AssertionError("unreachable")

    monkeypatch.setattr(TmuxController, "probe", probe)
    monkeypatch.setattr(view, "DEADLINE", 1)
    monkeypatch.setattr(view, "SLOTS", 1)
    with _serving(repo) as port:
        started = time.monotonic()
        # Each page probes two seats; the second probe gets no time left.
        first = _get(port, "/fleets/main")
        second = _get(port, "/fleets/main")
    assert time.monotonic() - started < 5
    assert first[0] != 503 and second[0] != 503
    pids = (tmp_path / "hung.pids").read_text().split()
    assert len(pids) == 2
    for pid in pids:
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid), 0)


# Info text


def _requirement_tips() -> dict[str, str]:
    text = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8")
    table = text.split("The info explanations F38 names")[1].split("\n\n")[1]
    rows = [line.strip("|").split(" | ", 1) for line in table.splitlines()[2:]]
    return {name.strip(): body.strip() for name, body in rows}


def test_the_info_text_the_page_serves_is_the_text_f38_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _requirement_tips() == view.TIPS
    f38 = (ROOT / "docs" / "requirements.md").read_text(encoding="utf-8").split("| F38 |")[1]
    assert f"`{view.HELP}`" in f38.split("\n")[0]
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@1"))
    _windows(monkeypatch, {"@1": "lead"})
    with _serving(repo) as port:
        served = "".join(
            _page(port, path)
            for path in ("/", "/settings", "/fleets/main", "/fleets/main/seats/lead")
        )
    found = {html.unescape(item) for item in re.findall(r'</summary><p>(.*?)</p>', served)}
    assert found == set(view.TIPS.values())
    assert html.escape(view.HELP) in served
