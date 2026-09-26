"""Scenarios 1–6: real Foil, tmux, and git. Only the agent is foil-fake."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _prepare(
    tmp_path: Path, fixture: str, monkeypatch: pytest.MonkeyPatch, *, fake: bool = True
) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    source = FIXTURES / fixture
    for path in source.rglob("*"):
        if not path.is_file() or "scripts" in path.relative_to(source).parts:
            continue
        target = repo / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    assert _git(repo, "init", "--quiet").returncode == 0
    assert _git(repo, "config", "user.name", "Foil Test").returncode == 0
    assert _git(repo, "config", "user.email", "foil-test@localhost").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "--allow-empty", "-m", "base").returncode == 0
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    if not fake:
        return repo
    templates = foil_root(repo) / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$', 'harness = "fake"', path.read_text(encoding="utf-8"), count=1
        )
        path.write_text(text, encoding="utf-8")
    return repo


def _scripts(repo: Path, fixture: str, seats: dict[str, str]) -> None:
    source = FIXTURES / fixture / "scripts"
    dest = foil_root(repo) / "run" / "fake"
    dest.mkdir(parents=True, exist_ok=True)
    for seat, name in seats.items():
        shutil.copy(source / name, dest / f"{seat}.json")


def _logs(repo: Path) -> str:
    directory = foil_root(repo) / "run" / "fake"
    if not directory.is_dir():
        return ""
    chunks = []
    for path in sorted(directory.glob("*.log")):
        chunks.append(f"----- {path.name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(chunks)


def _wait(repo: Path, predicate, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out\n{_logs(repo)}")


def _close(repo: Path) -> None:
    session = load_registry(repo).get("tmux_session") or ""
    if session:
        subprocess.run(["tmux", "kill-session", "-t", session], check=False, capture_output=True)


def _status(repo: Path) -> str:
    path = foil_root(repo) / "board" / "status.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _no_windows(session: str) -> None:
    listed = subprocess.run(
        ["tmux", "list-windows", "-t", session],
        check=False,
        capture_output=True,
        text=True,
    )
    assert listed.returncode != 0


def _finish_scenario_1(repo: Path, timeout: float = 90) -> None:
    _wait(repo, lambda: "state: done" in _status(repo), timeout)
    assert "done" in _status(repo)
    merged = _git(repo, "merge-base", "--is-ancestor", "foil/implementer-1", "HEAD")
    assert merged.returncode == 0
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


def test_scenario_1_merges_a_reviewed_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-1",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        _finish_scenario_1(repo)
    finally:
        _close(repo)


def test_scenario_2_worker_cannot_change_the_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
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


def test_scenario_3_resumes_after_the_session_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-1", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-1",
            {
                "lead": "lead.json",
                "implementer-1": "implementer.json",
                "reviewer-1": "reviewer.json",
            },
        )
        assert main(["seat", "spawn", "lead", "--task", "make the tests pass"]) == 0
        _wait(repo, lambda: "implementer-1" in load_registry(repo)["seats"])
        session = str(load_registry(repo)["tmux_session"])
        killed = subprocess.run(
            ["tmux", "kill-session", "-t", session],
            check=False,
            capture_output=True,
            text=True,
        )
        assert killed.returncode == 0
        assert main(["seat", "resume"]) == 0
        _finish_scenario_1(repo)
    finally:
        _close(repo)


def test_scenario_4_operator_answer_is_raw_on_the_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-4", monkeypatch)
    try:
        _scripts(repo, "scenario-4", {"lead": "lead.json"})
        assert main(["seat", "spawn", "lead", "--task", "ask the operator"]) == 0
        _wait(repo, lambda: "Which color" in _status(repo))
        assert main(["send", "lead", "use blue"]) == 0
        _wait(repo, lambda: "The answer is blue." in _status(repo))
        window = load_registry(repo)["seats"]["lead"]["window_id"]
        captured = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", window, "-S", "-40"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert main(["seat", "peek", "lead"]) == 0
        assert capsys.readouterr().out == captured.stdout
        assert "foil-fake lead ready" in captured.stdout
    finally:
        _close(repo)


def test_scenario_5_accepted_lesson_reaches_the_next_seat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-5", monkeypatch)
    try:
        _scripts(
            repo,
            "scenario-5",
            {"lead": "lead.json", "implementer-1": "implementer.json"},
        )
        assert main(["seat", "spawn", "lead", "--task", "staff a worker"]) == 0
        note = foil_root(repo) / "board" / "notes" / "accept.txt"
        _wait(repo, note.is_file)
        assert note.read_text(encoding="utf-8").strip() == "refused"
        instruction = foil_root(repo) / "run" / "instructions" / "reader.md"
        def lesson_landed() -> bool:
            return (
                instruction.is_file()
                and "check the merged tests" in instruction.read_text(encoding="utf-8")
            )

        _wait(repo, lesson_landed)
        assert main(["memory", "list", "--all"]) == 0
        listed = capsys.readouterr().out
        assert "check the merged tests" in listed
        assert "\taccepted\t" in listed
    finally:
        _close(repo)


def test_scenario_6_errors_are_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _prepare(tmp_path, "scenario-6", monkeypatch)
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
