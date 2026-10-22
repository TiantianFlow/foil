"""Seat-only git hooks: keep private text out of commits, and a seat inside its worktree."""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
from functools import cache
from pathlib import Path

from foil.project import common_dir, foil_root
from foil.store import private_dir, write_bytes

CHECKED = ("pre-commit", "pre-push")
# core.hooksPath replaces the whole directory, so every client hook passes through.
# reference-transaction and fsmonitor-watchman run too often for a Python start.
HOOKS = (
    "applypatch-msg", "pre-applypatch", "post-applypatch", "pre-commit", "pre-merge-commit",
    "prepare-commit-msg", "commit-msg", "post-commit", "pre-rebase", "post-checkout",
    "post-merge", "pre-push", "pre-auto-gc", "post-rewrite", "sendemail-validate",
    "push-to-checkout",
)
_EDGE = r"(?<![A-Za-z0-9_-])"
_TOKEN = re.compile(
    rf"{_EDGE}(?:gh[oprsu]_[A-Za-z0-9]{{20,}}|github_pat_[A-Za-z0-9_]{{20,}}"
    rf"|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{{20,}}|xox[abprs]-[A-Za-z0-9-]{{10,}})"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_TRAILER = re.compile(r"^[A-Za-z][A-Za-z-]*: .*<[^<>@\s]+@[^<>\s]+>$")


def _plain_environment() -> dict[str, str]:
    """This environment without the seat's hooks path, as git sees it outside Foil."""

    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_CONFIG_")}


def _git(args: list[str], cwd: Path | None = None) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_plain_environment(),
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    return done.stdout if done.returncode == 0 else ""


def _install(path: Path, script: str) -> None:
    """Replace a hook only when it changed, and never leave it unexecutable."""

    if path.is_file() and not path.is_symlink() and path.read_text(encoding="utf-8") == script:
        return
    spare = path.with_name(f".{path.name}.new")
    write_bytes(spare, script.encode())
    spare.chmod(0o700)
    os.replace(spare, path)


def hook_environment(toplevel: Path, cwd: Path, worktree: str) -> dict[str, str]:
    """Point a seat's git at Foil's hooks. The repository's own hooks run after them."""

    directory = foil_root(toplevel) / "hooks"
    private_dir(directory)
    for name in HOOKS:
        # -P keeps the repository's own directory off sys.path.
        python = shlex.quote(sys.executable)
        _install(directory / name, f'#!/bin/sh\nexec {python} -P -m foil.guard {name} "$@"\n')
    environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(directory.resolve()),
        "FOIL_GIT_COMMON": str(common_dir(cwd).resolve()),
    }
    if worktree:
        environment["FOIL_WORKTREE"] = str(Path(worktree).resolve())
    return environment


@cache
def _private_words() -> list[tuple[str, str]]:
    """Text that names this machine or this person. Best effort, not a full scan."""

    words = []
    home = os.path.expanduser("~")
    # A home one level below / (such as /root) names no person.
    if len(Path(home).parts) > 2:
        words.append(("a home directory path", home))
    host = socket.gethostname()
    for name in {host, host.split(".")[0]}:
        if len(name) >= 4 and re.search(r"[0-9.-]", name) and name != "localhost":
            words.append(("this machine's hostname", name))
    for scope in (["--global"], []):
        email = _git(["config", *scope, "user.email"]).strip()
        if email and "noreply" not in email.lower():
            words.append(("a personal email address", email))
    return words


def _finding(lines: list[str], where: str) -> str | None:
    words = _private_words()
    for line in lines:
        lowered = line.lower()
        # A Signed-off-by or Co-authored-by trailer names its author on purpose.
        for kind, word in [] if _TRAILER.match(line.strip()) else words:
            if word.lower() in lowered:
                return f"{kind} in {where}"
        if _TOKEN.search(line):
            return f"credential-shaped text in {where}"
    return None


def _diff_finding(diff: str) -> str | None:
    """The first private text in the lines a diff adds, with the file it is in."""

    current = "the change"
    added: dict[str, list[str]] = {}
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("+"):
            added.setdefault(current, []).append(line[1:])
    for path, lines in added.items():
        found = _finding(lines, path)
        if found:
            return found
    return None


def _pre_commit() -> str | None:
    worktree = os.environ.get("FOIL_WORKTREE", "")
    top = _git(["rev-parse", "--show-toplevel"]).strip()
    if worktree and top and Path(top).resolve() != Path(worktree):
        seat = os.environ.get("FOIL_SEAT_ID") or "this seat"
        return f"foil: commit refused: {seat} works in {worktree}, not in {top}"
    found = _diff_finding(_git(["diff", "--cached", "-U0", "--no-color", "--no-ext-diff"]))
    return f"foil: commit refused: the staged change adds {found}" if found else None


def _pre_push(updates: str) -> str | None:
    for update in updates.splitlines():
        parts = update.split()
        if len(parts) != 4 or set(parts[1]) == {"0"}:
            continue
        # The remote's old tip, when this repository has it, bounds the scan.
        known = [f"^{parts[3]}"] if _git(["cat-file", "-t", parts[3]]).strip() == "commit" else []
        for sha in _git(["rev-list", parts[1], *known, "--not", "--remotes"]).split():
            shown = _git(["show", "-U0", "--no-color", "--no-ext-diff", "--format=%B", sha])
            message, _sep, diff = shown.partition("\ndiff --git ")
            found = _finding(message.splitlines(), "the commit message") or _diff_finding(diff)
            if found:
                return f"foil: push refused: commit {sha[:12]} adds {found}"
    return None


def _refusal(name: str, data: bytes) -> str | None:
    """Checks run only in the project's own repository, not in one a seat makes or tests."""

    here = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
    project = os.environ.get("FOIL_GIT_COMMON", "")
    if name not in CHECKED or not here or not project or Path(here).resolve() != Path(project):
        return None
    return _pre_push(data.decode("utf-8", "replace")) if name == "pre-push" else _pre_commit()


def main(argv: list[str]) -> int:
    name, arguments = argv[0], argv[1:]
    data = sys.stdin.buffer.read()
    refused = _refusal(name, data)
    if refused:
        print(refused, file=sys.stderr)
        return 1
    # Then the hook this repository would have run without Foil.
    found = _git(["rev-parse", "--git-path", "hooks"]).strip()
    original = Path(found or "/nonexistent").resolve() / name
    ours = Path(os.environ.get("GIT_CONFIG_VALUE_0") or "/nonexistent").resolve()
    if found and os.access(original, os.X_OK) and original.parent != ours:
        done = subprocess.run(
            [str(original), *arguments], input=data, env=_plain_environment(), check=False
        )
        return done.returncode
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in HOOKS:
    raise SystemExit(main(sys.argv[1:]))
