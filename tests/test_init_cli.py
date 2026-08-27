"""CLI acceptance tests for `foil init`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_foil(
    *args: str,
    cwd: Path,
    state_root: Path,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["FOIL_STATE_DIR"] = str(state_root)
    return subprocess.run(
        [sys.executable, "-m", "foil", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_foil_help_lists_init() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "foil", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "init" in result.stdout
    assert "seat" in result.stdout
    assert "seats" in result.stdout
    assert "resume" in result.stdout
    assert "launch" not in result.stdout


def test_init_help_documents_existing_repositories_and_state_precedence() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "foil", "init", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    lowered = result.stdout.lower()
    assert "empty" in lowered
    assert "existing git repository" in lowered
    assert "accepts only an empty" not in lowered
    assert "must be empty" not in lowered
    assert "foil_state_dir" in lowered
    assert "git common" in lowered
    assert "xdg_state_home" in lowered
    assert "provider" not in lowered
    assert "runtime.toml" not in lowered


def test_init_in_current_empty_directory_emits_resolved_json(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"

    result = run_foil("init", cwd=project, state_root=state_root)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["roles_path"] == str(project / ".foil" / "roles")
    assert payload["state_root"] == str(state_root.resolve())
    assert payload["lead_seat_id"] is None
    assert payload["git_branch"] == "foil-demo"
    assert payload["roles"] == [
        "manager",
        "requirements-owner",
        "domain-designer",
        "implementer",
        "test-verifier",
        "reviewer-challenger",
        "researcher",
        "memory-curator",
    ]
    assert (project / ".foil" / "roles" / "manager.toml").is_file()
    assert (project / ".foil" / "seats.toml").is_file()
    assert not (project / ".foil" / "runtime.toml").exists()
    assert (state_root / "v1" / "fleets" / payload["fleet_id"] / "seats").is_dir()


def test_init_accepts_an_explicit_empty_directory(tmp_path: Path) -> None:
    invocation_directory = tmp_path / "invocation"
    invocation_directory.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"

    result = run_foil("init", str(project), cwd=invocation_directory, state_root=state_root)

    assert result.returncode == 0, result.stderr
    assert (project / ".foil" / "roles" / "implementer.toml").is_file()


def test_init_accepts_an_existing_git_repository(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Foil Test",
            "GIT_AUTHOR_EMAIL": "foil-test@localhost",
            "GIT_COMMITTER_NAME": "Foil Test",
            "GIT_COMMITTER_EMAIL": "foil-test@localhost",
        }
    )
    subprocess.run(
        ["git", "init", "--quiet", "-b", "trunk"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    (project / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "app.py"], cwd=project, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "initial"],
        cwd=project,
        check=True,
        capture_output=True,
        env=environment,
    )
    (project / "untracked.txt").write_text("keep me\n", encoding="utf-8")
    state_root = tmp_path / "state"

    result = run_foil("init", cwd=project, state_root=state_root)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["git_branch"] == "trunk"
    assert (project / ".foil" / "roles" / "implementer.toml").is_file()
    assert (project / "app.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert (project / "untracked.txt").read_text(encoding="utf-8") == "keep me\n"
    assert (state_root / "v1" / "fleets" / payload["fleet_id"] / "seats").is_dir()


def test_init_nonempty_directory_without_git_fails_without_partial_scaffold(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "existing.txt").write_text("keep", encoding="utf-8")
    state_root = tmp_path / "state"

    result = run_foil("init", cwd=project, state_root=state_root)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "empty" in result.stderr.lower()
    assert "git" in result.stderr.lower()
    assert not (project / ".foil").exists()
    assert not state_root.exists()


def test_init_second_run_fails_closed_without_mutating_the_scaffold(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "-b", "trunk"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    state_root = tmp_path / "state"

    first = run_foil("init", cwd=project, state_root=state_root)
    assert first.returncode == 0, first.stderr
    manager = project / ".foil" / "roles" / "manager.toml"
    original = manager.read_bytes()

    second = run_foil("init", cwd=project, state_root=state_root)

    assert second.returncode != 0
    assert "already" in second.stderr.lower()
    assert manager.read_bytes() == original
