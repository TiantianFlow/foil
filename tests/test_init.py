"""Unit and contract tests for deterministic onboarding."""

from __future__ import annotations

import json
import os
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


def _identity_environ() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Foil Test",
            "GIT_AUTHOR_EMAIL": "foil-test@localhost",
            "GIT_COMMITTER_NAME": "Foil Test",
            "GIT_COMMITTER_EMAIL": "foil-test@localhost",
        }
    )
    return environment


def _init_git_repository(path: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_identity_environ(),
    )
    return result.stdout.strip()


def _git_repository_with_user_files(path: Path) -> None:
    _init_git_repository(path)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "--quiet", "-m", "initial")
    (path / "untracked.txt").write_text("untracked\n", encoding="utf-8")


def _project_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.parts
    }


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
    seats_toml = project / ".foil" / "seats.toml"
    assert seats_toml.is_file()
    roster = tomllib.loads(seats_toml.read_text(encoding="utf-8"))
    assert roster["schema_version"] == 1
    assert set(roster["seats"]) == {"lead", "implementer", "reviewer-challenger"}
    assert roster["seats"]["lead"] == {"lead": True, "cli": "grok", "role": "manager"}
    assert roster["seats"]["implementer"] == {"cli": "grok", "role": "implementer"}
    assert roster["seats"]["reviewer-challenger"] == {
        "cli": "opencode",
        "role": "reviewer-challenger",
        "permission": "auto",
    }
    assert roster["seats"]["lead"]["cli"] != roster["seats"]["reviewer-challenger"]["cli"]

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


def test_initialize_project_accepts_an_existing_git_repository(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    head_before = _git(project, "rev-parse", "HEAD")
    branch_before = _git(project, "branch", "--show-current")
    status_before = _git(project, "status", "--porcelain").splitlines()
    state_root = tmp_path / "state"

    result = initialize_project(
        project,
        environ={"FOIL_STATE_DIR": str(state_root)},
        platform="linux",
        home=tmp_path / "home",
    )

    assert result.roles_path == project / ".foil" / "roles"
    assert result.git_branch == branch_before
    assert (result.roles_path / "implementer.toml").is_file()
    assert (project / ".foil" / "seats.toml").is_file()
    assert (state_root / "v1" / "fleets" / result.fleet_id / "seats").is_dir()
    # Every tracked and untracked user file and the Git state are preserved.
    assert (project / "tracked.txt").read_text(encoding="utf-8") == "tracked\n"
    assert (project / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
    assert _git(project, "rev-parse", "HEAD") == head_before
    assert _git(project, "branch", "--show-current") == branch_before
    status_after = _git(project, "status", "--porcelain").splitlines()
    assert sorted(set(status_after) - set(status_before)) == ["?? .foil/"]


def test_initialize_project_uses_the_git_common_directory_for_state(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)

    result = initialize_project(
        project,
        environ={},
        platform="linux",
        home=tmp_path / "home",
    )

    assert result.state_root == (project / ".git" / "foil").resolve()
    assert (result.state_root / "v1" / "fleets" / result.fleet_id).is_dir()


def test_initialize_project_refuses_nonempty_directory_without_git(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    existing = project / "keep.txt"
    existing.write_text("keep", encoding="utf-8")
    state_root = tmp_path / "state"

    with pytest.raises(InitializationError, match="not a Git repository"):
        initialize_project(
            project,
            environ={"FOIL_STATE_DIR": str(state_root)},
            platform="linux",
            home=tmp_path / "home",
        )

    assert existing.read_text(encoding="utf-8") == "keep"
    assert not (project / ".foil").exists()
    assert not state_root.exists()


def test_initialize_project_second_run_fails_closed_without_mutation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    state_root = tmp_path / "state"
    options = {
        "environ": {"FOIL_STATE_DIR": str(state_root)},
        "platform": "linux",
        "home": tmp_path / "home",
    }
    first = initialize_project(project, **options)
    before = _project_files(project)

    with pytest.raises(InitializationError, match="fleet state already exists"):
        initialize_project(project, **options)

    assert _project_files(project) == before
    assert (first.roles_path / "manager.toml").is_file()


def test_initialize_project_rerun_with_identical_scaffold_writes_nothing(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    state_root = tmp_path / "state"
    options = {
        "environ": {"FOIL_STATE_DIR": str(state_root)},
        "platform": "linux",
        "home": tmp_path / "home",
    }
    first = initialize_project(project, **options)
    manager = first.roles_path / "manager.toml"
    original = manager.read_bytes()
    original_mtime = manager.stat().st_mtime_ns
    import shutil

    shutil.rmtree(state_root)

    second = initialize_project(project, **options)

    assert second.fleet_id == first.fleet_id
    assert manager.read_bytes() == original
    assert manager.stat().st_mtime_ns == original_mtime


def test_initialize_project_refuses_conflicting_role_file_without_writes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    first = initialize_project(
        project,
        environ={"FOIL_STATE_DIR": str(tmp_path / "state-a")},
        platform="linux",
        home=tmp_path / "home",
    )
    manager = first.roles_path / "manager.toml"
    manager.write_text("operator-edited\n", encoding="utf-8")
    state_b = tmp_path / "state-b"

    with pytest.raises(InitializationError, match="conflicting"):
        initialize_project(
            project,
            environ={"FOIL_STATE_DIR": str(state_b)},
            platform="linux",
            home=tmp_path / "home",
        )

    assert manager.read_text(encoding="utf-8") == "operator-edited\n"
    assert _project_files(project)[str(manager.relative_to(project))] == b"operator-edited\n"
    assert not state_b.exists()


def test_initialize_project_retry_after_partial_failure_preserves_user_extras(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    extras = project / ".foil" / "roles"
    extras.mkdir(parents=True)
    (project / ".foil" / "notes.md").write_text("user notes\n", encoding="utf-8")
    (extras / "custom.toml").write_text("user role\n", encoding="utf-8")
    state_root = tmp_path / "state"
    options = {
        "environ": {"FOIL_STATE_DIR": str(state_root)},
        "platform": "linux",
        "home": tmp_path / "home",
    }

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected registry failure")

    monkeypatch.setattr("foil.onboarding._initialize_registry", boom)
    with pytest.raises(RuntimeError, match="injected registry failure"):
        initialize_project(project, **options)

    # Rollback removes only Foil-created artifacts, never user .foil extras.
    assert (project / ".foil" / "notes.md").read_text(encoding="utf-8") == "user notes\n"
    assert (extras / "custom.toml").read_text(encoding="utf-8") == "user role\n"
    assert not (extras / "manager.toml").exists()
    assert not state_root.exists()

    monkeypatch.undo()
    result = initialize_project(project, **options)

    assert (result.roles_path / "manager.toml").is_file()
    assert (extras / "custom.toml").read_text(encoding="utf-8") == "user role\n"
    assert (project / ".foil" / "notes.md").read_text(encoding="utf-8") == "user notes\n"
    assert (project / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"


def test_initialize_project_leaves_an_existing_seats_roster_untouched(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    seats = project / ".foil" / "seats.toml"
    seats.parent.mkdir()
    seats.write_text(
        'schema_version = 1\n\n[seats.lead]\ncli = "grok"\nrole = "manager"\n',
        encoding="utf-8",
    )
    original = seats.read_bytes()

    initialize_project(
        project,
        environ={"FOIL_STATE_DIR": str(tmp_path / "state")},
        platform="linux",
        home=tmp_path / "home",
    )

    assert seats.read_bytes() == original


def test_initialize_project_refuses_a_scaffold_path_that_is_a_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    (project / ".foil").write_text("not a directory\n", encoding="utf-8")
    state_root = tmp_path / "state"

    with pytest.raises(InitializationError, match="not a directory"):
        initialize_project(
            project,
            environ={"FOIL_STATE_DIR": str(state_root)},
            platform="linux",
            home=tmp_path / "home",
        )

    assert (project / ".foil").read_text(encoding="utf-8") == "not a directory\n"
    assert not state_root.exists()


def test_initialize_project_refuses_a_symlinked_role_file(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _git_repository_with_user_files(project)
    roles = project / ".foil" / "roles"
    roles.mkdir(parents=True)
    target = tmp_path / "elsewhere.toml"
    target.write_text("elsewhere\n", encoding="utf-8")
    (roles / "manager.toml").symlink_to(target)
    state_root = tmp_path / "state"

    with pytest.raises(InitializationError, match="regular file"):
        initialize_project(
            project,
            environ={"FOIL_STATE_DIR": str(state_root)},
            platform="linux",
            home=tmp_path / "home",
        )

    assert not state_root.exists()
