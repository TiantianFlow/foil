"""Seat lifecycle. Init creates the folder; spawn still does not launch."""

from __future__ import annotations

from pathlib import Path

from foil.board import ensure_board
from foil.errors import FoilError
from foil.project import (
    SKELETON,
    ensure_exclude,
    foil_root,
    git_toplevel,
    require_host_tools,
)
from foil.store import ensure_registry


def init_project(directory: str | None) -> None:
    require_host_tools()
    start = Path(directory).expanduser().resolve() if directory else Path.cwd()
    if not start.exists():
        raise FoilError("foil: not a git repository")
    toplevel = git_toplevel(start)
    root = foil_root(toplevel)
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for relative in SKELETON:
        path = root / relative
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    ensure_exclude(toplevel)
    ensure_board(toplevel)
    ensure_registry(toplevel)


def spawn_seat(
    root: Path,
    template: str,
    *,
    name: str | None = None,
    task: str | None = None,
) -> None:
    del root, name, task
    raise FoilError(f"foil: unknown template '{template}'")


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
