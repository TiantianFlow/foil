"""Prerequisite discovery and dry-run launch plan (CAP-026)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from foil.adapters import AdapterError, load_adapter, load_builtin_adapter
from foil.runtime_config import FleetConfig, SeatConfig, UsagePoolConfig

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Foil",
    "GIT_AUTHOR_EMAIL": "foil@localhost",
    "GIT_COMMITTER_NAME": "Foil",
    "GIT_COMMITTER_EMAIL": "foil@localhost",
}


class DoctorError(ValueError):
    """Prerequisite discovery or worktree preparation failed."""


def doctor_report(config: FleetConfig, *, apply: bool = False) -> dict[str, Any]:
    tmux = shutil.which("tmux")
    git = shutil.which("git")
    clis: list[dict[str, Any]] = []
    seen: set[str] = set()
    for seat in config.seats:
        pool = config.pool_for(seat)
        for name in _cli_names(config, seat, pool):
            if name in seen:
                continue
            seen.add(name)
            resolved = shutil.which(name)
            clis.append({"name": name, "ok": resolved is not None, "path": resolved})
    if apply:
        ensure_worktrees(config)
    return {
        "tmux": {"ok": tmux is not None, "path": tmux},
        "git": {"ok": git is not None, "path": git},
        "clis": clis,
        "credentials_inspected": False,
        "worktrees": _worktree_status(config),
        "plan": {
            "fleet_id": config.fleet_id,
            "seats": [
                {
                    "seat_id": seat.seat_id,
                    "worktree_path": str(seat.worktree_path),
                    "working_directory": str(seat.working_directory),
                    "usage_pool_id": seat.usage_pool_id,
                    "git_branch": seat.git_branch,
                }
                for seat in config.seats
            ],
        },
    }


def ensure_worktrees(config: FleetConfig) -> None:
    """Create independent local clones for seats whose worktree is not the Git root."""

    git_root = _git_toplevel_for_config(config)
    if git_root is None:
        raise DoctorError("working directory is not a valid Git worktree")
    _ensure_initial_commit(git_root)
    for seat in config.seats:
        path = seat.worktree_path
        if path.resolve() == git_root.resolve():
            continue
        if path.is_dir():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                ["git", "clone", "--local", "--quiet", str(git_root), str(path)],
                check=True,
                capture_output=True,
                text=True,
                env=_git_env(),
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise DoctorError(f"could not create independent worktree for {seat.seat_id}") from exc


def _cli_names(config: FleetConfig, seat: SeatConfig, pool: UsagePoolConfig) -> list[str]:
    names: list[str] = []
    if seat.cli:
        names.append(seat.cli)
    adapter = _load_pool_adapter(config, pool)
    if adapter is not None:
        names.extend(adapter.executable.candidates)
    return names


def _load_pool_adapter(config: FleetConfig, pool: UsagePoolConfig):
    if not pool.adapter_id:
        return None
    for directory in config.adapter_paths:
        for candidate in sorted(directory.glob("*.toml")):
            try:
                record = load_adapter(candidate)
            except AdapterError:
                continue
            if record.adapter_id == pool.adapter_id:
                return record
    try:
        return load_builtin_adapter(pool.adapter_id)
    except AdapterError:
        return None


def _worktree_status(config: FleetConfig) -> dict[str, Any]:
    git_root = _git_toplevel_for_config(config)
    project_head = _git_head(git_root) if git_root is not None else None
    seats: list[dict[str, Any]] = []
    ok = True
    for seat in config.seats:
        path = seat.worktree_path
        exists = path.is_dir()
        tree_head = _git_head(path) if exists else None
        same_root = (
            git_root is not None and exists and path.resolve() == git_root.resolve()
        )
        stale = (
            not same_root
            and project_head is not None
            and tree_head is not None
            and tree_head != project_head
        )
        ready = exists and tree_head is not None and not stale
        if not ready:
            ok = False
        seats.append(
            {
                "seat_id": seat.seat_id,
                "path": str(path),
                "exists": exists,
                "stale": stale,
            }
        )
    return {"ok": ok, "seats": seats}


def _git_head(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def _git_toplevel_for_config(config: FleetConfig) -> Path | None:
    """Prefer the hosting project repo over an already-cloned seat worktree."""

    seat_roots = {seat.worktree_path.resolve() for seat in config.seats}
    fallback: Path | None = None
    for seat in config.seats:
        for start in (seat.worktree_path, seat.working_directory):
            found = _git_toplevel(start)
            if found is None:
                continue
            if fallback is None:
                fallback = found
            resolved = found.resolve()
            if resolved not in seat_roots:
                return resolved
            ancestor = _git_toplevel(found.parent)
            if ancestor is not None and ancestor.resolve() not in seat_roots:
                return ancestor.resolve()
    return fallback


def _git_toplevel(start: Path) -> Path | None:
    probe = start
    seen: set[Path] = set()
    while probe not in seen:
        seen.add(probe)
        git_dir = probe / ".git"
        if git_dir.exists():
            result = subprocess.run(
                ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
        if probe.parent == probe:
            break
        probe = probe.parent
    return None


def _git_env() -> dict[str, str]:
    environment = os.environ.copy()
    for key, value in GIT_IDENTITY.items():
        environment.setdefault(key, value)
    return environment


def _ensure_initial_commit(git_root: Path) -> None:
    head = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode == 0:
        return
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(git_root),
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "foil identity",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DoctorError("working directory is not a valid Git worktree") from exc
