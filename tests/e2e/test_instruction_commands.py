"""What a seat is told to run, run as that seat.

A seat's instruction file names `foil` commands in three ways: the list of
commands it may run, the example lines of its skill, and code spans in
prose. This test takes every one of them from the file Foil wrote for a
real seat and runs it in that seat's fleet, with the environment and the
working directory of that seat's launch plan. A command the parser does
not know, or one Foil refuses for that caller, fails the test.

A line with a placeholder (`NAME`, `PATH`) gets a value that fits the
fleet. A synopsis with optional parts runs once without them and once
with each. A mention that is only a command name (`foil mail read`) is
completed with the arguments the parser asks for, so it is checked as a
command, not as an exact line. The few commands a file names in order to
forbid them are listed in REFUSED, and they must be refused.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import foil.tmux
from foil.cli import _build_parser, main
from foil.store import load_registry
from tests.e2e.support import board, close, fleet_dir
from tests.e2e.test_scenarios import _prepare

pytestmark = pytest.mark.e2e

# kind of seat -> the seat that plays it, and a seat it writes to.
KINDS = {"lead": ("lead", "implementer-1"), "worker": ("implementer-1", "lead")}
# Commands a file names only to say the seat may not run them.
REFUSED = {
    "lead": {"foil seat kill --all": "foil: not allowed\n", "foil init": "foil: not allowed\n"},
    "worker": {},
}
_SPAN = re.compile(r"`(foil [^`\n]+)`")
_OPTIONAL = re.compile(r"\s*\[([^\]]+)\]")
_REQUIRED = re.compile(r"the following arguments are required: (.+)")
_VALUE = re.compile(r"argument (--[a-z]+): expected one argument")
_GROUPS = ("seat", "mail", "board", "memory", "roster")


def mentions(text: str) -> list[str]:
    """Every `foil` command the text names: command lines and code spans."""

    commands = "|".join(_build_parser()._subparsers._group_actions[0].choices)
    named = re.compile(rf"\bfoil (?:{commands})\b")
    found: list[str] = []
    for line in text.splitlines():
        spans = [line.strip()] if line.startswith("foil ") else _SPAN.findall(line)
        outside = line
        for span in spans:
            outside = outside.replace(span, "")
        # A command the two shapes above would miss is a gap in this test.
        assert not named.search(outside), f"a command outside a code span: {line}"
        found.extend(span for span in spans if span not in found)
    return found


def variants(mention: str) -> list[list[str]]:
    """The line without its optional parts, then with each one."""

    base = shlex.split(_OPTIONAL.sub("", mention))
    lines = [base]
    for part in _OPTIONAL.findall(mention):
        lines.append([*base, *shlex.split(part)])
    return [line[1:] for line in lines]


def _parse(argv: list[str]) -> tuple[object | None, str]:
    error = io.StringIO()
    try:
        with contextlib.redirect_stderr(error):
            return _build_parser().parse_args(argv), ""
    except SystemExit:
        return None, error.getvalue()


@dataclass
class Fleet:
    repo: Path
    seat: str
    peer: str
    count: int = 0
    failures: list[str] = field(default_factory=list)

    def outside(self, *argv: str) -> str:
        """Run as the caller outside the fleet, which sets the stage for each command."""

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(list(argv)) == 0, argv
        return out.getvalue()

    @contextlib.contextmanager
    def as_seat(self) -> Iterator[None]:
        plan = json.loads(
            (fleet_dir(self.repo) / "run" / "plans" / f"{self.seat}.json").read_text("utf-8")
        )
        saved, here = dict(os.environ), Path.cwd()
        os.environ.update(plan["env"])
        os.chdir(plan["cwd"])
        try:
            yield
        finally:
            os.chdir(here)
            os.environ.clear()
            os.environ.update(saved)

    def run(self, argv: list[str]) -> tuple[int, str]:
        error = io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO("typed on stdin\n")
        try:
            with (
                self.as_seat(),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(error),
            ):
                code = main(argv)
        finally:
            sys.stdin = stdin
        return code, error.getvalue()

    def _fresh(self) -> str:
        self.count += 1
        return f"spare-{self.count}"

    def _room(self) -> str:
        """A new open room the seat is in."""

        self.outside("post", "--members", self.seat, f"room {self._fresh()}")
        rooms = [path for path in (board(self.repo) / "room").iterdir() if path.is_dir()]
        newest = max(rooms, key=lambda path: path.stat().st_mtime_ns)
        return str((newest / f"{newest.name}.md").resolve())

    def _mail(self) -> str:
        box = board(self.repo) / "mail" / self.seat
        return str(sorted(box.glob("*.md"))[0].resolve())

    def _lesson(self, *, accepted: bool) -> str:
        lesson = self.outside("memory", "add", f"lesson {self._fresh()}").strip()
        if accepted:
            self.outside("memory", "accept", lesson)
        return lesson

    def _value(self, argv: list[str], index: int | None, name: str) -> str:
        """A value for a placeholder, from the flag before it or from its command."""

        before = argv[index - 1] if index else ""
        command = " ".join(argv[:2] if argv[0] in _GROUPS else argv[:1])
        if before == "--name":
            return self._fresh()
        if before == "--re" or name == "/absolute/path/of/old/room":
            return self._room()
        if before == "--replaces":
            return self._lesson(accepted=True)
        if before == "--lines":
            return "5"
        if before == "--members" or name in {"NAME", "NAMES"}:
            return self.peer if command == "seat peek" else "spare"
        if name == "ID":
            return self._lesson(accepted=False)
        if name == "PATH" or name.startswith("/absolute/path/"):
            return self._mail() if command == "mail read" else "status.md"
        fixed = {
            "TEMPLATE": "reviewer",
            "TO": self.peer,
            "PATTERN": "tasks/*.md",
            # A list of two goes on one role, so the other keeps a list `model=` can edit.
            "ROLE": "reviewer" if any(item.startswith("harness=") for item in argv) else "lead",
            "FIELD=VALUE": "model=sonnet",
            "model=VALUE": "model=sonnet",
            "harness=ID/MODEL,ID/MODEL": "harness=fake/one,fake/two",
        }
        return fixed.get(name, "text")

    def complete(self, argv: list[str]) -> list[str] | str:
        """A line the parser takes: placeholders filled, missing arguments added.

        A line it does not take gives the parser's last complaint.
        """

        placeholders = re.compile(r"[A-Z]+(=[A-Z]+)?|/absolute/path/\S+|\w+=[A-Z/,]+")
        line = list(argv)
        for index, token in enumerate(line):
            if index and placeholders.fullmatch(token):
                line[index] = self._value(line, index, token)
        for _attempt in range(4):
            parsed, error = _parse(line)
            if parsed is not None:
                break
            missing, value = _REQUIRED.search(error), _VALUE.search(error)
            if missing:
                line += [self._value(line, None, name) for name in missing.group(1).split(", ")]
            elif value and line[-1] == value.group(1):
                line.append(self._value(line, len(line), ""))
            else:
                return error.strip()
        else:
            return error.strip()
        if line[0] == "post":
            # Usage, not authority: a decline or a close replies, and a post has text.
            bare = len(line) == 1
            if (bare or parsed.decline or parsed.close) and parsed.re is None:
                line += ["--re", self._room()]
            if not parsed.decline and parsed.TEXT is None:
                line.append("text")
        return line

    def _need(self, name: str, *, killed: bool = False) -> None:
        if name in {"user", self.seat, "lead"}:
            return
        record = load_registry(self.repo)["seats"].get(name)
        role = "implementer" if name.startswith("implementer") else "reviewer"
        if record is not None and record["state"] == "killed" and not killed:
            self.outside("seat", "archive", name)
            record = None
        if record is None:
            self.outside("seat", "spawn", role, "--name", name)
        if killed and (record is None or record["state"] != "killed"):
            self.outside("seat", "kill", name)

    def stage(self, argv: list[str]) -> None:
        """Put the fleet in a state where the command makes sense."""

        parsed, _error = _parse(argv)
        command = " ".join(argv[:2] if argv[0] in _GROUPS else argv[:1])
        if command == "seat archive":
            self._need(parsed.NAME, killed=True)
        elif command in {"seat kill", "seat resume", "seat peek"} and parsed.NAME:
            self._need(parsed.NAME)
        elif command == "seat spawn" and parsed.name:
            record = load_registry(self.repo)["seats"].get(parsed.name)
            if record is not None:
                self._need(parsed.name, killed=True)
                self.outside("seat", "archive", parsed.name)
        elif command == "send":
            self._need(parsed.TO)
        elif command == "post" and parsed.members:
            for name in parsed.members.split(","):
                self._need(name)

    def check(self, mention: str, refused: dict[str, str]) -> None:
        for argv in variants(mention):
            line = self.complete(argv)
            if isinstance(line, str):
                self.failures.append(f"`{mention}` is not a command: {line}")
                continue
            if mention not in refused:
                self.stage(line)
            code, error = self.run(line)
            shown = f"`{mention}` ran as `foil {shlex.join(line)}`: exit {code}: {error.strip()}"
            if mention in refused:
                if (code, error) != (1, refused[mention]):
                    self.failures.append(f"{shown}, and the file says it is not allowed")
            elif code != 0:
                self.failures.append(shown)


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_every_command_in_an_instruction_file_runs_for_that_seat(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seat, peer = KINDS[kind]
    # This test is about who may run what. The pause before Enter is not part of it.
    monkeypatch.setattr(foil.tmux, "NUDGE_ENTER_DELAY", 0)
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        assert main(["seat", "spawn", "lead", "--task", "the goal"]) == 0
        assert main(["seat", "spawn", "implementer", "--task", "board/tasks/t1.md"]) == 0
        for name in ("status.md", "tasks/t1.md"):
            (board(repo) / name).parent.mkdir(parents=True, exist_ok=True)
            (board(repo) / name).write_text("---\nstate: working\n---\n\nText.\n", encoding="utf-8")
        text = (fleet_dir(repo) / "run" / "instructions" / f"{seat}.md").read_text("utf-8")
        named = mentions(text)
        assert len(named) > 20, named
        refused = REFUSED[kind]
        assert set(refused) <= set(named), "REFUSED names a command the file no longer has"
        fleet = Fleet(repo, seat, peer)
        for mention in named:
            fleet.check(mention, refused)
        assert not fleet.failures, "\n".join(fleet.failures)
    finally:
        close(repo)
