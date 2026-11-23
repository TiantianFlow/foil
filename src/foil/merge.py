"""Merge a fleet's goal branch into a checkout, and the done-goal finish block.

The command is the human's tap. A refusal is one line and an inbox item.
The checkout is left as it was. The master is told the finish block and
does not run this command.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from foil.errors import FoilError, shown
from foil.front import parse_front
from foil.store import (
    FLEET_ID,
    SAFE_ID,
    exclusive_lock,
    fleet_dir,
    fleet_ids,
    load_registry,
    write_bytes,
)

_FILE_CAP = 1024 * 1024
_LINE_CAP = 200


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_EDITOR"] = "true"
    env["GIT_MERGE_AUTOEDIT"] = "no"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _refusal_path(fleet: Path) -> Path:
    return fleet / "run" / "merge.json"


def recorded_refusal(fleet: Path) -> tuple[str, str] | None:
    """The refusal line and its time, or None when the file is not a record."""

    path = _refusal_path(fleet)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    line, stamp = data.get("line"), data.get("at")
    if not isinstance(line, str) or not line.strip() or any(ord(char) < 32 for char in line):
        return None
    when = stamp if isinstance(stamp, str) else ""
    return " ".join(line.split()), when


def _record(fleet: Path, line: str) -> None:
    path = _refusal_path(fleet)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    payload = (json.dumps({"at": _now(), "line": line}, sort_keys=True) + "\n").encode()
    write_bytes(path, payload)


def _clear(fleet: Path) -> None:
    path = _refusal_path(fleet)
    if path.is_symlink() or not path.is_file():
        return
    path.unlink()


def _refuse(fleet: Path, line: str) -> NoReturn:
    _record(fleet, line)
    raise FoilError(f"foil: {line}")


def _branch_name(name: str) -> bool:
    if (
        not name
        or len(name) > _LINE_CAP
        or name.startswith("-")
        or any(ord(char) < 32 for char in name)
    ):
        return False
    checked = subprocess.run(
        ["git", "check-ref-format", "--branch", name],
        capture_output=True,
        text=True,
        check=False,
    )
    return checked.returncode == 0 and checked.stdout.strip() == name


def _exists(root: Path, branch: str) -> bool:
    found = _git(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    return found.returncode == 0


def _goal_named(fleet: str, branch: str) -> bool:
    if not FLEET_ID.fullmatch(fleet):
        return False
    return re.fullmatch(rf"foil/goal-{re.escape(fleet)}(?:-[0-9]+)?", branch) is not None


def _goal(root: Path, fleet: str) -> str:
    literal = f"foil/goal-{fleet}"
    try:
        lead = load_registry(root, fleet)["seats"].get("lead") or {}
    except FoilError:
        lead = {}
    branch = lead.get("branch") if isinstance(lead, dict) else ""
    state = lead.get("state") if isinstance(lead, dict) else ""
    if (
        isinstance(branch, str)
        and state not in {"joined", "killed"}
        and _goal_named(fleet, branch)
        and _exists(root, branch)
    ):
        return branch
    if _exists(root, literal):
        return literal
    return ""


def changed_paths(root: Path, branch: str) -> set[str] | None:
    """Paths ``branch`` changes since the merge base with ``HEAD``.

    None means git could not answer. An empty set means no changed path.
    """

    found = _git(root, ["diff", "--name-only", "--no-renames", f"HEAD...{branch}"])
    if found.returncode != 0:
        return None
    paths = set()
    for name in found.stdout.splitlines():
        if not name or name.startswith("/") or ".." in Path(name).parts:
            continue
        paths.add(name)
    return paths


def overlaps(root: Path) -> dict[str, tuple[list[str], list[str]]]:
    """Fleets whose goal branches change a path another fleet also changes.

    The value is the other fleet ids, then the shared paths, both sorted.
    A fleet with no goal branch, and a diff git cannot answer, is left out.
    """

    changed: dict[str, set[str]] = {}
    try:
        names = fleet_ids(root)
    except FoilError:
        return {}
    for fleet in names:
        goal = _goal(root, fleet)
        if not goal:
            continue
        paths = changed_paths(root, goal)
        if not paths:
            continue
        changed[fleet] = paths
    by_path: dict[str, list[str]] = {}
    for fleet, paths in changed.items():
        for path in paths:
            by_path.setdefault(path, []).append(fleet)
    found: dict[str, tuple[list[str], list[str]]] = {}
    for fleet, paths in changed.items():
        shared = sorted(path for path in paths if len(by_path[path]) > 1)
        others = sorted({other for path in shared for other in by_path[path] if other != fleet})
        if shared and others:
            found[fleet] = (others, shared)
    return found


def _current(root: Path) -> tuple[str, str]:
    """The current branch, or empty when HEAD is detached, and the commit."""

    head = _git(root, ["rev-parse", "HEAD"])
    if head.returncode != 0 or not head.stdout.strip():
        raise FoilError("foil: not a git checkout")
    name = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if name.returncode != 0:
        raise FoilError("foil: not a git checkout")
    branch = "" if name.stdout.strip() == "HEAD" else name.stdout.strip()
    return branch, head.stdout.strip()


def _dirty(root: Path) -> bool:
    status = _git(root, ["status", "--porcelain"])
    if status.returncode != 0:
        raise FoilError("foil: not a git checkout")
    return bool(status.stdout.strip())


def _merging(root: Path) -> bool:
    return _git(root, ["rev-parse", "-q", "--verify", "MERGE_HEAD"]).returncode == 0


def _conflict(root: Path, ours: str, theirs: str) -> bool:
    """Whether merging ``theirs`` into ``ours`` is a content conflict.

    ``git merge`` exits before it writes MERGE_HEAD when the committer
    identity is unset, which is how a conflict was reported as a generic
    failure. The two commits answer the question without that identity.
    """

    found = _git(root, ["merge-tree", "--write-tree", "--name-only", ours, theirs])
    return found.returncode == 1


def _restore(root: Path, branch: str, commit: str, switched: bool) -> None:
    if _merging(root) and _git(root, ["merge", "--abort"]).returncode != 0:
        raise FoilError("foil: could not merge")
    if not switched:
        return
    back = ["checkout", branch] if branch else ["checkout", "--detach", commit]
    if _git(root, back).returncode != 0:
        raise FoilError("foil: could not merge")


def merge_goal(root: Path, fleet: str, *, into: str | None = None) -> dict[str, str]:
    """Merge the goal branch. A refusal leaves the checkout as it was."""

    if not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    directory = fleet_dir(root, fleet)
    with exclusive_lock(directory / "run" / "merge.lock"):
        if _refusal_path(directory).is_symlink():
            raise FoilError("foil: refusing symlink")
        if into is not None and not _branch_name(into):
            _refuse(directory, f"invalid branch '{shown(into or '')}'")
        try:
            current, commit = _current(root)
        except FoilError:
            _refuse(directory, "not a git checkout")
        goal = _goal(root, fleet)
        if not goal:
            _refuse(directory, "no goal branch")
        target = into or current
        if not target:
            _refuse(directory, "checkout is not on a branch")
        if into is not None and not _exists(root, into):
            _refuse(directory, f"unknown branch '{shown(into)}'")
        try:
            dirty = _dirty(root)
        except FoilError:
            _refuse(directory, "not a git checkout")
        if dirty:
            _refuse(directory, "checkout is dirty")
        if fleet in overlaps(root):
            _refuse(directory, "fleets touch the same files")
        switched = False
        if target != current:
            if _git(root, ["checkout", target]).returncode != 0:
                _refuse(directory, f"could not check out '{shown(target)}'")
            switched = True
        if _conflict(root, target, goal):
            try:
                _restore(root, current, commit, switched)
            except FoilError:
                _refuse(directory, "could not merge")
            _refuse(directory, "merge conflict")
        merged = _git(root, ["merge", "--no-edit", goal])
        if merged.returncode != 0:
            conflict = _merging(root)
            try:
                _restore(root, current, commit, switched)
            except FoilError:
                _refuse(directory, "could not merge")
            _refuse(directory, "merge conflict" if conflict else "could not merge")
        _clear(directory)
        return {"branch": goal, "into": target}


def _reviewers(root: Path, fleet: str) -> set[str]:
    try:
        seats = load_registry(root, fleet)["seats"]
    except FoilError:
        return set()
    return {
        name
        for name, seat in seats.items()
        if isinstance(seat, dict) and seat.get("template") == "reviewer"
    }


def _fact(body: str, heading: str) -> str:
    lines = body.splitlines()
    marker = f"## {heading}"
    start = next((index + 1 for index, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        return "-"
    for line in lines[start:]:
        if line.startswith("## "):
            break
        folded = " ".join(line.split())
        if not folded:
            continue
        if any(ord(char) < 32 for char in folded):
            return "-"
        return folded[:_LINE_CAP]
    return "-"


def _result(root: Path, fleet: str) -> Path | None:
    folder = fleet_dir(root, fleet) / "board" / "results"
    try:
        if folder.is_symlink() or not folder.is_dir():
            return None
    except OSError:
        return None
    authors = _reviewers(root, fleet)
    found: list[Path] = []
    try:
        paths = list(folder.glob("*.md"))
    except OSError:
        return None
    for path in paths:
        name = path.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.md", name):
            continue
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _FILE_CAP:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        front = parse_front(text)
        fields = front.fields
        if front.raw is not None or not fields or fields.get("contract") != "result/v1":
            continue
        author = fields.get("author")
        if fields.get("outcome") != "pass" or not isinstance(author, str):
            continue
        if not SAFE_ID.fullmatch(author) or author not in authors:
            continue
        found.append(path)
    if not found:
        return None
    found.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    return found[-1]


def finish_block(root: Path, fleet: str, project: str) -> str:
    """The message a done goal asks the master to send, citing the result file."""

    path = _result(root, fleet)
    if path is None:
        changed = tests = risks = "-"
        cited = "(none)"
    else:
        try:
            body = parse_front(path.read_text(encoding="utf-8")).body
        except (OSError, UnicodeDecodeError):
            body = ""
        changed, tests, risks = _fact(body, "Changed"), _fact(body, "Tests"), _fact(body, "Risks")
        cited = f"projects/{project}/fleets/{fleet}/board/results/{path.name}"
    return "\n".join(
        [
            "card: merge",
            f"project: {project}",
            f"fleet: {fleet}",
            "",
            f"What changed: {changed}",
            f"Tests: {tests}",
            f"Risks: {risks}",
            f"Result: {cited}",
        ]
    )
