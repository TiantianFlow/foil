"""Doctor prerequisite and worktree reporting (CAP-026, CAP-024)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from foil.doctor import doctor_report
from foil.runtime_config import load_fleet_config


def _git(cwd: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment.setdefault("GIT_AUTHOR_NAME", "Foil")
    environment.setdefault("GIT_AUTHOR_EMAIL", "foil@localhost")
    environment.setdefault("GIT_COMMITTER_NAME", "Foil")
    environment.setdefault("GIT_COMMITTER_EMAIL", "foil@localhost")
    subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )


def test_doctor_reports_missing_worktrees_until_apply(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    environment = os.environ.copy()
    environment["FOIL_STATE_DIR"] = str(tmp_path / "state")
    initialized = subprocess.run(
        [sys.executable, "-m", "foil", "init", "."],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert initialized.returncode == 0, initialized.stderr
    _git(project, "init", "--quiet", "-b", "foil-demo")
    config = load_fleet_config(project / ".foil" / "runtime.toml")

    dry = doctor_report(config, apply=False)
    assert dry["worktrees"]["ok"] is False
    assert all(not seat["exists"] for seat in dry["worktrees"]["seats"])

    applied = doctor_report(config, apply=True)
    assert applied["worktrees"]["ok"] is True
    assert all(seat["exists"] and not seat["stale"] for seat in applied["worktrees"]["seats"])

    (project / "later.txt").write_text("after clone", encoding="utf-8")
    _git(project, "add", "later.txt")
    _git(project, "commit", "--quiet", "-m", "after clone")
    stale = doctor_report(config, apply=False)
    assert stale["worktrees"]["ok"] is False
    assert any(seat["stale"] for seat in stale["worktrees"]["seats"])
