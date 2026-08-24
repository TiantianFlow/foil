"""CLI acceptance tests for `foil init` (CAP-001, CAP-016, CAP-025–CAP-028)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
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


def test_init_help_documents_empty_directory_and_state_precedence() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "foil", "init", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    lowered = result.stdout.lower()
    assert "empty" in lowered
    assert "foil_state_dir" in lowered
    assert "git common" in lowered
    assert "xdg_state_home" in lowered
    assert "provider" not in lowered


def test_init_in_current_empty_directory_emits_resolved_json(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"

    result = run_foil("init", cwd=project, state_root=state_root)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["config_path"] == str(project / ".foil" / "fleet.toml")
    assert payload["state_root"] == str(state_root.resolve())
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

    config = tomllib.loads((project / ".foil" / "fleet.toml").read_text(encoding="utf-8"))
    assert payload["fleet_id"] == config["fleet_id"]
    assert (state_root / "v1" / "fleets" / payload["fleet_id"] / "seats").is_dir()


def test_init_accepts_an_explicit_empty_directory(tmp_path: Path) -> None:
    invocation_directory = tmp_path / "invocation"
    invocation_directory.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"

    result = run_foil("init", str(project), cwd=invocation_directory, state_root=state_root)

    assert result.returncode == 0, result.stderr
    assert (project / ".foil" / "fleet.toml").is_file()


def test_init_nonempty_directory_fails_without_partial_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "existing.txt").write_text("keep", encoding="utf-8")
    state_root = tmp_path / "state"

    result = run_foil("init", cwd=project, state_root=state_root)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "empty" in result.stderr.lower()
    assert not (project / ".foil").exists()
    assert not state_root.exists()
