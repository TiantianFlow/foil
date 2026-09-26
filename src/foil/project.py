"""Git toplevel, host-tool checks, and the project Foil folder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from foil.errors import FoilError

FOIL_DIRNAME = ".foil"
EXCLUDE_PATTERN = "/.foil/"
SKELETON = (
    "templates/personas",
    "harnesses",
    "memory",
    "board/mail",
    "board/notes",
    "board/tasks",
    "board/results",
    "run/instructions",
    "run/plans",
    "run/fake",
    "skills",
)


def require_host_tools() -> None:
    if shutil.which("git") is None:
        raise FoilError("foil: git is not installed")
    if shutil.which("tmux") is None:
        raise FoilError("foil: tmux is not installed")


def git_toplevel(start: Path | None = None) -> Path:
    cwd = Path.cwd() if start is None else start
    if not cwd.exists():
        raise FoilError("foil: not a git repository")
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise FoilError("foil: git is not installed") from exc
    if result.returncode != 0:
        raise FoilError("foil: not a git repository")
    toplevel = Path(result.stdout.strip())
    if (toplevel / FOIL_DIRNAME).is_dir():
        return toplevel
    common = _common_dir(cwd)
    if common is not None and (common.parent / FOIL_DIRNAME).is_dir():
        return common.parent
    return toplevel


def _common_dir(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def foil_root(toplevel: Path) -> Path:
    return toplevel / FOIL_DIRNAME


def is_initialized(toplevel: Path) -> bool:
    return foil_root(toplevel).is_dir()


def discover_project(start: Path | None = None) -> Path:
    require_host_tools()
    return git_toplevel(start)


def ensure_exclude(toplevel: Path) -> None:
    try:
        result = subprocess.run(
            ["git", "-C", str(toplevel), "rev-parse", "--git-path", "info/exclude"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FoilError("foil: not a git repository") from exc
    raw = result.stdout.strip()
    if not raw:
        raise FoilError("foil: not a git repository")
    path = Path(raw) if Path(raw).is_absolute() else toplevel / raw
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if EXCLUDE_PATTERN in current.splitlines():
        return
    prefix = "" if not current or current.endswith("\n") else "\n"
    path.write_text(f"{current}{prefix}{EXCLUDE_PATTERN}\n", encoding="utf-8")
