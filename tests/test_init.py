"""Unit and contract tests for deterministic onboarding."""

from __future__ import annotations

import json
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from foil.fleet import FleetStore
from foil.onboarding import (
    DEFAULT_ROLE_IDS,
    InitializationError,
    initialize_project,
    resolve_state_root,
)
from foil.registry import RegistryStore


def _init_git_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_state_root_explicit_override_precedes_git(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repository(project)
    override = tmp_path / "operator-state"

    resolved = resolve_state_root(
        project,
        environ={"FOIL_STATE_DIR": str(override)},
        platform="linux",
        home=tmp_path / "home",
    )

    assert resolved == override.resolve()


def test_state_root_uses_git_common_directory_for_git_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repository(project)

    resolved = resolve_state_root(project, environ={}, platform="linux", home=tmp_path / "home")

    assert resolved == (project / ".git" / "foil").resolve()


def test_state_root_uses_xdg_state_home_outside_git(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    xdg_state = tmp_path / "xdg-state"

    first = resolve_state_root(
        project,
        environ={"XDG_STATE_HOME": str(xdg_state)},
        platform="linux",
        home=tmp_path / "home",
    )
    second = resolve_state_root(
        project,
        environ={"XDG_STATE_HOME": str(xdg_state)},
        platform="linux",
        home=tmp_path / "home",
    )

    assert first.parent == xdg_state.resolve() / "foil" / "projects"
    assert first == second


@pytest.mark.parametrize(
    ("platform", "relative_parent"),
    [
        ("linux", Path(".local/state/foil/projects")),
        ("darwin", Path("Library/Application Support/Foil/state/projects")),
    ],
)
def test_state_root_platform_fallbacks_are_deterministic(
    tmp_path: Path,
    platform: str,
    relative_parent: Path,
) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    project.mkdir()

    first = resolve_state_root(project, environ={}, platform=platform, home=home)
    second = resolve_state_root(project, environ={}, platform=platform, home=home)

    assert first.parent == home / relative_parent
    assert first == second


def test_state_root_rejects_empty_explicit_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(InitializationError, match="FOIL_STATE_DIR"):
        resolve_state_root(
            project,
            environ={"FOIL_STATE_DIR": ""},
            platform="linux",
            home=tmp_path / "home",
        )


def test_initialize_project_scaffolds_role_library_and_empty_fleet(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"

    result = initialize_project(
        project,
        environ={"FOIL_STATE_DIR": str(state_root)},
        platform="linux",
        home=tmp_path / "home",
    )

    assert result.roles_path == project / ".foil" / "roles"
    assert result.lead_seat_id is None
    assert result.git_branch == "foil-demo"
    assert result.roles == DEFAULT_ROLE_IDS
    assert not (project / ".foil" / "fleet.toml").exists()
    assert not (project / ".foil" / "runtime.toml").exists()

    specializations: set[str] = set()
    for role_id in DEFAULT_ROLE_IDS:
        role = tomllib.loads((result.roles_path / f"{role_id}.toml").read_text(encoding="utf-8"))
        assert role["schema_version"] == 1
        assert role["id"] == role_id
        assert isinstance(role["primary_specialization"], str) and role["primary_specialization"]
        assert "primary_specializations" not in role
        specializations.add(role["primary_specialization"])
    assert len(specializations) == len(DEFAULT_ROLE_IDS)

    expected_fleet_dir = state_root / "v1" / "fleets" / result.fleet_id
    assert result.state_root == state_root.resolve()
    assert (expected_fleet_dir / "seats").is_dir()
    assert (expected_fleet_dir / "locks").is_dir()
    assert RegistryStore(state_root).list_seats(result.fleet_id) == []
    assert FleetStore(state_root).read(result.fleet_id).lead_seat_id is None
    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE((expected_fleet_dir / "seats").stat().st_mode) == 0o700

    fleet_record_path = expected_fleet_dir / "fleet.json"
    fleet_record = json.loads(fleet_record_path.read_text(encoding="utf-8"))
    assert fleet_record["schema_version"] == 1
    assert fleet_record["fleet_id"] == result.fleet_id
    assert fleet_record["state"] == "initialized"
    assert fleet_record["lead_seat_id"] is None
    assert fleet_record["project_root"] == str(project.resolve())
    assert fleet_record["extensions"] == {}
    assert stat.S_IMODE(fleet_record_path.stat().st_mode) == 0o600


def test_initialize_project_refuses_nonempty_directory_without_writes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    existing = project / "keep.txt"
    existing.write_text("keep", encoding="utf-8")
    state_root = tmp_path / "state"

    with pytest.raises(InitializationError, match="empty"):
        initialize_project(
            project,
            environ={"FOIL_STATE_DIR": str(state_root)},
            platform="linux",
            home=tmp_path / "home",
        )

    assert existing.read_text(encoding="utf-8") == "keep"
    assert not (project / ".foil").exists()
    assert not state_root.exists()


def test_initialize_project_refuses_to_overwrite_existing_scaffold(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"
    options = {
        "environ": {"FOIL_STATE_DIR": str(state_root)},
        "platform": "linux",
        "home": tmp_path / "home",
    }
    first = initialize_project(project, **options)
    original = (first.roles_path / "manager.toml").read_bytes()

    with pytest.raises(InitializationError, match="empty"):
        initialize_project(project, **options)

    assert (first.roles_path / "manager.toml").read_bytes() == original
