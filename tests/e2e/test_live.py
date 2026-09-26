"""Scenarios 1–6 with the harness ``foil init`` selects.

Skipped unless ``FOIL_E2E_LIVE=1``. The default suite rewrites templates
to the fake harness and does not run this module's tests.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry
from tests.e2e.test_scenarios import (
    _close,
    _finish_scenario_1,
    _prepare,
    _status,
    _wait,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.e2e_live,
    pytest.mark.skipif(
        os.environ.get("FOIL_E2E_LIVE") != "1",
        reason="set FOIL_E2E_LIVE=1 to run scenarios against a real harness CLI",
    ),
]

_REAL_PATH = os.environ.get("PATH", "")
_TIMEOUT = 600.0
_TASK_1 = (
    "make the tests pass. Follow TASK.md. "
    "Spawn implementer-1 to run sh fix.sh, then reviewer-1 to run sh review.sh. "
    "Merge foil/implementer-1 and write board/status.md containing the line state: done."
)
_TASK_4 = (
    "ask the operator. Follow TASK.md. "
    "Write board/status.md containing the line: Which color should the status use? "
    "After mail that says use blue, write board/status.md containing the line: The answer is blue."
)
_TASK_5 = (
    "staff a worker. Follow TASK.md. "
    "Spawn implementer-1 to run sh propose.sh. "
    "That script writes refused to board/notes/accept.txt and proposes the lesson "
    "check the merged tests. Then run sh accept.sh, which accepts it and spawns "
    "reviewer --name reader."
)


@pytest.fixture(autouse=True)
def _use_real_harness_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the suite's harness stubs so init selects a real CLI."""

    monkeypatch.setenv("PATH", _REAL_PATH)


def test_scenario_1_live(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch, fake=False)
    try:
        assert main(["seat", "spawn", "lead", "--task", _TASK_1]) == 0
        _finish_scenario_1(repo, _TIMEOUT)
    finally:
        _close(repo)


def test_scenario_2_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch, fake=False)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        before = (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
        monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
        assert main(["seat", "spawn", "reviewer"]) == 1
        assert capsys.readouterr().err == "foil: not allowed\n"
        assert main(["seat", "kill", "lead"]) == 1
        assert capsys.readouterr().err == "foil: not allowed\n"
        monkeypatch.delenv("FOIL_SEAT_ID")
        after = (foil_root(repo) / "run" / "registry.json").read_text(encoding="utf-8")
        assert after == before
    finally:
        _close(repo)


def test_scenario_3_live(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch, fake=False)
    try:
        assert main(["seat", "spawn", "lead", "--task", _TASK_1]) == 0
        _wait(repo, lambda: "implementer-1" in load_registry(repo)["seats"], _TIMEOUT)
        session = str(load_registry(repo)["tmux_session"])
        killed = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
            capture_output=True,
            text=True,
        )
        assert killed.returncode == 0
        assert main(["seat", "resume"]) == 0
        _finish_scenario_1(repo, _TIMEOUT)
    finally:
        _close(repo)


def test_scenario_4_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-4", monkeypatch, fake=False)
    try:
        assert main(["seat", "spawn", "lead", "--task", _TASK_4]) == 0
        _wait(repo, lambda: "Which color" in _status(repo), _TIMEOUT)
        assert main(["send", "lead", "use blue"]) == 0
        _wait(repo, lambda: "The answer is blue." in _status(repo), _TIMEOUT)
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        captured = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window, "-S", "-40"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert main(["seat", "peek", "lead"]) == 0
        assert capsys.readouterr().out == captured.stdout
    finally:
        _close(repo)


def test_scenario_5_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-5", monkeypatch, fake=False)
    try:
        assert main(["seat", "spawn", "lead", "--task", _TASK_5]) == 0
        note = foil_root(repo) / "board" / "notes" / "accept.txt"
        _wait(repo, note.is_file, _TIMEOUT)
        assert note.read_text(encoding="utf-8").strip() == "refused"
        instruction = foil_root(repo) / "run" / "instructions" / "reader.md"

        def lesson_landed() -> bool:
            return (
                instruction.is_file()
                and "check the merged tests" in instruction.read_text(encoding="utf-8")
            )

        _wait(repo, lesson_landed, _TIMEOUT)
        assert main(["memory", "list", "--all"]) == 0
        listed = capsys.readouterr().out
        assert "check the merged tests" in listed
        assert "\taccepted\t" in listed
    finally:
        _close(repo)


def test_scenario_6_live(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-6", monkeypatch, fake=False)
    assert main(["send", "operator", "hello"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: unknown seat 'operator'\n"
    assert "Traceback" not in captured.err

    assert main(["seat", "spawn", "nope"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: unknown template 'nope'\n"
    assert "Traceback" not in captured.err

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert main(["seat", "list"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "foil: not a git repository\n"
    assert captured.err.count("\n") == 1
    assert "Traceback" not in captured.err
    assert repo.is_dir()
