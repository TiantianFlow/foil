"""Scenario 16: the human's agent joins as the lead, as a process of its own.

The last test is the dead-seat check's interval, by the real clock's numbers.

The repository is scenario 1's: one failing test that one line fixes. The
agent's harness is `codex`, the preset with a wake command, and a stand-in
for that CLI keeps each wake it is given.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import load_registry
from tests.e2e.support import (
    FIXTURES,
    board,
    close,
    fleet_dir,
    mail,
    outside_agent,
    scripts,
    states,
    wait,
)
from tests.e2e.test_scenarios import _git, _no_windows, _prepare
from tests.harness import standin

pytestmark = pytest.mark.e2e

_NEW = "foil: 2 new for lead. Run foil mail wait --timeout 0.\n"


def test_scenario_16_a_joined_lead_merges_reviewed_work_and_hands_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    clis = tmp_path / "clis"
    clis.mkdir()
    standin.install(clis)
    monkeypatch.setenv("PATH", os.pathsep.join((str(clis), os.environ["PATH"])))
    kept = tmp_path / "wakes.jsonl"
    monkeypatch.setenv("STANDIN_WAKES", str(kept))
    try:
        scripts(
            repo,
            "scenario-16",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        script = json.loads(
            (FIXTURES / "scenario-16" / "scripts" / "human.json").read_text("utf-8")
        )
        ran = outside_agent(repo, tmp_path, "human", script["steps"]).finish(timeout=150)
        runs = [entry for entry in ran if entry["kind"] == "run"]
        first, second, third = (entry["out"] for entry in ran if entry["kind"] == "await")
        verdict, word = (entry["out"] for entry in runs if entry["argv"][1:3] == ["mail", "read"])
        (left,) = (entry["out"] for entry in runs if entry["argv"][1:3] == ["seat", "leave"])

        # The join named the wake route, because the harness has one and the session is known.
        assert "foil mail wait --wake" in runs[0]["out"]
        box = str((board(repo) / "mail" / "lead").resolve())
        # The wait was already running when the worker sent its two mails, half a second
        # apart, and both came out of that one wait.
        assert [line.split()[1] for line in first.splitlines()] == ["implementer-1"] * 2
        assert all(box in line for line in first.splitlines())
        # A blocking wait leaves its items new, and every other command says so on stderr.
        listed, taken, nothing = runs[4], runs[5], runs[6]
        assert "lead\tlead\tjoined\t" in listed["out"]
        assert listed["err"] == _NEW
        assert (taken["out"], nothing["out"]) == (first, "no new mail\n")
        # The second wait, started after the first ended, saw only what came after.
        assert len(second.splitlines()) == 1 and " reviewer-1 " in second
        assert verdict.rstrip().endswith("approved")

        # Each wait that found something ran the preset's wake once, for the joined session.
        written = kept.read_text(encoding="utf-8") if kept.is_file() else ""
        wakes = [json.loads(line) for line in written.splitlines()]
        paths = [line.split()[-1] for line in (first + second).splitlines()]
        assert wakes == [
            {
                "thread": "thread-1",
                "message": f"foil: 2 new: implementer-1 {paths[0]}; implementer-1 {paths[1]}",
            },
            {"thread": "thread-1", "message": f"foil: 1 new: reviewer-1 {paths[2]}"},
        ]

        # The worker's branch is merged into the human's own checkout, and the tests pass.
        assert mail(repo, "implementer-1") == [("lead", "fix the failing test")]
        assert (
            _git(repo, "merge-base", "--is-ancestor", "foil/implementer-1", "HEAD").returncode == 0
        )
        assert _git(repo, "status", "--porcelain").stdout == ""
        checked = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr

        # After the leave the caller is the human again: a launched lead took the seat from
        # the handoff, and its mail came back from a wait on the human's mailbox.
        assert "lead left." in left
        assert len(third.splitlines()) == 1 and " lead " in third
        assert str((board(repo) / "mail" / "user").resolve()) in third
        handed = "continuing from the handoff: foil/implementer-1 is merged and reviewed."
        assert word.rstrip().endswith(handed)
        assert mail(repo, "user") == [("lead", handed)]
        assert ("lead\tlead\talive\t" in runs[-1]["out"], runs[-1]["err"]) == (True, "")
        assert states(repo) == dict.fromkeys(("implementer-1", "lead", "reviewer-1"), "alive")
        session = str(load_registry(repo)["tmux_session"])
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(session)
    finally:
        close(repo)


def test_a_dead_seat_is_reported_once_the_last_check_is_thirty_seconds_old(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    record = fleet_dir(repo) / "run" / "seats" / "lead.json"

    def last_check_was(seconds: float) -> None:
        """Move the time of the last check back. The interval itself is not patched."""

        kept = json.loads(record.read_text(encoding="utf-8"))
        kept["checked"] = time.time() - seconds
        record.write_text(json.dumps(kept), encoding="utf-8")

    def waited() -> str:
        capsys.readouterr()
        assert main(["mail", "wait", "--timeout", "0"]) == 0
        return capsys.readouterr().out

    try:
        assert main(["seat", "join", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        assert waited() == "no new mail\n"
        window = load_registry(repo)["seats"]["implementer-1"]["window_id"]
        subprocess.run(["tmux", "kill-window", "-t", window], check=True)
        wait(repo, lambda: states(repo)["implementer-1"] == "dead", timeout=10, dead_ok=True)

        # A check 25 seconds ago is recent enough: the seat is dead and nothing says so yet.
        last_check_was(25)
        assert waited() == "no new mail\n"
        # At 31 seconds any command outside the fleet checks again, and the wait has the mail.
        last_check_was(31)
        capsys.readouterr()
        assert main(["seat", "list"]) == 0
        assert capsys.readouterr().err == "foil: 1 new for lead. Run foil mail wait --timeout 0.\n"
        (line,) = waited().splitlines()
        assert line.split()[1] == "foil"
        died = Path(line.split()[-1]).read_text(encoding="utf-8")
        assert "implementer-1 died. See foil seat peek implementer-1 --lines 200." in died
        # The same death is not reported twice.
        last_check_was(31)
        assert waited() == "no new mail\n"
    finally:
        close(repo)
