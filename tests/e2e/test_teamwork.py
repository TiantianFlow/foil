"""Scenarios 12 to 15: rework after a review, a merge conflict, two fleets, a scoped room.

Scenarios 12 and 13 use scenario 1's repository: one failing test that one line fixes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from foil.board_ops import split_front_matter
from foil.cli import main
from foil.store import load_registry
from tests.e2e.support import board, close, mail, outside_agent, scripts, states, wait
from tests.e2e.test_scenarios import _finish_scenario_1, _git, _no_windows, _prepare, _status

pytestmark = pytest.mark.e2e


def _note(repo: Path, name: str, fleet: str = "main") -> str:
    path = board(repo, fleet) / "notes" / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_scenario_12_a_rejected_fix_is_reworked_and_reviewed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        scripts(
            repo,
            "scenario-12",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        wait(repo, lambda: "state: done" in _status(repo), timeout=30, what="the merge")

        # Each seat was told what the script says, by the seat that says it.
        assert mail(repo, "lead") == [
            ("implementer-1", "fixed on foil/implementer-1"),
            ("implementer-1", "reworked on foil/implementer-1"),
            ("reviewer-1", "approved"),
            ("reviewer-1", "rejected: check_calc.py fails"),
            ("user", "make the tests pass"),
        ]
        assert mail(repo, "implementer-1") == [
            ("lead", "fix the failing test"),
            ("lead", "rework: the review rejected it, see board/results/review.md"),
        ]
        assert mail(repo, "reviewer-1") == [("lead", "review again"), ("lead", "review the fix")]
        # The same two seats did both rounds: nothing was spawned for the rework.
        assert states(repo) == dict.fromkeys(("implementer-1", "lead", "reviewer-1"), "alive")
        tries = _git(repo, "log", "--format=%s", "foil/implementer-1").stdout.splitlines()
        assert tries == ["rework", "first try", "base"]
        review = (board(repo) / "results" / "review.md").read_text(encoding="utf-8")
        assert "1 passed" in review
        _finish_scenario_1(repo)
    finally:
        close(repo)


def test_scenario_13_two_implementers_change_one_line_and_one_resolves_the_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        scripts(
            repo,
            "scenario-13",
            {
                "lead": "lead.json",
                "implementer-1": "implementer-1.json",
                "implementer-2": "implementer-2.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "two changes to add"]) == 0
        wait(repo, lambda: "state: done" in _status(repo), timeout=30, what="both merges")

        seats = load_registry(repo)["seats"]
        first, second = seats["implementer-1"], seats["implementer-2"]
        assert (first["branch"], second["branch"]) == ("foil/implementer-1", "foil/implementer-2")
        assert first["worktree"] != second["worktree"]
        # Whichever branch came second met a conflict, and its own seat resolved it.
        asked = [
            (name, body)
            for name in ("implementer-1", "implementer-2")
            for _sender, body in mail(repo, name)
            if body.startswith("conflict: merge ")
        ]
        assert len(asked) == 1, asked
        late = asked[0][0]
        resolved = [body for _sender, body in mail(repo, "lead") if body.startswith("resolved on ")]
        assert resolved == [f"resolved on foil/{late}"]
        assert sorted(_note(repo, "merged.txt").split()) == [
            "foil/implementer-1",
            "foil/implementer-2",
        ]

        for branch in ("foil/implementer-1", "foil/implementer-2"):
            assert _git(repo, "merge-base", "--is-ancestor", branch, "HEAD").returncode == 0
        assert (repo / "calc.py").read_text(
            encoding="utf-8"
        ) == "def add(a, b):\n    return a + b\n"
        assert _git(repo, "status", "--porcelain").stdout == ""
        assert _git(repo, "rev-parse", "--verify", "--quiet", "MERGE_HEAD").returncode != 0
        checked = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--noconftest", "check_calc.py"],
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


def test_scenario_14_a_second_fleet_runs_beside_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-14", monkeypatch)
    both = ("main", "review")
    try:
        for fleet in both:
            scripts(
                repo,
                "scenario-14",
                {"lead": "lead.json", "implementer-1": "implementer.json"},
                fleet,
            )
        # The first fleet is the default one. The second is chosen by FOIL_FLEET
        # in the environment of the process that works on it, and nowhere else.
        assert main(["seat", "spawn", "lead", "--task", "staff the fleet"]) == 0
        staffed = [{"run": ["foil", "seat", "spawn", "lead", "--task", "staff the fleet"]}]
        outside_agent(repo, tmp_path, "staff", staffed, fleet="review").finish()
        wait(
            repo,
            lambda: all(_note(repo, "implementer-1-cwd.txt", fleet) for fleet in both),
            timeout=30,
            what="both fleets' implementers",
        )

        registries = {fleet: load_registry(repo, fleet) for fleet in both}
        for fleet in both:
            # Each seat was told its own fleet, and a seat's spawn stayed in it.
            assert _note(repo, "lead.txt", fleet) == f"{fleet}\n"
            assert _note(repo, "implementer-1.txt", fleet) == f"{fleet}\n"
            assert states(repo, fleet) == {"lead": "alive", "implementer-1": "alive"}
            worker = registries[fleet]["seats"]["implementer-1"]
            assert _note(repo, "implementer-1-cwd.txt", fleet) == f"{worker['worktree']}\n"
            assert mail(repo, "implementer-1", fleet) == [("lead", "say where you are")]
        sessions = {str(registries[fleet]["tmux_session"]) for fleet in both}
        assert len(sessions) == 2
        workers = [registries[fleet]["seats"]["implementer-1"] for fleet in both]
        assert workers[0]["worktree"] != workers[1]["worktree"]
        assert workers[0]["branch"] != workers[1]["branch"]
        assert workers[0]["session_id"] != workers[1]["session_id"]

        # Mail, the seat list and a kill reach the chosen fleet only.
        steps = [
            {"run": ["foil", "send", "lead", "only for this fleet"]},
            {"run": ["foil", "seat", "list"]},
            {"until": ["grep", "-q", "heard", str(board(repo, "review") / "notes" / "heard.txt")]},
            {"run": ["foil", "seat", "kill", "--all"]},
        ]
        ran = outside_agent(repo, tmp_path, "steer", steps, fleet="review").finish()
        listed = [entry["out"] for entry in ran if entry["kind"] == "run"][1]
        assert [line.split("\t")[:3] for line in listed.splitlines()] == [
            ["implementer-1", "implementer", "alive"],
            ["lead", "lead", "alive"],
        ]
        assert _note(repo, "heard.txt", "review") == "heard\n"
        assert _note(repo, "heard.txt", "main") == ""
        assert ("user", "only for this fleet") in mail(repo, "lead", "review")
        assert mail(repo, "lead", "main") == [("user", "staff the fleet")]
        _no_windows(str(registries["review"]["tmux_session"]))
        assert states(repo, "review") == {"lead": "killed", "implementer-1": "killed"}
        assert states(repo, "main") == {"lead": "alive", "implementer-1": "alive"}

        capsys.readouterr()
        assert main(["seat", "list"]) == 0
        assert capsys.readouterr().out.count("\talive\t") == 2
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(str(registries["main"]["tmux_session"]))
    finally:
        close(repo)


def _room(repo: Path) -> list[tuple[str, dict, str]]:
    """The one room's files in write order, as (author, front matter, body)."""

    (directory,) = (board(repo) / "room").iterdir()
    root = directory / f"{directory.name}.md"
    later = sorted(path for path in directory.glob("*.md") if path != root)
    found = []
    for path in (root, *later):
        fields, body = split_front_matter(path.read_text(encoding="utf-8"))
        assert fields is not None, path
        found.append((str(fields["from"]), fields, body))
    return found


def test_scenario_15_the_lead_scopes_a_room_and_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-15", monkeypatch)
    try:
        scripts(
            repo,
            "scenario-15",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "settle the format"]) == 0
        wait(repo, lambda: _note(repo, "late.txt"), timeout=30, what="the reply after the close")

        # Foil refused the reply to the closed room, and wrote no file for it.
        assert _note(repo, "late.txt") == "1\n"
        files = _room(repo)
        assert [author for author, _fields, _body in files] == [
            "lead",
            "implementer-1",
            "lead",
            "reviewer-1",
            "lead",
        ]
        opened, answer, widened, declined, closed = files
        # The room opened for one worker. The other was not told.
        assert (opened[0], opened[1]["members"], opened[1]["nudged"]) == (
            "lead",
            ["implementer-1", "lead"],
            ["implementer-1"],
        )
        assert opened[2].rstrip().endswith("Which format do results use?")
        # A reply wakes the members, and its author is not one of them.
        assert (answer[0], answer[1]["nudged"]) == ("implementer-1", ["lead"])
        assert "members" not in answer[1]
        # A longer member list wakes the seat it adds, and nobody a second time.
        assert (widened[0], widened[1]["members"], widened[1]["nudged"]) == (
            "lead",
            ["implementer-1", "lead", "reviewer-1"],
            ["reviewer-1"],
        )
        assert (declined[0], declined[1]["nudged"], declined[2].strip()) == (
            "reviewer-1",
            [],
            "decline",
        )
        # The close wakes the seat it names and no other member.
        assert (closed[0], closed[1]["closed"], closed[1]["nudged"]) == (
            "lead",
            "true",
            ["implementer-1"],
        )
        assert "This room is closed." in closed[2]

        # What each seat was woken with is what the files say was typed.
        assert len(_note(repo, "implementer-1-woken.txt").split()) == 2
        assert len(_note(repo, "reviewer-1-woken.txt").split()) == 1
        assert states(repo) == dict.fromkeys(("implementer-1", "lead", "reviewer-1"), "alive")
    finally:
        close(repo)
