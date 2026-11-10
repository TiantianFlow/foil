"""The human's own agent as a real process: scenario 9, and two callers at once."""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

import foil.tmux
from foil.board_ops import split_front_matter
from foil.cli import main
from foil.store import load_registry
from tests.e2e.support import (
    FIXTURES,
    board,
    close,
    fleet_dir,
    launches,
    outside_agent,
    scripts,
    states,
    timeout_report,
    wait,
)
from tests.e2e.test_scenarios import _git, _no_windows, _panes, _prepare, _status

pytestmark = pytest.mark.e2e


def _mail(repo: Path, box: str) -> list[tuple[str, str]]:
    """Each mail in a mailbox as (sender, body), sorted. Two in one second have no order."""

    found = []
    for path in (board(repo) / "mail" / box).glob("*.md"):
        fields, body = split_front_matter(path.read_text(encoding="utf-8"))
        found.append((str((fields or {}).get("from")), body.strip()))
    return sorted(found)


def test_scenario_9_a_launched_lead_asks_the_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-9", monkeypatch)
    try:
        scripts(repo, "scenario-9", {"lead": "lead.json"})
        steps = json.loads((FIXTURES / "scenario-9" / "scripts" / "human.json").read_text("utf-8"))
        ran = outside_agent(repo, tmp_path, "human", steps["steps"]).finish()
        printed = [entry["out"] for entry in ran if entry["kind"] == "run"]

        # What the human's agent read, in the order it read it.
        _spawned, _first, question, blocked, _sent, _later, answer, done, peeked = printed
        assert question == "question in board/status.md: which color\n"
        assert "state: blocked" in blocked and "Which color should the status use?" in blocked
        assert answer == "done: the status uses blue\n"
        assert "state: done" in done and "The answer is blue." in done

        # What is on the board afterwards.
        assert "The answer is blue." in _status(repo)
        assert _mail(repo, "user") == [
            ("lead", "done: the status uses blue"),
            ("lead", "question in board/status.md: which color"),
        ]
        # The agent's process had no seat identity, so its mail is the human's.
        assert _mail(repo, "lead") == [("user", "pick a color"), ("user", "use blue")]

        # The peek is the raw pane, and mail to the human was typed into no pane.
        registry = load_registry(repo)
        window = registry["seats"]["lead"]["window_id"]
        captured = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window, "-S", "-40"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert peeked == captured.stdout
        assert "foil-fake lead ready" in peeked
        session = str(registry["tmux_session"])
        mailbox = str((board(repo) / "mail" / "user").resolve())
        assert all(mailbox not in text for text in _panes(session).values())
        assert main(["seat", "kill", "--all"]) == 0
        _no_windows(session)
    finally:
        close(repo)


def _counting_lead(repo: Path) -> Path:
    """A lead that writes down each mail it reads. Returns the list it keeps."""

    fake = fleet_dir(repo) / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    step = {
        "when": {"mail_contains": "count me"},
        "do": [{"sh": 'sed -n "s/^count me //p" "$FAKE_MAIL" >> "$FAKE_BOARD/notes/read.txt"'}],
    }
    (fake / "lead.json").write_text(json.dumps({"steps": [step]}), encoding="utf-8")
    return board(repo) / "notes" / "read.txt"


def _read(path: Path) -> list[str]:
    return sorted(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else []


def test_two_callers_outside_the_fleet_work_on_it_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        read = _counting_lead(repo)
        assert main(["seat", "spawn", "lead"]) == 0

        def steps(who: str, role: str) -> list[dict]:
            return [
                {"run": ["foil", "seat", "spawn", role]},
                *({"run": ["foil", "send", "lead", f"count me {who}{n}"]} for n in range(3)),
                {"run": ["foil", "post", f"room from {who}"]},
                {"run": ["foil", "memory", "add", f"lesson from {who}"]},
            ]

        agents = [
            outside_agent(repo, tmp_path, who, steps(who, role))
            for who, role in (("a", "reviewer"), ("b", "implementer"))
        ]
        for agent in agents:
            agent.finish()

        # Both seats are in the registry, each in its own window.
        seats = states(repo)
        assert seats == dict.fromkeys(("implementer-1", "lead", "reviewer-1"), "alive"), (
            timeout_report(repo)
        )
        session = str(load_registry(repo)["tmux_session"])
        windows = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert sorted(windows.stdout.split()) == sorted(seats)
        # Nothing either caller wrote was lost, and the lead was woken for each mail.
        sent = sorted(f"{who}{n}" for who in ("a", "b") for n in range(3))
        assert _mail(repo, "lead") == [("user", f"count me {name}") for name in sent]
        wait(repo, lambda: _read(read) == sent, timeout=20, what="the lead to read six mails")
        rooms = [path for path in (board(repo) / "room").iterdir() if path.is_dir()]
        assert len(rooms) == 2
        capsys.readouterr()
        assert main(["memory", "list", "--all"]) == 0
        listed = capsys.readouterr().out
        assert "lesson from a" in listed and "lesson from b" in listed
    finally:
        close(repo)


def test_two_nudges_at_once_reach_the_seat_as_two_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    # A wide gap between a line and its Enter, so the second sender surely arrives in it.
    monkeypatch.setattr(foil.tmux, "NUDGE_ENTER_DELAY", 1.0)
    try:
        read = _counting_lead(repo)
        assert main(["seat", "spawn", "lead"]) == 0
        wait(repo, lambda: launches(repo, "lead"), what="the lead to start")
        senders = [
            threading.Thread(target=main, args=(["send", "lead", f"count me {n}"],))
            for n in range(2)
        ]
        for sender in senders:
            sender.start()
        for sender in senders:
            sender.join()
        wait(repo, lambda: _read(read) == ["0", "1"], timeout=10, what="the lead to read both")
    finally:
        close(repo)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "A spawn that fails after it made the seat's worktree removes the window and the "
        "task mail, and leaves the worktree and its branch with no seat. The same is left "
        "when two callers spawn one role at once and the second finds the name taken."
    ),
)
def test_a_spawn_that_cannot_launch_leaves_no_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        session = str(load_registry(repo)["tmux_session"])
        # Someone else's session takes the fleet's name, so the next launch is refused.
        subprocess.run(["tmux", "kill-session", "-t", session], check=True)
        subprocess.run(["tmux", "new-session", "-d", "-s", session, "sleep", "60"], check=True)
        capsys.readouterr()
        assert main(["seat", "spawn", "implementer", "--task", "a task"]) == 1
        assert capsys.readouterr().err == "foil: could not launch seat\n"
        assert "implementer-1" not in load_registry(repo)["seats"]
        assert not list((board(repo) / "mail").glob("implementer-1/*.md"))
        listed = _git(repo, "worktree", "list", "--porcelain").stdout
        assert listed.count("worktree ") == 1, listed
        assert _git(repo, "branch", "--list", "foil/*").stdout == ""
    finally:
        subprocess.run(["tmux", "kill-session", "-t", session], check=False, capture_output=True)
        close(repo)
