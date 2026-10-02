"""A startup dialog must not see the task typed into the new pane."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry

pytestmark = pytest.mark.e2e


def _git(repo: Path, *args: str) -> None:
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_task_mail_does_not_type_into_a_startup_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    monkeypatch.chdir(repo)
    assert main(["init"]) == 0
    templates = foil_root(repo) / "templates"
    for role in ("lead", "implementer", "reviewer"):
        path = templates / f"{role}.toml"
        text = re.sub(
            r'(?m)^harness = ".*"$',
            'harness = "fake"',
            path.read_text(encoding="utf-8"),
            count=1,
        )
        path.write_text(text, encoding="utf-8")
    fake = foil_root(repo) / "run" / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    (fake / "lead.json").write_text(
        json.dumps(
            {
                "exit_on_input": True,
                "steps": [
                    {
                        "when": {"mail_contains": "write the marker"},
                        "do": [{"write": "board/notes/done.txt", "text": "done\n"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    try:
        assert main(["seat", "spawn", "lead", "--task", "write the marker"]) == 0
        marker = foil_root(repo) / "board" / "notes" / "done.txt"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not marker.is_file():
            time.sleep(0.1)
        assert marker.is_file()
        assert marker.read_text(encoding="utf-8") == "done\n"
        assert main(["seat", "list"]) == 0
        assert "lead\tlead\talive\t" in capsys.readouterr().out
    finally:
        session = load_registry(repo).get("tmux_session") or ""
        if session:
            subprocess.run(
                ["tmux", "kill-session", "-t", session],
                check=False,
                capture_output=True,
            )
