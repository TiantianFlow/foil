"""Git common dir, host-tool checks, and the per-user project store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from foil.errors import FoilError

FOIL_DIRNAME = ".foil"
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
_MIGRATION = (
    "foil: an old .foil/ is still in this repository; "
    "move it aside and run foil init again"
)


def require_host_tools() -> None:
    if shutil.which("git") is None:
        raise FoilError("foil: git is not installed")
    if shutil.which("tmux") is None:
        raise FoilError("foil: tmux is not installed")


def git_toplevel(start: Path | None = None) -> Path:
    return Path(_git_line(start, "--show-toplevel"))


def common_dir(start: Path | None = None) -> Path:
    cwd = Path.cwd() if start is None else start
    return _common_dir(cwd)


def _common_dir(cwd: Path) -> Path:
    raw = _git_line(cwd, "--git-common-dir")
    path = Path(raw)
    if not path.is_absolute():
        path = (cwd / path).resolve()
    return path


def _git_line(start: Path | None, flag: str) -> str:
    cwd = Path.cwd() if start is None else start
    if not cwd.exists():
        raise FoilError("foil: not a git repository")
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", flag],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise FoilError("foil: git is not installed") from exc
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        raise FoilError("foil: not a git repository")
    return raw


def store_root() -> Path:
    override = os.environ.get("FOIL_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "foil"


def project_key(common: Path) -> str:
    resolved = common.resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:6]
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(resolved)).strip("-")
    slug = slug[-48:] if len(slug) > 48 else slug
    return f"{slug or 'repo'}-{digest}"


def foil_root(toplevel: Path) -> Path:
    """The project folder for a repository, or a hand-built ``.foil``.

    A Git repository resolves through the central store. A directory that
    is not a repository keeps ``.foil`` beside it, which is how unit tests
    build a folder without running ``init``.
    """

    try:
        common = _common_dir(toplevel)
    except FoilError:
        return toplevel / FOIL_DIRNAME
    return store_root() / "projects" / project_key(common)


def project_record(root: Path) -> dict[str, str]:
    path = root / "project.json"
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: str(payload.get(key, "")) for key in ("path", "origin", "created")}


def is_initialized(toplevel: Path) -> bool:
    root = foil_root(toplevel)
    return root.is_dir() and (root / "project.json").is_file()


def discover_project(start: Path | None = None) -> Path:
    require_host_tools()
    named = os.environ.get("FOIL_PROJECT", "").strip()
    if named:
        folder = store_root() / "projects" / named
        if (folder / "project.json").is_file():
            return Path(project_record(folder).get("path") or git_toplevel(start))
    return git_toplevel(start)


def _origin(toplevel: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(toplevel), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _checkout_path(root: Path, toplevel: Path) -> Path:
    """The main checkout, not the linked or seat worktree init was run from.

    An existing recorded path stays. Otherwise the parent of the Git
    common dir is the main checkout.
    """

    recorded = project_record(root).get("path", "")
    if recorded and Path(recorded).exists():
        return Path(recorded)
    return _common_dir(toplevel).parent


def _write_record(root: Path, toplevel: Path, *, created: str) -> None:
    payload = {
        "path": str(_checkout_path(root, toplevel).resolve()),
        "origin": _origin(toplevel),
        "created": created,
    }
    path = root / "project.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _seat_alive(root: Path) -> bool:
    registry = root / "run" / "registry.json"
    if registry.is_symlink() or not registry.is_file():
        return False
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    seats = payload.get("seats") if isinstance(payload, dict) else None
    if not isinstance(seats, dict):
        return True
    return any(
        isinstance(record, dict) and record.get("state") != "killed" for record in seats.values()
    )


def _rewrite_worktree_paths(root: Path, pairs: list[tuple[str, str]]) -> None:
    """Point stored seat paths at each worktree's new directory."""

    if not pairs:
        return
    run = root / "run"
    targets = [run / "registry.json"]
    for folder in ("plans", "instructions"):
        directory = run / folder
        if directory.is_dir():
            targets.extend(
                path for path in directory.iterdir() if path.is_file() and not path.is_symlink()
            )
    for path in targets:
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        replaced = text
        for old, new in pairs:
            if old == new:
                continue
            replaced = replaced.replace(old, new)
        if replaced != text:
            path.write_text(replaced, encoding="utf-8")


def _repair_worktrees(root: Path, toplevel: Path, *, relocated: Path | None = None) -> None:
    worktrees = root / "worktrees"
    seats = []
    if worktrees.is_dir():
        seats = [child for child in worktrees.iterdir() if (child / ".git").exists()]
    if not seats:
        return
    pairs: list[tuple[str, str]] = []
    if relocated is not None:
        pairs.append((str(relocated.resolve()), str(root.resolve())))
    result = subprocess.run(
        ["git", "-C", str(toplevel), "worktree", "repair", *[str(seat) for seat in seats]],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        message = "foil: could not repair worktrees"
        if detail:
            message = f"{message}: {detail}"
        raise FoilError(message)
    _rewrite_worktree_paths(root, pairs)


def _simple_legacy(legacy: Path) -> bool:
    if legacy.is_symlink() or not legacy.is_dir():
        return False
    for name in ("board", "run", "templates"):
        child = legacy / name
        if child.is_symlink() or not child.is_dir():
            return False
    return True


def _move_legacy(legacy: Path, root: Path, toplevel: Path) -> None:
    """Move every child, or put back anything already moved.

    A name that already exists in the store is a collision. The old
    directory is left as it was, including any seat worktree.
    """

    moved: list[tuple[Path, Path]] = []
    try:
        for child in list(legacy.iterdir()):
            target = root / child.name
            if target.exists():
                raise FoilError(_MIGRATION)
            shutil.move(str(child), str(target))
            moved.append((target, legacy / child.name))
        legacy.rmdir()
    except (OSError, FoilError):
        for target, source in reversed(moved):
            if target.exists() and not source.exists():
                shutil.move(str(target), str(source))
        raise


def prepare_project(toplevel: Path) -> Path:
    """Create or adopt the store folder. Print one line when a move is unsafe."""

    root = foil_root(toplevel)
    projects = root.parent
    record = project_record(root) if root.is_dir() else {}
    if not record:
        adopted = _adopt(projects, toplevel)
        if adopted is not None:
            root = adopted
            record = project_record(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    created = record.get("created") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_record(root, toplevel, created=created)
    legacy = toplevel / FOIL_DIRNAME
    if legacy.is_dir() and not legacy.is_symlink() and legacy.resolve() != root.resolve():
        if not _seat_alive(legacy) and _simple_legacy(legacy):
            try:
                _move_legacy(legacy, root, toplevel)
            except (OSError, FoilError):
                print(_MIGRATION)
            else:
                print(f"Moved {legacy} into {root}")
                _repair_worktrees(root, toplevel, relocated=legacy)
        else:
            print(_MIGRATION)
    return root


def _adopt(projects: Path, toplevel: Path) -> Path | None:
    origin = _origin(toplevel)
    if not origin or not projects.is_dir():
        return None
    matches: list[Path] = []
    for folder in projects.iterdir():
        if not folder.is_dir():
            continue
        record = project_record(folder)
        if record.get("origin") != origin:
            continue
        recorded = record.get("path", "")
        if recorded and not Path(recorded).exists():
            matches.append(folder)
    if len(matches) != 1:
        return None
    chosen = matches[0]
    target = foil_root(toplevel)
    if chosen.resolve() == target.resolve():
        return chosen
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    chosen.rename(target)
    _repair_worktrees(target, toplevel, relocated=chosen)
    print(f"Adopted {chosen.name} as {target}")
    return target
