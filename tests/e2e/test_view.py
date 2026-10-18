"""`foil view` with real foil, real git, real tmux, and the fake agent."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import load_registry
from tests.e2e.test_archive import _peek, _ready, _windows
from tests.e2e.test_scenarios import _close, _prepare
from tests.test_view import _get

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


def test_the_seat_page_shows_the_peek_and_stopping_leaves_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    process = None
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        _ready(repo, capsys, "lead")
        registry = load_registry(repo)
        session = str(registry["tmux_session"])
        window = registry["seats"]["lead"]["window_id"]
        process = subprocess.Popen(
            [sys.executable, "-m", "foil", "view", "--port", str(port)],
            cwd=repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdout.readline() == f"http://127.0.0.1:{port}/\n"
        status, _fields, body = _get(port, "/fleets/main/seats/lead")
        page = body.decode()
        assert status == 200
        assert f"tmux attach -t ={session} \\; select-window -t {window}</code>" in page
        assert '<span class="dot alive"></span>alive' in page
        assert "foil-fake lead ready" in page.replace("\n", "")
        assert "foil-fake lead ready" in _peek(capsys, "lead").replace("\n", "")
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=10)
        assert (process.returncode, out, err) == (0, "", "")
        process = None
        assert window in _windows(session)
        assert main(["seat", "list"]) == 0
        assert "lead\tlead\talive\t" in capsys.readouterr().out
    finally:
        if process is not None:
            process.kill()
        _close(repo)
