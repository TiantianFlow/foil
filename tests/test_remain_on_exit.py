"""A failed turn and a dead runner both leave a pane peek can read."""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root, store_root
from foil.store import load_registry
from tests.test_init import _init_git_repository

_STUB = """#!/usr/bin/env python3
import subprocess
import sys
import time

deadline = time.time() + 5
while time.time() < deadline:
    result = subprocess.run(
        ["tmux", "show-options", "-wv", "remain-on-exit"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stdout.strip() == "on":
        break
    time.sleep(0.05)
print("stub harness failed", file=sys.stderr)
raise SystemExit(1)
"""


def _windows(tmux: str, socket: str) -> list[str]:
    result = subprocess.run(
        [tmux, "-L", socket, "list-windows", "-a", "-F", "#{window_id}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _install_socket_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, socket: str) -> str:
    real = subprocess.run(
        ["sh", "-c", "command -v tmux"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    wrapper = tmp_path / "bin" / "tmux"
    wrapper.parent.mkdir()
    wrapper.write_text(
        f'#!/bin/sh\nexec {real} -L {socket} "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrapper.parent}{os.pathsep}{os.environ['PATH']}")
    return real


def _until(check: Callable[[], bool], seconds: float = 10) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()


def _peek(capsys: pytest.CaptureFixture[str]) -> str:
    main(["seat", "peek", "lead", "--lines", "200"])
    return capsys.readouterr().out


def _listed(capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["seat", "list"]) == 0
    return capsys.readouterr().out


def test_a_failed_turn_and_a_dead_runner_stay_peekable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    socket = f"foilt2{uuid.uuid4().hex[:8]}"
    real = _install_socket_tmux(tmp_path, monkeypatch, socket)
    stub = tmp_path / "stub-harness"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    monkeypatch.chdir(repo)
    try:
        assert main(["init"]) == 0
        capsys.readouterr()
        harness = foil_root(repo) / "harnesses" / "stub.toml"
        harness.parent.mkdir(parents=True)
        harness.write_text(
            f'id = "stub"\nstart = ["{stub}", "{{prompt}}"]\n'
            f'turn = ["{stub}", "{{session_id}}", "{{prompt}}"]\n'
            'session_id = "generated"\nenv = []\n\n[permission]\nask = []\nauto = []\n',
            encoding="utf-8",
        )
        mapping = store_root() / "mapping.toml"
        rewritten = mapping.read_text(encoding="utf-8").replace(
            'role = "lead"\nharness = "claude"',
            'role = "lead"\nharness = "stub"',
            1,
        )
        mapping.write_text(rewritten, encoding="utf-8")

        assert main(["seat", "spawn", "lead"]) == 0
        assert _until(lambda: "foil: turn ended (exit 1)" in _peek(capsys))
        assert "stub harness failed" in _peek(capsys)
        assert main(["seat", "list", "--json"]) == 0
        row = json.loads(capsys.readouterr().out)[0]
        assert (row["state"], row["last_exit"]) == ("idle", 1)

        pid = subprocess.run(
            [real, "-L", socket, "list-panes", "-a", "-F", "#{pane_pid}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0]
        os.kill(int(pid), signal.SIGKILL)
        assert _until(lambda: "lead\tlead\tdead\t" in _listed(capsys))
        first = _windows(real, socket)
        assert len(first) == 1
        assert "stub harness failed" in _peek(capsys)

        assert main(["seat", "resume", "lead"]) == 0
        capsys.readouterr()
        assert _until(lambda: "\tdead\t" not in _listed(capsys))
        plan = json.loads(
            (foil_root(repo) / "fleets" / "main" / "run" / "plans" / "lead.json").read_text(
                encoding="utf-8"
            )
        )
        assert (plan["first"], plan["restarted"]) == ("turn", True)
        assert len(_windows(real, socket)) == 1

        assert main(["seat", "kill", "--all"]) == 0
        assert _windows(real, socket) == []
        assert load_registry(repo)["seats"]["lead"]["state"] == "killed"
    finally:
        subprocess.run([real, "-L", socket, "kill-server"], check=False, capture_output=True)
