"""Prerequisite discovery for a live fleet (CAP-026)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from foil.fleet import FleetStore
from foil.registry import RegistryStore
from foil.tmux import TmuxController

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
        "reconciliation": _reconciliation(Path(state_root), fleet, records),
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
    if not destination.is_dir():
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
    exclude_git_pattern(git_root, "/worktrees/")
    exclude_git_pattern(destination, "/FOIL.md")


def exclude_git_pattern(repo: Path, pattern: str) -> None:
    """Append a private exclude without editing a tracked .gitignore."""

    try:
        listed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "info/exclude"],
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise DoctorError("could not update Git exclude") from exc
    raw = listed.stdout.strip()
    if not raw:
        raise DoctorError("could not update Git exclude")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if pattern in current.splitlines():
        return
    prefix = "" if not current or current.endswith("\n") else "\n"
    path.write_text(f"{current}{prefix}{pattern}\n", encoding="utf-8")


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


def _reconciliation(state_root: Path, fleet, records) -> dict[str, Any]:
    registered = {record.seat_id for record in records}
    orphan_tmux: list[dict[str, str]] = []
    missing_tmux: list[str] = []
    try:
        controller = TmuxController()
        marked = controller.list_marked_windows(fleet.fleet_id)
    except Exception:
        marked = []
    for window in marked:
        if window["seat_id"] not in registered:
            orphan_tmux.append(window)
    for record in records:
        probe = None
        try:
            probe = TmuxController().probe(fleet.fleet_id, record.seat_id, record.tmux)
        except Exception:
            probe = None
        if probe is None or probe.state.value == "dead":
            missing_tmux.append(record.seat_id)
    status_dir = (
        state_root / "v1" / "fleets" / fleet.fleet_id / "status" / "seats"
    )
    stale_status = [
        path.stem
        for path in sorted(status_dir.glob("*.json"))
        if path.stem not in registered
    ]
    obsolete_plans = _unknown_names(
        state_root / "v1" / "fleets" / fleet.fleet_id / "runner-plans",
        registered,
        suffix=".json",
    )
    obsolete_bootstrap = _unknown_names(
        state_root / "v1" / "fleets" / fleet.fleet_id / "adapter-state",
        registered,
        directories=True,
    )
    retained_worktrees = []
    worktrees = Path(fleet.project_root) / "worktrees"
    if worktrees.is_dir():
        for path in sorted(worktrees.iterdir()):
            if path.is_dir() and path.name not in registered:
                retained_worktrees.append(str(path))
    return {
        "orphan_tmux": orphan_tmux,
        "missing_tmux": missing_tmux,
        "stale_status": stale_status,
        "obsolete_plans": obsolete_plans,
        "obsolete_bootstrap": obsolete_bootstrap,
        "retained_worktrees": retained_worktrees,
    }


def _unknown_names(
    directory: Path,
    registered: set[str],
    *,
    suffix: str = "",
    directories: bool = False,
) -> list[str]:
    if not directory.is_dir():
        return []
    names: list[str] = []
    for path in sorted(directory.iterdir()):
        name = path.name[: -len(suffix)] if suffix and path.name.endswith(suffix) else path.name
        if directories and not path.is_dir():
            continue
        if name not in registered:
            names.append(name)
    return names


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
