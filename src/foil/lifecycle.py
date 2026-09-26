"""Seat lifecycle. Init creates the folder; spawn launches a seat."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from foil.board import ensure_board, send_mail
from foil.errors import FoilError
from foil.presets import (
    expand_argv,
    installed_harness,
    load_preset,
    load_template,
    write_default_templates,
)
from foil.project import (
    SKELETON,
    ensure_exclude,
    foil_root,
    git_toplevel,
    require_host_tools,
)
from foil.store import (
    SAFE_ID,
    SEAT_FIELDS,
    ensure_registry,
    load_registry,
    save_registry,
    scan,
    write_bytes,
)
from foil.tmux import TmuxController, TmuxError


def init_project(directory: str | None) -> None:
    require_host_tools()
    start = Path(directory).expanduser().resolve() if directory else Path.cwd()
    if not start.exists():
        raise FoilError("foil: not a git repository")
    toplevel = git_toplevel(start)
    root = foil_root(toplevel)
    if any(
        not ((root / "templates" / f"{role}.toml").exists())
        for role in ("lead", "implementer", "reviewer")
    ):
        installed_harness()
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for relative in SKELETON:
        path = root / relative
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    ensure_exclude(toplevel)
    ensure_board(toplevel)
    ensure_registry(toplevel)
    write_default_templates(toplevel)


def _shown(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


def _seat_name(registry: dict, template_name: str, requested: str | None) -> str:
    seats = registry["seats"]
    if template_name == "lead":
        if requested not in (None, "lead"):
            raise FoilError("foil: lead seat must be named lead")
        existing = seats.get("lead")
        if existing and existing.get("state") != "killed":
            raise FoilError("foil: lead already exists")
        return "lead"
    if not seats:
        raise FoilError("foil: the first seat must be the lead")
    if requested == "lead" or (requested is not None and requested in seats):
        raise FoilError(f"foil: seat '{_shown(requested or '')}' already exists")
    if requested:
        if not SAFE_ID.fullmatch(requested):
            raise FoilError(f"foil: invalid name '{_shown(requested)}'")
        return requested
    for number in range(1, 10001):
        candidate = f"{template_name}-{number}"
        if candidate not in seats:
            return candidate
    raise FoilError("foil: could not name seat")


def _git(toplevel: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(toplevel), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _worktree(toplevel: Path, seat: str) -> tuple[str, str]:
    branch = f"foil/{seat}"
    if _git(toplevel, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        raise FoilError(f"foil: branch '{branch}' already exists")
    destination = (toplevel.parent / f"{toplevel.name}.foil" / seat).resolve()
    project = toplevel.resolve()
    inside = destination == project or project in destination.parents
    if inside or (project / "worktrees") in destination.parents:
        raise FoilError("foil: refusing worktree inside the project")
    if destination.exists() or destination.is_symlink():
        raise FoilError("foil: worktree path already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = _git(toplevel, ["worktree", "add", "-b", branch, str(destination)])
    if created.returncode != 0:
        raise FoilError("foil: could not create worktree")
    return str(destination), branch


def _session_name(toplevel: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", toplevel.name).strip("-")[:32] or "repo"
    digest = hashlib.sha256(str(toplevel.resolve()).encode()).hexdigest()[:8]
    return f"foil-{slug}-{digest}"


def spawn_seat(
    root: Path,
    template: str,
    *,
    name: str | None = None,
    task: str | None = None,
) -> None:
    loaded = load_template(root, template)
    if task is not None:
        scan(task)
    registry = load_registry(root)
    seat = _seat_name(registry, loaded["name"], name)
    preset = load_preset(root, loaded["harness"])
    native = str(uuid.uuid4()) if preset["session_id"] == "generated" else ""
    worktree, branch = _worktree(root, seat) if loaded["worktree"] else ("", "")
    cwd = Path(worktree) if worktree else root
    instruction = (foil_root(root) / "run" / "instructions" / f"{seat}.md").resolve()
    write_bytes(instruction, f"# {seat}\n".encode())
    prompt = f"Read {instruction} first."
    argv = expand_argv(
        preset,
        model=loaded["model"] or None,
        prompt=prompt,
        session_id=native or None,
        permission=loaded["permission"],
    )
    forward = list(preset["env"])
    if "PATH" not in forward:
        forward.append("PATH")
    plan_path = (foil_root(root) / "run" / "plans" / f"{seat}.json").resolve()
    plan = {
        "argv": argv,
        "cwd": str(cwd.resolve()),
        "env": {"FOIL_SEAT_ID": seat},
        "env_forward": forward,
    }
    scan(plan)
    write_bytes(plan_path, json.dumps(plan, sort_keys=True).encode())
    session = registry.get("tmux_session") or _session_name(root)
    try:
        target = TmuxController().launch(
            fleet_id=str(registry["fleet_id"]),
            seat_id=seat,
            session_name=session,
            window_name=seat,
            working_directory=cwd.resolve(),
            runner_argv=[sys.executable, "-m", "foil.runner", str(plan_path)],
        )
    except TmuxError as exc:
        raise FoilError("foil: could not launch seat") from exc
    if not target.window_id:
        TmuxController().abandon_window(target)
        raise FoilError("foil: could not launch seat")
    record = {key: "" for key in SEAT_FIELDS}
    record.update(
        {
            "name": seat,
            "template": loaded["name"],
            "harness": loaded["harness"],
            "window_id": target.window_id,
            "worktree": worktree,
            "branch": branch,
            "session_id": native,
        }
    )
    registry["tmux_session"] = session
    if loaded["name"] == "lead":
        registry["lead"] = "lead"
    registry["seats"][seat] = record
    try:
        save_registry(root, registry)
    except Exception:
        TmuxController().abandon_window(target)
        raise
    if task is not None:
        send_mail(root, seat, task)


def kill_seats(
    root: Path, *, name: str | None = None, all_seats: bool = False
) -> None:
    del root, all_seats
    if name is None:
        return
    raise FoilError(f"foil: unknown seat '{name}'")


def resume_seats(root: Path, name: str | None) -> None:
    del root
    if name is None:
        return
    raise FoilError(f"foil: unknown seat '{name}'")


def list_seats(root: Path, *, as_json: bool = False) -> None:
    del root
    if as_json:
        print("[]")


def peek_seat(root: Path, name: str, *, lines: int) -> None:
    del root, lines
    raise FoilError(f"foil: unknown seat '{name}'")
