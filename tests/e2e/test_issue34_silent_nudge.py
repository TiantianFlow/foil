"""``foil send`` stores the mail and exits 0 whether or not the lead runs it.

A live idle lead is nudged and handles the mail. A lead whose process has
exited, or a killed lead, is not nudged. The mail file is still written.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry
from tests.e2e.test_scenarios import _close, _prepare, _wait

pytestmark = pytest.mark.e2e

_REPORT = "finished the task"


def _arm(repo: Path) -> None:
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "lead.json").write_text(
        """{
  "steps": [
    {"when": {"mail_contains": "finished the task"}, "do": [
      {"write": "board/notes/lead-handled.txt", "text": "handled\\n"}
    ]}
  ]
}
""",
        encoding="utf-8",
    )


def _ready(name: str, capsys: pytest.CaptureFixture[str]) -> bool:
    if main(["seat", "peek", name]) != 0:
        capsys.readouterr()
        return False
    return f"foil-fake {name} ready" in capsys.readouterr().out


def _listed(capsys: pytest.CaptureFixture[str]) -> str:
    assert main(["seat", "list"]) == 0
    return capsys.readouterr().out


def _handled(repo: Path) -> Path:
    return foil_root(repo) / "board" / "notes" / "lead-handled.txt"


def _mail(repo: Path) -> Path:
    found = [
        path
        for path in (foil_root(repo) / "board" / "mail" / "lead").glob("*.md")
        if _REPORT in path.read_text(encoding="utf-8")
    ]
    assert len(found) == 1
    return found[0]


def _widen(repo: Path) -> None:
    window = load_registry(repo)["seats"]["lead"]["window_id"]
    resized = subprocess.run(
        ["tmux", "resize-window", "-t", window, "-x", "400"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert resized.returncode == 0, resized.stderr


def _send(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    try:
        assert main(["send", "lead", _REPORT]) == 0
    finally:
        monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    sent = capsys.readouterr()
    assert sent.out == ""
    assert sent.err == ""


def _stays_absent(path: Path) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        assert not path.exists()
        time.sleep(0.2)


def _spawn(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _arm(repo)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    _wait(repo, lambda: _ready("lead", capsys) and _ready("implementer-1", capsys))


def _stop_lead_process(repo: Path) -> None:
    window = load_registry(repo)["seats"]["lead"]["window_id"]
    listed = subprocess.run(
        ["tmux", "list-panes", "-t", window, "-F", "#{pane_pid}"],
        check=False,
        capture_output=True,
        text=True,
    )
    lines = [line for line in listed.stdout.splitlines() if line.strip()]
    assert lines and lines[0].isdigit(), listed.stdout
    os.kill(int(lines[0]), signal.SIGKILL)


def test_a_live_lead_runs_the_workers_completion_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Control: an idle live lead is nudged and handles the mail."""

    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _spawn(repo, capsys)
        _widen(repo)
        _send(monkeypatch, capsys)
        mail = _mail(repo)
        text = mail.read_text(encoding="utf-8")
        assert "from: implementer-1\n" in text
        assert "to: lead\n" in text
        assert main(["mail", "read", str(mail.resolve())]) == 0
        assert capsys.readouterr().out == f"{_REPORT}\n"
        handled = _handled(repo)

        def landed() -> bool:
            return handled.is_file() and handled.read_text(encoding="utf-8") == "handled\n"

        _wait(repo, landed)
        assert main(["seat", "peek", "lead"]) == 0
        assert str(mail.resolve()) in capsys.readouterr().out
        assert "lead\tlead\talive\t" in _listed(capsys)
        assert "implementer-1\timplementer\talive\t" in _listed(capsys)
    finally:
        _close(repo)


def test_a_dead_lead_pane_never_runs_the_completion_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The lead process has exited and its window remains. send still exits 0."""

    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _spawn(repo, capsys)
        _widen(repo)
        _stop_lead_process(repo)

        def dead() -> bool:
            if "lead\tlead\tdead\t" not in _listed(capsys):
                return False
            if main(["seat", "peek", "lead"]) != 0:
                capsys.readouterr()
                return False
            return "Pane is dead" in capsys.readouterr().out

        _wait(repo, dead)
        _send(monkeypatch, capsys)
        mail = _mail(repo)
        assert "from: implementer-1\n" in mail.read_text(encoding="utf-8")
        assert main(["mail", "read", str(mail.resolve())]) == 0
        assert capsys.readouterr().out == f"{_REPORT}\n"
        _stays_absent(_handled(repo))
        assert main(["seat", "peek", "lead"]) == 0
        pane = capsys.readouterr().out
        assert "foil-fake lead ready" in pane
        assert "Pane is dead" in pane
        assert str(mail.resolve()) not in pane
        listed = _listed(capsys)
        assert "lead\tlead\tdead\t" in listed
        assert "implementer-1\timplementer\talive\t" in listed
    finally:
        _close(repo)


def test_a_killed_lead_never_runs_the_completion_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Killing the lead removes its window. send still exits 0 and the script does not run."""

    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        _spawn(repo, capsys)
        assert main(["seat", "kill", "lead"]) == 0
        killed = capsys.readouterr()
        assert killed.out == ""
        assert killed.err == ""
        assert "lead\tlead\tkilled\t" in _listed(capsys)
        assert main(["seat", "peek", "lead"]) == 1
        assert capsys.readouterr().err == "foil: seat 'lead' is killed\n"
        _send(monkeypatch, capsys)
        mail = _mail(repo)
        assert "from: implementer-1\n" in mail.read_text(encoding="utf-8")
        assert main(["mail", "read", str(mail.resolve())]) == 0
        assert capsys.readouterr().out == f"{_REPORT}\n"
        _stays_absent(_handled(repo))
        assert main(["seat", "peek", "lead"]) == 1
        assert capsys.readouterr().err == "foil: seat 'lead' is killed\n"
        listed = _listed(capsys)
        assert "lead\tlead\tkilled\t" in listed
        assert "implementer-1\timplementer\talive\t" in listed
    finally:
        _close(repo)
