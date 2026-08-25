"""Prerequisite discovery for a live fleet (CAP-026)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from foil.fleet import FleetStore
from foil.registry import RegistryStore

GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "Foil",
    "GIT_AUTHOR_EMAIL": "foil@localhost",
    "GIT_COMMITTER_NAME": "Foil",
    "GIT_COMMITTER_EMAIL": "foil@localhost",
}

HOST_CLIS = ("grok", "opencode")


class DoctorError(ValueError):
    """Prerequisite discovery or worktree preparation failed."""


def doctor_report(
    state_root: Path | str,
    fleet_id: str,
    *,
    apply: bool = False,
) -> dict[str, Any]:
    fleet = FleetStore(state_root).read(fleet_id)
    records = RegistryStore(state_root).list_seats(fleet_id)
    tmux = shutil.which("tmux")
    git = shutil.which("git")
    clis: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in (*HOST_CLIS, *_seat_cli_names(records)):
        if name in seen:
            continue
        seen.add(name)
        resolved = shutil.which(name)
        clis.append({"name": name, "ok": resolved is not None, "path": resolved})
    if apply:
        ensure_registered_worktrees(Path(fleet.project_root), records)
    return {
        "tmux": {"ok": tmux is not None, "path": tmux},
        "git": {"ok": git is not None, "path": git},
        "clis": clis,
        "credentials_inspected": False,
        "worktrees": _worktree_status(Path(fleet.project_root), records),
        "plan": {
            "fleet_id": fleet.fleet_id,
            "lead_seat_id": fleet.lead_seat_id,
            "seats": [
                {
                    "seat_id": record.seat_id,
                    "worktree_path": record.worktree_path,
                    "working_directory": record.working_directory,
                    "cli": (record.extensions.get("profile") or {}).get("cli"),
                    "is_lead": record.seat_id == fleet.lead_seat_id,
                }
                for record in records
            ],
        },
    }


def ensure_isolated_worktree(project_root: Path, destination: Path) -> None:
    """Clone the hosting project into an independent seat worktree."""

    git_root = git_toplevel(project_root)
    if git_root is None:
        raise DoctorError("working directory is not a valid Git worktree")
    _ensure_initial_commit(git_root)
    if destination.resolve() == git_root.resolve():
        return
    if destination.is_dir():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--local", "--quiet", str(git_root), str(destination)],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DoctorError("could not create independent worktree") from exc


def ensure_registered_worktrees(project_root: Path, records) -> None:
    """Create missing isolated clones for registered seats."""

    git_root = git_toplevel(project_root)
    if git_root is None:
        raise DoctorError("working directory is not a valid Git worktree")
    _ensure_initial_commit(git_root)
    for record in records:
        path = Path(record.worktree_path)
        if path.resolve() == git_root.resolve():
            continue
        ensure_isolated_worktree(git_root, path)


def git_toplevel(start: Path) -> Path | None:
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
                return Path(result.stdout.strip()).resolve()
        if probe.parent == probe:
            break
        probe = probe.parent
    return None


def _seat_cli_names(records) -> list[str]:
    names: list[str] = []
    for record in records:
        profile = record.extensions.get("profile") or {}
        cli = profile.get("cli")
        if isinstance(cli, str) and cli:
            names.append(cli)
    return names


def _worktree_status(project_root: Path, records) -> dict[str, Any]:
    git_root = git_toplevel(project_root)
    project_head = _git_head(git_root) if git_root is not None else None
    seats: list[dict[str, Any]] = []
    ok = True
    for record in records:
        path = Path(record.worktree_path)
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
        isolated = not same_root
        ready = exists and (same_root or (tree_head is not None and not stale))
        if isolated and not ready:
            ok = False
        seats.append(
            {
                "seat_id": record.seat_id,
                "path": str(path),
                "exists": exists,
                "stale": stale,
                "isolated": isolated,
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


def _git_env() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(GIT_IDENTITY)
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
