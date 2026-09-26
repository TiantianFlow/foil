"""Slice F: mail is written first, then one nudge line."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.tmux import TmuxController
from tests.test_slice_b import _mail, _repo, _seed
from tests.test_spawn import _launch


def _keys(monkeypatch: pytest.MonkeyPatch, code: int = 0) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(self: TmuxController, argv: list[str]) -> subprocess.CompletedProcess[str]:
        del self
        calls.append(list(argv))
        if argv and argv[0] == "display-message":
            window = argv[argv.index("-t") + 1]
            registry = json.loads(Path(".foil/run/registry.json").read_text(encoding="utf-8"))
            seat = next(
                name
                for name, record in registry["seats"].items()
                if record.get("window_id") == window
            )
            stdout = f"$0\t{window}\t{registry['fleet_id']}\t{seat}\n"
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        return subprocess.CompletedProcess(argv, code, "", "")

    monkeypatch.setattr("foil.tmux.TmuxController._run", run)
    return calls


def test_send_types_the_sender_and_mail_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead", window_id="@12")
    calls = _keys(monkeypatch)
    assert main(["send", "lead", "hello"]) == 0
    mail = _mail(repo, "lead")[0]
    line = f"user {mail.resolve()}"
    assert calls[0][:4] == ["display-message", "-p", "-t", "@12"]
    assert calls[1:] == [
        ["send-keys", "-l", "-t", "@12", "--", line],
        ["send-keys", "-t", "@12", "Enter"],
    ]
    assert "hello" not in line


def test_missing_window_keeps_the_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead", window_id="@12")
    calls = _keys(monkeypatch, code=1)
    assert main(["send", "lead", "hello"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Traceback" not in captured.err
    assert len(_mail(repo, "lead")) == 1
    assert calls[0][:4] == ["display-message", "-p", "-t", "@12"]
    assert calls[1:] == [["send-keys", "-l", "-t", "@12", "--", calls[1][-1]]]
    assert "hello" not in calls[1][-1]


def test_spawn_task_nudges_with_the_same_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _launch(monkeypatch)
    calls = _keys(monkeypatch)
    assert main(["seat", "spawn", "lead", "--task", "ship it"]) == 0
    mail = _mail(repo, "lead")[0]
    assert mail.read_text(encoding="utf-8").endswith("ship it\n")
    assert calls[0][:4] == ["display-message", "-p", "-t", "@21"]
    assert calls[1] == ["send-keys", "-l", "-t", "@21", "--", f"user {mail.resolve()}"]
    assert "ship it" not in calls[1][-1]
    assert calls[2] == ["send-keys", "-t", "@21", "Enter"]


def test_send_does_not_type_into_a_foreign_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _seed(repo, "lead", window_id="@12")
    calls: list[list[str]] = []

    def run(self: TmuxController, argv: list[str]) -> subprocess.CompletedProcess[str]:
        del self
        calls.append(list(argv))
        if argv and argv[0] == "display-message":
            return subprocess.CompletedProcess(argv, 0, "$0\t@12\tfleet\tother\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("foil.tmux.TmuxController._run", run)
    assert main(["send", "lead", "hello"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(_mail(repo, "lead")) == 1
    assert calls[0][0] == "display-message"
    assert all(call[0] != "send-keys" for call in calls)
