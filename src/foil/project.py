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
    "board/room",
    "board/tasks",
    "board/results",
    "run/instructions",
    "run/plans",
    "run/fake",
    "skills",
)
_OLD_FOIL = (
    "install 0.3.4 beside it, which does not replace the foil on PATH: "
    "UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "
    '"git+https://github.com/TiantianFlow/foil.git@v0.3.4", '
    "then run DIR/bin/foil seat kill --all. With pipx: "
    "PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "
    '"git+https://github.com/TiantianFlow/foil.git@v0.3.4", '
    "then run DIR/bin/foil seat kill --all"
)
_UPGRADE = (
    "kill them with the old foil (foil seat kill --all), then upgrade, "
    f"then foil init. When that foil is no longer on PATH, {_OLD_FOIL}"
)
_RESOLVED_HOME: dict[str, Path] = {}


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
        path = Path(override).expanduser()
        if path.is_absolute():
            return path.resolve()
        cached = _RESOLVED_HOME.get(override)
        if cached is None:
            cached = path.resolve()
            _RESOLVED_HOME[override] = cached
        return cached
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
    return {key: str(payload.get(key, "")) for key in ("path", "origin", "root", "created")}


def is_initialized(toplevel: Path) -> bool:
    root = foil_root(toplevel)
    return root.is_dir() and (root / "project.json").is_file()


def main_checkout(start: Path | None = None) -> Path:
    """The main checkout, even when the command runs inside a worktree.

    A 0.3.x seat worktree's own toplevel is that worktree, which has no
    ``.foil/``. The directory does, and it is the parent of the Git common dir.
    """

    cwd = Path.cwd() if start is None else start
    return _common_dir(cwd).parent


def legacy_dir(start: Path | None = None) -> Path | None:
    """An in-repo ``.foil/`` in the main checkout, when it is not the store."""

    try:
        checkout = main_checkout(start)
    except FoilError:
        return None
    legacy = checkout / FOIL_DIRNAME
    if legacy.is_symlink() or not legacy.is_dir():
        return None
    try:
        store = foil_root(checkout)
    except FoilError:
        store = legacy
    if legacy.resolve() == store.resolve():
        return None
    return legacy


def refuse_legacy_layout(toplevel: Path) -> None:
    """Name the upgrade when a 0.4 command still sees an in-repo ``.foil/``.

    The directory is the main checkout's, not the current toplevel, so a
    command run from a 0.3.x seat worktree still finds it.
    """

    legacy = legacy_dir(toplevel)
    if legacy is None:
        return
    blocking = _blocking_seats(legacy)
    if blocking:
        _refuse_live_legacy(blocking)
    raise FoilError(
        "foil: an old .foil/ is still in this repository. Run foil init."
    )


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


def _root_commit(toplevel: Path) -> str:
    """The first commit, used as an identity when the repo has no origin."""

    try:
        result = subprocess.run(
            ["git", "-C", str(toplevel), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    commits = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return commits[0] if len(commits) == 1 else ""


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
        "root": _root_commit(toplevel),
        "created": created,
    }
    path = root / "project.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _blocking_seats(root: Path) -> list[str]:
    """Seats that were never killed, even when their tmux window is gone.

    An unreadable registry is reported as unreadable rather than as a
    seat named unknown.
    """

    registry = root / "run" / "registry.json"
    if registry.is_symlink() or not registry.is_file():
        return []
    try:
        payload = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise FoilError(
            "foil: an old .foil/ has a registry that cannot be read. "
            "Fix run/registry.json in that directory, then run foil init."
        ) from None
    seats = payload.get("seats") if isinstance(payload, dict) else None
    if not isinstance(seats, dict):
        raise FoilError(
            "foil: an old .foil/ has a registry that cannot be read. "
            "Fix run/registry.json in that directory, then run foil init."
        )
    return sorted(
        name
        for name, record in seats.items()
        if isinstance(name, str)
        and isinstance(record, dict)
        and record.get("state") != "killed"
    )


def _refuse_live_legacy(names: list[str]) -> None:
    shown = ", ".join(names) if names else "unknown"
    raise FoilError(
        "foil: an old .foil/ still has seats that were not killed "
        f"({shown}). A stale seat whose tmux window is already gone also "
        f"counts. {_UPGRADE}"
    )


def _write_json(path: Path, text: str) -> None:
    """Replace a JSON file atomically (N7)."""

    from foil.store import write_bytes

    write_bytes(path, text.encode())


def _run_files(root: Path) -> list[Path]:
    run = root / "run"
    targets = [run / "registry.json"]
    for folder in ("plans", "instructions", "archive"):
        directory = run / folder
        # A symlink would rewrite files outside the store. Archive and list
        # already refuse that directory.
        if directory.is_symlink() or not directory.is_dir():
            continue
        targets.extend(
            path for path in directory.iterdir() if path.is_file() and not path.is_symlink()
        )
    return [path for path in targets if path.is_file() and not path.is_symlink()]


def _swap_prefix(text: str, old: str, new: str) -> str:
    """Replace ``old`` only as a whole path, bounded on both sides.

    A boundary before the match stops ``/tmp/x`` matching inside
    ``/private/tmp/x``. A boundary after it stops ``/.foil`` matching
    ``/.foil-home`` or ``/.foilrc``.
    """

    boundary = "/\\\"' \n\t`"
    pieces: list[str] = []
    start = 0
    while True:
        found = text.find(old, start)
        if found < 0:
            pieces.append(text[start:])
            return "".join(pieces)
        after = found + len(old)
        before_ok = found == 0 or text[found - 1] in boundary
        after_ok = after == len(text) or text[after] in boundary
        if before_ok and after_ok:
            pieces.append(text[start:found])
            pieces.append(new)
        else:
            pieces.append(text[start:after])
        start = after


def _replace_text(root: Path, pairs: list[tuple[str, str]]) -> None:
    usable = sorted(
        ((old, new) for old, new in pairs if old and old != new),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    if not usable:
        return
    from foil.store import exclusive_lock

    def rewrite(path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        replaced = text
        for old, new in usable:
            replaced = _swap_prefix(replaced, old, new)
            replaced = _swap_prefix(
                replaced, json.dumps(old)[1:-1], json.dumps(new)[1:-1]
            )
        if replaced != text:
            _write_json(path, replaced if replaced.endswith("\n") else replaced + "\n")

    for path in _run_files(root):
        if path.parent.name == "archive":
            continue
        if path.name != "registry.json":
            rewrite(path)
            continue
        with exclusive_lock(path.with_suffix(".lock")):
            if path.is_symlink() or not path.is_file():
                continue
            archive = path.parent / "archive"
            if not archive.is_symlink() and archive.is_dir():
                for child in archive.iterdir():
                    if child.is_file() and not child.is_symlink():
                        rewrite(child)
            rewrite(path)


def _texts_in_run(root: Path) -> list[str]:
    texts: list[str] = []
    for path in _run_files(root):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        texts.append(raw)
        if path.suffix != ".json":
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        texts.extend(_string_values(payload))
    return texts


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        found: list[str] = []
        for item in value.values():
            found.extend(_string_values(item))
        return found
    if isinstance(value, list):
        found = []
        for item in value:
            found.extend(_string_values(item))
        return found
    return []


def _accepted_foil(text: str) -> str:
    """``<checkout>/.foil`` when it ends on a path boundary and is gone.

    ``/.foil-home`` and ``/.foilrc`` do not end at ``/.foil``. A prefix
    whose parent still exists is a live checkout and is not rewritten.
    """

    marker = "/.foil"
    start = 0
    while True:
        at = text.find(marker, start)
        if at < 0:
            return ""
        after = at + len(marker)
        bounded = after == len(text) or text[after] in "/\\\"' \n\t`"
        begin = at
        while begin > 0 and text[begin - 1] not in " \n\t\"'`":
            begin -= 1
        foil = text[begin:after]
        if bounded and foil.startswith("/") and not Path(foil).parent.exists():
            return foil
        start = after


def _foil_prefix_in_run(root: Path) -> str:
    """An old ``<checkout>/.foil`` named in ``run/`` whose checkout is gone.

    A rename with no seat worktree leaves no gitdir to read. The stored
    board and instruction paths still name the old directory.
    """

    for text in _texts_in_run(root):
        foil = _accepted_foil(text)
        if foil:
            return foil
    return ""


def _missing_checkout(root: Path, checkout: str) -> str:
    """The one plan cwd that is an absolute path and no longer exists.

    This is the old checkout when no seat worktree recorded it. Several
    distinct missing directories are left alone.
    """

    found: set[str] = set()
    for path in _run_files(root):
        if path.parent.name != "plans" or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if (
            isinstance(cwd, str)
            and cwd.startswith("/")
            and cwd != checkout
            and not Path(cwd).exists()
        ):
            found.add(cwd)
    if len(found) == 1:
        return found.pop()
    return ""


def _seat_gitdir(toplevel: Path, root: Path) -> str:
    """A Foil seat gitdir, never a worktree the user created.

    A seat lives under ``.foil/worktrees/<label>`` or under the store's
    ``worktrees/<label>``, and that label is a directory in the store.
    A user's own worktree at ``<checkout>/worktrees/<name>`` is ignored.
    """

    labels = set()
    seats = root / "worktrees"
    if seats.is_dir():
        labels = {child.name for child in seats.iterdir() if child.is_dir()}
    admin = _common_dir(toplevel) / "worktrees"
    if not admin.is_dir():
        return ""
    found = ""
    for child in admin.iterdir():
        if child.name not in labels:
            continue
        recorded = child / "gitdir"
        if recorded.is_symlink() or not recorded.is_file():
            continue
        text = recorded.read_text(encoding="utf-8").strip()
        marker = f"/worktrees/{child.name}/.git"
        # A seat under .foil/worktrees, or under the store's worktrees/.
        # A user's worktree is already excluded: its label is not here.
        if not text.endswith(marker):
            continue
        prefix = text.removesuffix("/.git")
        marker_at = prefix.rfind("/worktrees/")
        if marker_at < 0:
            continue
        # A partial repair leaves some gitdirs current. Prefer one whose
        # old directory is already gone, so the prefix is the leftover.
        if not Path(prefix[:marker_at]).exists():
            return text
        found = found or text
    return found


def _old_foil_prefix(toplevel: Path, root: Path) -> str:
    """The old ``<repo>/.foil`` or store root, taken from a Foil seat's gitdir.

    That text is ``<old location>/worktrees/<label>/.git``. Stripping
    ``/worktrees/<label>/.git`` leaves the old ``.foil`` directory or the
    old store project folder. It is rewritten only when that directory is
    gone. The checkout itself is a separate pair, and only when it is gone.
    """

    text = _seat_gitdir(toplevel, root)
    suffix = "/.git"
    if not text.endswith(suffix):
        return ""
    parent = text.removesuffix(suffix)
    marker = "/worktrees/"
    if marker not in parent:
        return ""
    return parent[: parent.rfind(marker)]


def _stale_worktrees(toplevel: Path, root: Path) -> dict[str, str]:
    """Foil seat name to the path Git still records, before repair.

    A renamed repository leaves that path in the common dir. Only a seat
    whose label exists under the store is considered, so a user's own
    worktree is not rewritten.
    """

    admin = _common_dir(toplevel) / "worktrees"
    found: dict[str, str] = {}
    seats = root / "worktrees"
    labels: set[str] = set()
    if seats.is_dir():
        labels = {child.name for child in seats.iterdir() if child.is_dir()}
    if not admin.is_dir():
        return found
    for child in admin.iterdir():
        if child.name not in labels:
            continue
        recorded = child / "gitdir"
        if recorded.is_symlink() or not recorded.is_file():
            continue
        text = recorded.read_text(encoding="utf-8").strip()
        if text.endswith("/.git") and "/worktrees/" in text:
            found[child.name] = text.removesuffix("/.git")
    return found


def _rewrite_worktree_paths(
    root: Path,
    toplevel: Path,
    *,
    relocated: Path | None,
    stale: dict[str, str],
    old_foil: str,
    previous: str,
    recover_missing: bool = True,
) -> None:
    """Point stored paths at the store, using prefixes read before repair.

    Repair rewrites the gitdir text, so the old prefix has to be captured
    first. The rewrite itself runs only after repair succeeds.
    """

    pairs: list[tuple[str, str]] = []
    recorded = previous or project_record(root).get("path", "")
    checkout = str(_common_dir(toplevel).parent.resolve())
    store = str(root.resolve())
    worktrees = root / "worktrees"
    if recorded and recorded != checkout and not Path(recorded).exists():
        pairs.append((recorded, checkout))
        pairs.append((f"{recorded}/.foil", store))
        # A hand repair already updated the gitdir, so it no longer names
        # the old store. The recorded path is the main checkout, whose
        # common dir is `<checkout>/.git` even after that checkout is gone.
        old_store = store_root() / "projects" / project_key(Path(recorded) / ".git")
        if not old_store.exists():
            pairs.append((str(old_store.resolve()), store))
            # The hand repair also left each seat's recorded worktree path
            # pointing at that store. The gitdir no longer says so.
            if worktrees.is_dir():
                for seat in worktrees.iterdir():
                    old_seat = old_store / "worktrees" / seat.name
                    if not old_seat.exists():
                        pairs.append((str(old_seat), str(seat.resolve())))
    if worktrees.is_dir():
        for seat in worktrees.iterdir():
            old = stale.get(seat.name, "")
            if old:
                pairs.append((old, str(seat.resolve())))
    if not old_foil:
        old_foil = _foil_prefix_in_run(root)
    # The directory itself must be gone. Its parent being gone is required
    # only for rewriting that parent (the old checkout) to the current one.
    if old_foil and not Path(old_foil).exists():
        pairs.append((old_foil, store))
        checkout_of = old_foil.removesuffix("/.foil") if old_foil.endswith("/.foil") else ""
        if checkout_of and _accepted_foil(old_foil) == old_foil:
            pairs.append((checkout_of, checkout))
    elif relocated is not None and not Path(relocated).exists():
        pairs.append((str(relocated), store))
    if recover_missing and not any(
        new == checkout or new.startswith(f"{checkout}/") for _old, new in pairs
    ):
        missing = _missing_checkout(root, checkout)
        if missing:
            pairs.append((missing, checkout))
    _replace_text(root, pairs)


def _rewrite_project_keys(root: Path, toplevel: Path) -> None:
    """Replace a stale ``FOIL_PROJECT`` value with the current project key."""

    current = project_key(_common_dir(toplevel))
    for path in _run_files(root):
        if path.parent.name != "plans" or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        env = payload.get("env") if isinstance(payload, dict) else None
        if not isinstance(env, dict):
            continue
        stored = env.get("FOIL_PROJECT")
        if not isinstance(stored, str) or stored == current:
            continue
        env["FOIL_PROJECT"] = current
        _write_json(path, json.dumps(payload) + "\n")


def _repair_pending(root: Path, toplevel: Path, *, previous: str = "") -> bool:
    """A repair or rewrite that an earlier init did not finish.

    The folder is already in place, so adopt and the legacy move do not
    run again. ``previous`` is the checkout recorded before this init wrote
    the current one. A recorded checkout that no longer exists, or a seat
    gitdir that still names a path other than the store worktree, is the
    leftover.
    """

    checkout = str(_common_dir(toplevel).parent.resolve())
    recorded = previous or project_record(root).get("path", "")
    if recorded and recorded != checkout and not Path(recorded).exists():
        return True
    return any(not Path(old).exists() for old in _stale_worktrees(toplevel, root).values())


def _repair_worktrees(
    root: Path, toplevel: Path, *, relocated: Path | None = None, previous: str = ""
) -> None:
    stale = _stale_worktrees(toplevel, root)
    old_foil = _old_foil_prefix(toplevel, root)
    worktrees = root / "worktrees"
    seats = []
    if worktrees.is_dir():
        seats = [child for child in worktrees.iterdir() if (child / ".git").exists()]
    if seats:
        _repair_git(toplevel, seats)
    _rewrite_worktree_paths(
        root,
        toplevel,
        relocated=relocated,
        stale=stale,
        old_foil=old_foil,
        previous=previous,
    )
    _rewrite_project_keys(root, toplevel)


def _repair_git(toplevel: Path, seats: list[Path]) -> None:
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


def _remove_empty_tree(path: Path) -> None:
    children = sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for child in children:
        child.rmdir()
    path.rmdir()


def _empty_dir(path: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    return all(not (child.is_symlink() or not child.is_dir()) for child in path.rglob("*"))


def _simple_legacy(legacy: Path) -> bool:
    if legacy.is_symlink() or not legacy.is_dir():
        return False
    for name in ("board", "run", "templates"):
        child = legacy / name
        if child.is_symlink() or not child.is_dir():
            return False
    return True


def _fresh_skeleton(root: Path) -> bool:
    """True when the store holds only empty directories and project.json.

    A refused 0.4.0 init left exactly that. Those empty directories are
    not a collision: the legacy children replace them.
    """

    for path in root.rglob("*"):
        if path.is_symlink():
            return False
        if path.is_file() and path.name != "project.json":
            return False
        if path.is_dir() and any(child.is_symlink() for child in path.iterdir()):
            return False
    return (root / "project.json").is_file()


def _move_legacy(legacy: Path, root: Path, toplevel: Path) -> None:
    """Move every child, or put back anything already moved.

    A name that already exists in the store is a collision, unless the
    store holds only a fresh skeleton. The old directory is left as it
    was, including any seat worktree.
    """

    skeleton = _fresh_skeleton(root)
    moved: list[tuple[Path, Path]] = []
    try:
        for child in list(legacy.iterdir()):
            target = root / child.name
            if target.exists() and not (skeleton and _empty_dir(target)):
                raise FoilError(
                    "foil: an old .foil/ is still in this repository and "
                    f"the store already has '{target.name}'. Remove that "
                    "stored entry, then run foil init again."
                )
            if target.exists():
                _remove_empty_tree(target)
            shutil.move(str(child), str(target))
            moved.append((target, legacy / child.name))
        legacy.rmdir()
    except (OSError, FoilError):
        for target, source in reversed(moved):
            if target.exists() and not source.exists():
                shutil.move(str(target), str(source))
        raise


def rewrite_stored_paths(root: Path, toplevel: Path, *, previous: str) -> None:
    """Rewrite stored paths once the run directories exist.

    Called only after a move, an adopt, or a retry. ``prepare_project``
    rewrites before ``run/plans`` and ``run/instructions`` are created, so
    a file placed there afterwards would keep the old prefix. This pass
    does not run git repair again, and it does not invent a checkout from
    a missing plan cwd.
    """

    _rewrite_worktree_paths(
        root,
        toplevel,
        relocated=None,
        stale=_stale_worktrees(toplevel, root),
        old_foil=_old_foil_prefix(toplevel, root),
        previous=previous,
        recover_missing=False,
    )
    _rewrite_project_keys(root, toplevel)


def prepare_project(toplevel: Path) -> tuple[Path, str, bool]:
    """Create or adopt the store folder.

    A legacy folder that still has a seat which was never killed refuses
    before any store directory is created. A collision after that point
    prints one instruction line and leaves both trees in place.
    """

    root = foil_root(toplevel)
    legacy = legacy_dir(toplevel)
    legacy_here = legacy is not None
    if legacy_here:
        blocking = _blocking_seats(legacy)
        if blocking:
            _refuse_live_legacy(blocking)
    projects = root.parent
    record = project_record(root) if root.is_dir() else {}
    # Read before adopt or _write_record. Adopt rewrites project.json, so a
    # later read would already be the current checkout and could not retry.
    previous = record.get("path", "")
    adopted_ok = False
    if not record:
        adopted = _adopt(projects, toplevel)
        if adopted is not None:
            root = adopted
            record = project_record(root)
            adopted_ok = True
            # Adopt already repaired and rewrote. Keeping the old checkout
            # would make the retry below do that work a second time.
            previous = ""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    created = record.get("created") or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_record(root, toplevel, created=created)
    rewritten = False
    if legacy_here:
        if _simple_legacy(legacy):
            try:
                _move_legacy(legacy, root, toplevel)
            except (OSError, FoilError) as exc:
                print(exc if isinstance(exc, FoilError) else f"foil: {exc}")
            else:
                print(f"Moved {legacy} into {root}")
                _repair_worktrees(root, toplevel, relocated=legacy, previous=previous)
                rewritten = True
        else:
            print(
                "foil: an old .foil/ is still in this repository and is not "
                "a simple directory tree. board, run, and templates must be "
                "real directories. Make them real directories, then run "
                "foil init again."
            )
    elif _repair_pending(root, toplevel, previous=previous):
        _repair_worktrees(root, toplevel, previous=previous)
        rewritten = True
    elif adopted_ok:
        rewritten = True
    return root, previous, rewritten


def _gone_records(projects: Path) -> list[tuple[Path, dict[str, str]]]:
    gone: list[tuple[Path, dict[str, str]]] = []
    if not projects.is_dir():
        return gone
    for folder in projects.iterdir():
        if not folder.is_dir():
            continue
        record = project_record(folder)
        recorded = record.get("path", "")
        if recorded and not Path(recorded).exists():
            gone.append((folder, record))
    return gone


def _could_be(record: dict[str, str], root: str) -> bool:
    """A gone record that might be this no-origin repository.

    A matching root commit is one. A 0.4.0 record has no root field, and a
    repository with no commits has none either, so a record with no root
    and no origin is the other. A record that names some other root, or
    any origin, belongs to a different repository.
    """

    if record.get("origin"):
        return False
    recorded = record.get("root", "")
    if root and recorded == root:
        return True
    return not recorded


def _adopt(projects: Path, toplevel: Path) -> Path | None:
    origin = _origin(toplevel)
    gone = _gone_records(projects)
    if origin:
        matches = [folder for folder, record in gone if record.get("origin") == origin]
        several = (
            "foil: several stored projects match this origin and their "
            "recorded paths are gone"
        )
    else:
        root = _root_commit(toplevel)
        matches = [folder for folder, record in gone if _could_be(record, root)]
        several = (
            "foil: several stored projects match this repository and their "
            "recorded paths are gone"
        )
        identified = [
            folder for folder in matches if root and project_record(folder).get("root") == root
        ]
        if len(identified) == 1:
            matches = identified
        elif not identified and matches:
            shown = ", ".join(str(folder) for folder in matches)
            print(
                "foil: an orphaned store record was not adopted "
                f"({shown}); a new project folder was created"
            )
            return None
    if len(matches) != 1:
        if len(matches) > 1:
            print(several)
        return None
    chosen = matches[0]
    previous = project_record(chosen).get("path", "")
    target = foil_root(toplevel)
    if chosen.resolve() == target.resolve():
        return chosen
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    chosen.rename(target)
    _repair_worktrees(target, toplevel, relocated=chosen, previous=previous)
    print(f"Adopted {chosen.name} as {target}")
    return target
