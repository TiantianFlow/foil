"""Project initialization and deterministic state-root resolution (CAP-016, CAP-025)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from foil.registry import SCHEMA_VERSION, _atomic_write_json, _ensure_private_directory
from foil.runtime_config import ConfigError, load_fleet_config

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
    config_path: Path
    runtime_config_path: Path
    state_root: Path
    fleet_id: str
    git_branch: str
    roles: tuple[str, ...] = DEFAULT_ROLE_IDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "fleet_id": self.fleet_id,
            "git_branch": self.git_branch,
            "roles": list(self.roles),
            "runtime_config_path": str(self.runtime_config_path),
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


def _validate_templates(fleet_text: str, role_templates: Mapping[str, str]) -> None:
    try:
        fleet = tomllib.loads(fleet_text)
        roles = {role_id: tomllib.loads(text) for role_id, text in role_templates.items()}
    except tomllib.TOMLDecodeError as exc:
        raise InitializationError("packaged onboarding template is malformed") from exc

    if fleet.get("schema_version") != SCHEMA_VERSION:
        raise InitializationError("packaged fleet template has an unsupported schema version")
    pools = fleet.get("usage_pools")
    seats = fleet.get("seats")
    if not isinstance(pools, list) or len({pool.get("id") for pool in pools}) < 2:
        raise InitializationError("packaged fleet template requires at least two usage pools")
    if not isinstance(seats, list) or [seat.get("id") for seat in seats] != list(DEFAULT_ROLE_IDS):
        raise InitializationError("packaged fleet template does not contain the default role set")

    specializations: dict[str, str] = {}
    for seat in seats:
        seat_id = seat["id"]
        specialization = seat.get("primary_specialization")
        if not isinstance(specialization, str) or not specialization:
            raise InitializationError(f"default seat has no primary specialization: {seat_id}")
        if "specializations" in seat:
            raise InitializationError(f"default seat has multiple specializations: {seat_id}")
        specializations[seat_id] = specialization
        role = roles.get(seat_id)
        if (
            role is None
            or role.get("schema_version") != SCHEMA_VERSION
            or role.get("id") != seat_id
            or role.get("primary_specialization") != specialization
            or "primary_specializations" in role
        ):
            raise InitializationError(f"packaged role does not match its default seat: {seat_id}")

    for seat in seats:
        for target in seat.get("challenges", []):
            if target not in specializations:
                raise InitializationError(f"challenge target does not exist: {target}")
            if specializations[target] == seat["primary_specialization"]:
                raise InitializationError(f"challenge target has the same specialization: {target}")


def _write_scaffold(project_root: Path, fleet_id: str) -> tuple[Path, Path, str]:
    template_root = resources.files("foil.templates")
    fleet_resource = template_root.joinpath("fleet.toml")
    if not fleet_resource.is_file():
        raise InitializationError("packaged fleet template is missing")
    runtime_resource = template_root.joinpath("runtime.toml")
    if not runtime_resource.is_file():
        raise InitializationError("packaged runtime template is missing")
    fleet_text = fleet_resource.read_text(encoding="utf-8").replace("__FLEET_ID__", fleet_id)
    implementer_worktree = json.dumps(
        str(project_root / "worktrees" / "implementer"),
        ensure_ascii=False,
    )
    reviewer_worktree = json.dumps(
        str(project_root / "worktrees" / "reviewer-challenger"),
        ensure_ascii=False,
    )
    runtime_text = (
        runtime_resource.read_text(encoding="utf-8")
        .replace("__FLEET_ID__", fleet_id)
        .replace(
            "__PROJECT_ROOT_TOML__",
            json.dumps(str(project_root), ensure_ascii=False),
        )
        .replace("__IMPLEMENTER_WORKTREE_TOML__", implementer_worktree)
        .replace("__REVIEWER_WORKTREE_TOML__", reviewer_worktree)
    )
    role_templates = _load_role_templates()
    _validate_templates(fleet_text, role_templates)

    scaffold = project_root / ".foil"
    scaffold.mkdir(mode=0o755)
    try:
        roles_directory = scaffold / "roles"
        roles_directory.mkdir(mode=0o755)
        config_path = scaffold / "fleet.toml"
        config_path.write_text(fleet_text, encoding="utf-8")
        config_path.chmod(0o644)
        runtime_config_path = scaffold / "runtime.toml"
        runtime_config_path.write_text(runtime_text, encoding="utf-8")
        runtime_config_path.chmod(0o644)
        for role_id, role_text in role_templates.items():
            role_path = roles_directory / f"{role_id}.toml"
            role_path.write_text(role_text, encoding="utf-8")
            role_path.chmod(0o644)
        try:
            runtime_config = load_fleet_config(runtime_config_path)
        except ConfigError as exc:
            raise InitializationError("packaged runtime template is invalid") from exc
        if runtime_config.fleet_id != fleet_id:
            raise InitializationError("packaged runtime template has the wrong fleet ID")
        git_branches = {seat.git_branch for seat in runtime_config.seats}
        if len(git_branches) != 1:
            raise InitializationError("packaged runtime template requires one Git branch")
    except Exception:
        shutil.rmtree(scaffold, ignore_errors=True)
        raise
    return config_path, runtime_config_path, git_branches.pop()


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
    return _atomic_write_json(
        fleet_root / "fleet.json",
        {
            "schema_version": SCHEMA_VERSION,
            "fleet_id": fleet_id,
            "state": "initialized",
            "project_root": str(project_root),
            "updated_at": updated_at,
            "extensions": {},
        },
    )


def initialize_project(
    project_root: Path | str = Path("."),
    *,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | str | None = None,
) -> InitializationResult:
    """Scaffold an empty project and initialize its versioned seat registry."""

    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise InitializationError(f"project directory does not exist: {project}")
    if any(project.iterdir()):
        raise InitializationError(f"project directory must be empty: {project}")

    state_root = resolve_state_root(
        project,
        environ=environ,
        platform=platform,
        home=home,
    )
    fleet_id = f"starter-{hashlib.sha256(os.fsencode(project)).hexdigest()[:12]}"
    config_path, runtime_config_path, git_branch = _write_scaffold(project, fleet_id)
    try:
        _initialize_registry(state_root, fleet_id, project)
    except Exception:
        shutil.rmtree(config_path.parent, ignore_errors=True)
        raise
    return InitializationResult(
        config_path=config_path,
        runtime_config_path=runtime_config_path,
        state_root=state_root,
        fleet_id=fleet_id,
        git_branch=git_branch,
    )
