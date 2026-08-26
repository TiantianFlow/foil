"""Project initialization and deterministic state-root resolution (CAP-016, CAP-025)."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from foil.fleet import FleetRecord, FleetStore
from foil.registry import SCHEMA_VERSION, _ensure_private_directory

DEFAULT_ROLE_IDS = (
    "manager",
    "requirements-owner",
    "domain-designer",
    "implementer",
    "test-verifier",
    "reviewer-challenger",
    "researcher",
    "memory-curator",
)


class InitializationError(ValueError):
    """The requested project scaffold or state location is invalid."""


@dataclass(frozen=True, slots=True)
class InitializationResult:
    project_root: Path
    roles_path: Path
    state_root: Path
    fleet_id: str
    lead_seat_id: str | None = None
    git_branch: str = "foil-demo"
    roles: tuple[str, ...] = DEFAULT_ROLE_IDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "fleet_id": self.fleet_id,
            "git_branch": self.git_branch,
            "lead_seat_id": self.lead_seat_id,
            "project_root": str(self.project_root),
            "roles": list(self.roles),
            "roles_path": str(self.roles_path),
            "state_root": str(self.state_root),
        }


def _absolute_environment_path(value: str, variable_name: str) -> Path:
    if not value:
        raise InitializationError(f"{variable_name} is explicitly set but empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise InitializationError(f"{variable_name} must be an absolute path")
    return path.resolve()


def _git_common_directory(project_root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value:
        return None
    common_directory = Path(value)
    if not common_directory.is_absolute():
        common_directory = project_root / common_directory
    return common_directory.resolve()


def _project_id(project_root: Path) -> str:
    digest = hashlib.sha256(os.fsencode(project_root)).hexdigest()[:16]
    return f"project-{digest}"


def resolve_state_root(
    project_root: Path | str,
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | str | None = None,
) -> Path:
    """Resolve CAP-016 state precedence without creating filesystem entries."""

    project = Path(project_root).expanduser().resolve()
    environment = os.environ if environ is None else environ
    current_platform = sys.platform if platform is None else platform
    home_directory = Path.home() if home is None else Path(home).expanduser()

    if "FOIL_STATE_DIR" in environment:
        return _absolute_environment_path(environment["FOIL_STATE_DIR"], "FOIL_STATE_DIR")

    git_common_directory = _git_common_directory(project)
    if git_common_directory is not None:
        return git_common_directory / "foil"

    if "XDG_STATE_HOME" in environment:
        base = _absolute_environment_path(environment["XDG_STATE_HOME"], "XDG_STATE_HOME")
        return (base / "foil" / "projects" / _project_id(project)).resolve()
    elif current_platform == "darwin":
        base = home_directory / "Library" / "Application Support" / "Foil" / "state"
    else:
        base = home_directory / ".local" / "state"
        base = base / "foil"
    return (base / "projects" / _project_id(project)).resolve()


def _load_role_templates() -> dict[str, str]:
    roles_root = resources.files("foil.templates").joinpath("roles")
    templates: dict[str, str] = {}
    for role_id in DEFAULT_ROLE_IDS:
        resource = roles_root.joinpath(f"{role_id}.toml")
        if not resource.is_file():
            raise InitializationError(f"packaged role template is missing: {role_id}")
        templates[role_id] = resource.read_text(encoding="utf-8")
    return templates


def _validate_role_library(role_templates: Mapping[str, str]) -> None:
    specializations: set[str] = set()
    for role_id, text in role_templates.items():
        try:
            role = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise InitializationError(f"packaged role is malformed: {role_id}") from exc
        specialization = role.get("primary_specialization")
        if (
            role.get("schema_version") != SCHEMA_VERSION
            or role.get("id") != role_id
            or not isinstance(specialization, str)
            or not specialization
            or "primary_specializations" in role
        ):
            raise InitializationError(f"packaged role is invalid: {role_id}")
        if specialization in specializations:
            raise InitializationError(f"role library reuses specialization: {specialization}")
        specializations.add(specialization)


@dataclass(slots=True)
class _ScaffoldWrites:
    """Artifacts one init attempt created; rollback never deletes user files."""

    roles_path: Path
    files: list[Path] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)

    def rollback(self) -> None:
        for path in self.files:
            with suppress(OSError):
                path.unlink()
        for directory in reversed(self.directories):
            with suppress(OSError):
                directory.rmdir()


def _existing_directory(path: Path, description: str) -> bool:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISDIR(file_stat.st_mode):
        raise InitializationError(f"{description} exists but is not a directory: {path}")
    return True


def _write_role_library(project_root: Path) -> _ScaffoldWrites:
    """Create only missing Foil files after validating every collision first.

    Existing role files with identical packaged content are left untouched;
    conflicting content or unsafe paths fail closed before any write. On a
    partial failure only artifacts this call created are rolled back, never
    user-owned `.foil` extras or the project tree.
    """

    role_templates = _load_role_templates()
    _validate_role_library(role_templates)
    scaffold = project_root / ".foil"
    roles_directory = scaffold / "roles"
    writes = _ScaffoldWrites(roles_path=roles_directory)

    scaffold_exists = _existing_directory(scaffold, "Foil scaffold")
    roles_exist = (
        _existing_directory(roles_directory, "Foil role library")
        if scaffold_exists
        else False
    )
    for role_id, role_text in role_templates.items():
        role_path = roles_directory / f"{role_id}.toml"
        try:
            file_stat = role_path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise InitializationError(f"role file is not a regular file: {role_path}")
        try:
            existing = role_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise InitializationError(f"cannot read role file: {role_path}") from exc
        if existing != role_text:
            raise InitializationError(
                f"role file already exists with conflicting content: {role_path}. "
                "Reconcile or remove it before rerunning `foil init`."
            )

    try:
        if not scaffold_exists:
            scaffold.mkdir(mode=0o755)
            writes.directories.append(scaffold)
        if not roles_exist:
            roles_directory.mkdir(mode=0o755)
            writes.directories.append(roles_directory)
        for role_id, role_text in role_templates.items():
            role_path = roles_directory / f"{role_id}.toml"
            if role_path.exists():
                continue
            role_path.write_text(role_text, encoding="utf-8")
            role_path.chmod(0o644)
            writes.files.append(role_path)
    except Exception:
        writes.rollback()
        raise
    return writes


def _initialize_registry(state_root: Path, fleet_id: str, project_root: Path) -> Path:
    _ensure_private_directory(state_root)
    version_root = state_root / f"v{SCHEMA_VERSION}"
    fleet_root = version_root / "fleets" / fleet_id
    if fleet_root.exists():
        raise InitializationError(f"fleet state already exists: {fleet_id}")
    layout = (
        version_root,
        version_root / "fleets",
        fleet_root,
        fleet_root / "seats",
        fleet_root / "locks",
        fleet_root / "notepads",
        fleet_root / "memory",
    )
    for path in layout:
        _ensure_private_directory(path)

    updated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return FleetStore(state_root).write(
        FleetRecord(
            fleet_id=fleet_id,
            project_root=str(project_root),
            updated_at=updated_at,
            display_name="Foil fleet",
            lead_seat_id=None,
            state="initialized",
        )
    )


def _observed_branch(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def initialize_project(
    project_root: Path | str = Path("."),
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | str | None = None,
) -> InitializationResult:
    """Scaffold a project and initialize its versioned seat registry.

    Accepts an empty directory or an existing Git repository. Existing
    tracked and untracked user files and Git state are preserved; a
    non-empty directory outside Git and conflicting Foil/path/state
    collisions fail closed with actionable errors before any write.
    """

    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise InitializationError(f"project directory does not exist: {project}")
    non_empty = any(project.iterdir())
    if non_empty and _git_common_directory(project) is None:
        raise InitializationError(
            f"project directory is not empty and is not a Git repository: {project}. "
            "Initialize Git first (for example `git init -b foil-demo`) or choose "
            "an empty directory."
        )

    state_root = resolve_state_root(
        project,
        environ=environ,
        platform=platform,
        home=home,
    )
    fleet_id = f"starter-{hashlib.sha256(os.fsencode(project)).hexdigest()[:12]}"
    fleet_root = state_root / f"v{SCHEMA_VERSION}" / "fleets" / fleet_id
    if fleet_root.exists():
        raise InitializationError(
            f"fleet state already exists for this project: {fleet_root}. "
            "This project is already initialized; use the existing state or "
            "remove it only when you intend a fresh fleet."
        )
    writes = _write_role_library(project)
    try:
        _initialize_registry(state_root, fleet_id, project)
    except Exception:
        writes.rollback()
        raise
    return InitializationResult(
        project_root=project,
        roles_path=writes.roles_path,
        state_root=state_root,
        fleet_id=fleet_id,
        git_branch=_observed_branch(project) or "foil-demo",
    )
