"""A harness that exits at launch leaves a pane peek can read."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
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
        f"#!/bin/sh\nexec {real} -L {socket} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{wrapper.parent}{os.pathsep}{os.environ['PATH']}")
    return real


def test_an_exited_harness_stays_peekable_until_kill_or_resume(
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
        harness.write_text(
            f'id = "stub"\ncommand = ["{stub}"]\n'
            'session_id = "none"\nenv = []\n\n[permission]\nask = []\nauto = []\n',
            encoding="utf-8",
        )
        lead = foil_root(repo) / "templates" / "lead.toml"
        rewritten = re.sub(
            r'harness = "[^"]+"',
            'harness = "stub"',
            lead.read_text(encoding="utf-8"),
            count=1,
        )
        lead.write_text(rewritten, encoding="utf-8")

        assert main(["seat", "spawn", "lead"]) == 0
        deadline = __import__("time").time() + 5
        listed = ""
        while __import__("time").time() < deadline:
            assert main(["seat", "list"]) == 0
            listed = capsys.readouterr().out
            if "\tdead\t" in listed:
                break
        assert "lead\tlead\tdead\t" in listed
        first = _windows(real, socket)
        assert len(first) == 1

        assert main(["seat", "peek", "lead"]) == 0
        assert "stub harness failed" in capsys.readouterr().out

        assert main(["seat", "resume", "lead"]) == 0
        capsys.readouterr()
        deadline = __import__("time").time() + 5
        listed = ""
        while __import__("time").time() < deadline:
            assert main(["seat", "list"]) == 0
            listed = capsys.readouterr().out
            if "\tdead\t" in listed:
                break
        assert "lead\tlead\tdead\t" in listed
        instruction = foil_root(repo) / "run" / "instructions" / "lead.md"
        assert "You were restarted." in instruction.read_text(encoding="utf-8")
        assert len(_windows(real, socket)) == 1

        assert main(["seat", "kill", "--all"]) == 0
        assert _windows(real, socket) == []
        assert load_registry(repo)["seats"]["lead"]["state"] == "killed"
    finally:
        subprocess.run([real, "-L", socket, "kill-server"], check=False, capture_output=True)
