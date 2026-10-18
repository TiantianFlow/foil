"""`foil view` over a whole fleet lifecycle: real foil, git, tmux, and the fake agent.

The viewer runs as its own process beside a scenario-1 fleet and is read at
each stage: not started, done, crashed, resumed, stopped, and killed.
"""

from __future__ import annotations

import html
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import load_registry
from tests.e2e.test_archive import _peek, _windows
from tests.e2e.test_scenarios import _close, _prepare, _scripts, _status, _wait
from tests.test_view import _get

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
SEATS = ("implementer-1", "lead", "reviewer-1")


def _viewer(repo: Path) -> tuple[subprocess.Popen[str], int]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "foil", "view", "--port", str(port)],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline() == f"http://127.0.0.1:{port}/\n"
    return process, port


def _stop(process: subprocess.Popen[str]) -> None:
    process.send_signal(signal.SIGTERM)
    out, err = process.communicate(timeout=10)
    assert (process.returncode, out, err) == (0, "", "")


def _page(port: int, path: str) -> str:
    status, _fields, body = _get(port, path)
    assert status == 200, (path, status)
    return body.decode()


def _states(port: int) -> dict[str, str]:
    agents = _page(port, "/fleets/main").split("<h2>Agents")[1].split("</section>")[0]
    found = {}
    for name in SEATS:
        if f">{name}</a>" in agents:
            row = agents.split(f">{name}</a>")[1].split("</tr>")[0]
            found[name] = row.split('<span class="dot ')[1].split('"')[0]
    return found


def _attach(port: int, name: str) -> str:
    page = _page(port, f"/fleets/main/seats/{name}")
    return html.unescape(page.split("<code>")[1].split("</code>")[0])


def test_the_viewer_follows_a_fleet_from_start_to_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    _scripts(
        repo,
        "scenario-1",
        {"lead": "lead.json", "implementer-1": "implementer.json", "reviewer-1": "reviewer.json"},
    )
    process, port = _viewer(repo)
    try:
        fleet = _page(port, "/fleets/main")
        assert "No session yet. This fleet has not started." in fleet
        assert "No seats." in fleet

        # Done: the lead spawned, delegated, merged, and reported.
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        _wait(repo, lambda: "state: done" in _status(repo))
        assert main(["post", "a word for every seat"]) == 0
        capsys.readouterr()
        registry = load_registry(repo)
        session = str(registry["tmux_session"])
        assert _states(port) == dict.fromkeys(SEATS, "alive")
        fleet = _page(port, "/fleets/main")
        assert f"<code>tmux attach -t &#x27;={session}&#x27;</code>" in fleet
        assert "foil/implementer-1" in fleet
        board = fleet.split("<h2>Board")[1].split("</section>")[0]
        assert board.index("status.md") < board.index("mail/")
        posts = fleet.split("<h2>Posts")[1].split("</section>")[0]
        assert "1 file" in posts and "room/" in posts
        status = _page(port, "/fleets/main?open=status.md")
        assert "state: done" in status and "- [x] Merge foil/implementer-1" in status
        window = registry["seats"]["implementer-1"]["window_id"]
        assert _attach(port, "implementer-1") == (
            f"tmux attach -t '={session}' \\; select-window -t {window}"
        )
        pane = _page(port, "/fleets/main/seats/implementer-1")
        assert "foil-fake implementer-1 ready" in pane.replace("\n", "")
        assert "foil-fake implementer-1 ready" in _peek(capsys, "implementer-1").replace("\n", "")
        assert "<td>3</td><td>0</td><td>0</td>" in _page(port, "/")

        # Crashed: the session is gone, every seat reads dead, and nothing is attached.
        subprocess.run(["tmux", "kill-session", "-t", session], check=True, capture_output=True)
        assert _states(port) == dict.fromkeys(SEATS, "dead")
        lead = _page(port, "/fleets/main/seats/lead")
        assert "foil: seat &#x27;lead&#x27; is dead" in lead and "select-window" not in lead

        # Resumed: alive again, with the new window ids.
        assert main(["seat", "resume"]) == 0
        _wait(repo, lambda: _states(port) == dict.fromkeys(SEATS, "alive"))
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        assert _attach(port, "lead").endswith(f"select-window -t {window}")

        # Stopping the viewer leaves the windows; a second viewer reads the same fleet.
        _stop(process)
        assert window in _windows(session)
        process, port = _viewer(repo)

        # Killed: every seat reads killed, and peek's error replaces the pane.
        assert main(["seat", "kill", "--all"]) == 0
        assert _states(port) == dict.fromkeys(SEATS, "killed")
        assert "<td>0</td><td>0</td><td>3</td>" in _page(port, "/")
        reviewer = _page(port, "/fleets/main/seats/reviewer-1")
        assert "foil: seat &#x27;reviewer-1&#x27; is killed" in reviewer
        _stop(process)
        process = None
    finally:
        if process is not None:
            process.kill()
        _close(repo)
