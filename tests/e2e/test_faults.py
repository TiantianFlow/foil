"""Scenarios 10 and 11, and a startup dialog: seats that crash, hang, or wait.

The repository is scenario 1's: one failing test that one line fixes.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import load_registry
from tests.e2e.support import board, close, fleet_dir, launches, scripts, states, wait
from tests.e2e.test_scenarios import _finish_scenario_1, _git, _no_windows, _prepare, _status

pytestmark = pytest.mark.e2e


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_scenario_10_a_seat_that_crashed_mid_task_is_resumed_with_its_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        scripts(repo, "scenario-10", {"lead": "lead.json", "implementer-1": "implementer.json"})
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        wait(
            repo,
            lambda: states(repo).get("implementer-1") == "dead",
            timeout=30,
            what="the worker to crash",
            dead_ok=True,
        )
        seat = load_registry(repo)["seats"]["implementer-1"]
        # The crash left the edit in the worktree and nothing on the branch.
        assert "left + right" in (Path(seat["worktree"]) / "calc.py").read_text(encoding="utf-8")
        assert _git(repo, "rev-list", "--count", "HEAD..foil/implementer-1").stdout == "0\n"
        assert "state: done" not in _status(repo)
        # The pane that was left is still readable, and the name is not free.
        capsys.readouterr()
        assert main(["seat", "peek", "implementer-1"]) == 0
        assert "foil-fake: exit 3" in capsys.readouterr().out
        assert main(["seat", "archive", "implementer-1"]) == 1
        assert capsys.readouterr().err == "foil: seat 'implementer-1' is dead\n"

        assert main(["seat", "resume", "implementer-1"]) == 0
        wait(repo, lambda: "state: done" in _status(repo), timeout=30, what="the merge")
        started = [(item["launch"], item["session"]) for item in launches(repo, "implementer-1")]
        assert started == [("first", seat["session_id"]), ("resume", seat["session_id"])]
        assert load_registry(repo)["seats"]["implementer-1"]["session_id"] == seat["session_id"]
        _finish_scenario_1(repo)
    finally:
        close(repo)


def test_scenario_11_a_worker_that_hangs_in_silence_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        scripts(repo, "scenario-11", {"lead": "lead.json", "implementer-1": "implementer.json"})
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        question = board(repo) / "mail" / "user"
        wait(repo, lambda: list(question.glob("*.md")), timeout=30, what="the lead's question")
        assert "state: blocked" in _status(repo)
        assert "implementer-1 has sent nothing" in _status(repo)
        # From outside, a seat that hangs is a seat that is alive.
        assert states(repo) == {"lead": "alive", "implementer-1": "alive"}
        peeked = (board(repo) / "notes" / "peek.txt").read_text(encoding="utf-8")
        assert "foil-fake implementer-1 ready" in peeked
        first = load_registry(repo)["seats"]["implementer-1"]
        hung = launches(repo, "implementer-1")[0]["pid"]
        assert _running(hung)

        assert main(["send", "lead", "replace it"]) == 0
        wait(repo, lambda: "state: done" in _status(repo), timeout=30, what="the merge")
        second = load_registry(repo)["seats"]["implementer-1"]
        assert (second["branch"], second["worktree"] != first["worktree"]) == (
            "foil/implementer-1-2",
            True,
        )
        assert [item["launch"] for item in launches(repo, "implementer-1")] == ["first", "fresh"]
        # The kill ended the process that hung, and the archive kept its branch.
        wait(repo, lambda: not _running(hung), timeout=10, what="the hung process to end")
        assert _git(repo, "show-ref", "--verify", "refs/heads/foil/implementer-1").returncode == 0
        capsys.readouterr()
        assert main(["seat", "list", "--archived"]) == 0
        assert "implementer-1\timplementer\tkilled\t" in capsys.readouterr().out
        assert _git(repo, "merge-base", "--is-ancestor", second["branch"], "HEAD").returncode == 0
        checked = subprocess.run(
            ["python3", "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
        session = str(load_registry(repo)["tmux_session"])
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(session)
    finally:
        close(repo)


def test_a_seat_at_a_startup_dialog_gets_its_task_once_the_dialog_is_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    trust = "Do you trust the files in this folder? 1. Yes 2. No"
    try:
        fake = fleet_dir(repo) / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        script = {
            "start": [{"wait": 1}, {"dialog": trust, "accept": "1"}],
            "steps": [
                {
                    "when": {"mail_contains": "write the marker"},
                    "do": [{"write": "board/notes/done.txt", "text": "done\n"}],
                }
            ],
        }
        (fake / "lead.json").write_text(json.dumps(script), encoding="utf-8")
        assert main(["seat", "spawn", "lead", "--task", "write the marker"]) == 0
        window = load_registry(repo)["seats"]["lead"]["window_id"]

        def pane() -> str:
            shown = subprocess.run(
                ["tmux", "capture-pane", "-p", "-J", "-t", window],
                check=False,
                capture_output=True,
                text=True,
            )
            return shown.stdout

        # The dialog only takes its own answer. A task typed into it would end the seat.
        wait(repo, lambda: trust in pane(), timeout=30, what="the dialog")
        marker = board(repo) / "notes" / "done.txt"
        assert not marker.exists()
        assert pane().strip() == trust
        assert states(repo) == {"lead": "alive"}
        # The human's agent clears a folder-trust prompt itself (F12).
        subprocess.run(["tmux", "send-keys", "-t", window, "1", "Enter"], check=True)
        wait(repo, marker.is_file, timeout=30, what="the task after the dialog")
        assert states(repo) == {"lead": "alive"}
    finally:
        close(repo)
