"""Seat-only git hooks: keep private text out of commits, and a seat inside its worktree."""

from __future__ import annotations

import os
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path

from foil.project import foil_root
from foil.store import private_dir, write_bytes

HOOKS = ("pre-commit", "pre-push")
_TOKEN = re.compile(
    r"gh[oprsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}"
    r"|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _git(args: list[str], cwd: Path | None = None) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace", check=False
    )
    return done.stdout if done.returncode == 0 else ""


def hook_environment(toplevel: Path, cwd: Path, worktree: str) -> dict[str, str]:
    """Point a seat's git at Foil's hooks. The repository's own hooks run after them."""

    directory = foil_root(toplevel) / "hooks"
    private_dir(directory)
    for name in HOOKS:
        path = directory / name
        script = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -I -m foil.guard {name} "$@"\n'
        write_bytes(path, script.encode())
        path.chmod(0o700)
    found = _git(["rev-parse", "--git-path", "hooks"], cwd).strip()
    environment = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(directory.resolve()),
        "FOIL_REPO_HOOKS": str((cwd / found).resolve()) if found else "",
    }
    if worktree:
        environment["FOIL_WORKTREE"] = str(Path(worktree).resolve())
    return environment


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
        for kind, word in words:
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
        seat = os.environ.get("FOIL_SEAT_ID", "")
        return f"foil: commit refused: seat {seat} works in {worktree}, not in {top}"
    found = _diff_finding(_git(["diff", "--cached", "-U0", "--no-color", "--no-ext-diff"]))
    return f"foil: commit refused: the staged change adds {found}" if found else None


def _pre_push(updates: str) -> str | None:
    for update in updates.splitlines():
        parts = update.split()
        if len(parts) != 4 or set(parts[1]) == {"0"}:
            continue
        for sha in _git(["rev-list", parts[1], "--not", "--remotes"]).split():
            shown = _git(["show", "-U0", "--no-color", "--no-ext-diff", "--format=%B", sha])
            message, _sep, diff = shown.partition("\ndiff --git ")
            found = _finding(message.splitlines(), "the commit message") or _diff_finding(diff)
            if found:
                return f"foil: push refused: commit {sha[:12]} adds {found}"
    return None


def main(argv: list[str]) -> int:
    name, arguments = argv[0], argv[1:]
    data = sys.stdin.buffer.read() if name == "pre-push" else b""
    refused = _pre_push(data.decode("utf-8", "replace")) if name == "pre-push" else _pre_commit()
    if refused:
        print(refused, file=sys.stderr)
        return 1
    # Then the hook the repository would have run without Foil.
    original = Path(os.environ.get("FOIL_REPO_HOOKS") or "/nonexistent") / name
    ours = Path(os.environ.get("GIT_CONFIG_VALUE_0") or "/nonexistent")
    if os.access(original, os.X_OK) and original.parent.resolve() != ours.resolve():
        return subprocess.run([str(original), *arguments], input=data, check=False).returncode
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in HOOKS:
    raise SystemExit(main(sys.argv[1:]))
