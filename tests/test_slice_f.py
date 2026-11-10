"""Slice F: send and post write a file and run no tmux command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.tmux import TmuxController, TmuxTarget
from tests.test_slice_b import _mail, _repo, _seed


def _tmux_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(self: TmuxController, argv: list[str]) -> subprocess.CompletedProcess[str]:
        del self
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("foil.tmux.TmuxController._run", run)
    return calls


def test_send_and_post_touch_no_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead", window_id="@12")
    calls = _tmux_calls(monkeypatch)
    assert main(["send", "lead", "hello"]) == 0
    assert main(["post", "--members", "lead", "a thread"]) == 0
    assert capsys.readouterr().err == ""
    assert len(_mail(repo, "lead")) == 1
    assert calls == []


def test_spawn_task_writes_mail_before_launch_and_types_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    calls = _tmux_calls(monkeypatch)
    ready: list[str] = []

    def launch(self: TmuxController, **kwargs: object) -> object:
        del self
        mail = _mail(repo, "lead")
        assert mail, "task mail exists before the window"
        ready.append(mail[0].read_text(encoding="utf-8"))
        return TmuxTarget(
            session_name=str(kwargs["session_name"]),
            window_name=str(kwargs["window_name"]),
            session_id="$1",
            window_id="@21",
        )

    monkeypatch.setattr("foil.lifecycle.TmuxController.launch", launch)
    assert main(["seat", "spawn", "lead", "--task", "ship it"]) == 0
    assert ready[0].endswith("ship it\n")
    assert all(call[0] != "send-keys" for call in calls)
