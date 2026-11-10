#!/usr/bin/env python3
"""Test double for a seat CLI. Not part of the foil package.

It runs in the seat's pane, reads typed lines, and follows a script at
`<fleet>/run/fake/<seat>.json`:

    {
      "exit_on_input": false,
      "start": [ACTION, ...],
      "steps": [{"when": {CONDITION, ...}, "do": [ACTION, ...]}, ...]
    }

`start` runs on every launch, before the double says it is ready. A step
runs when every condition in its `when` holds.

Conditions:
    {"mail_contains": TEXT}  the nudged mail or room file contains TEXT
    {"launch": KIND}         this launch is "first", "resume", "fresh" or
                             "lost"; a list names several
    {"quiet_for": SECONDS}   no line was typed for that long; runs once

Actions:
    {"sh": COMMAND}          run a shell command; a failure stops the step
    {"write": PATH, "text": TEXT}
    {"send": SEAT, "text": TEXT}
    {"wait": SECONDS}
    {"exit": CODE}           the process ends there, in the middle of the step
    {"hang": true}           stay alive and never read another line
    {"dialog": TEXT, "accept": LINE}
                             print TEXT and take typed lines until LINE; any
                             other line ends the process with code 1

A step that does not finish leaves its mail unhandled, so the next launch
sees it again. A worker that stays silent is a script with no `send`, and
a slow start is a `wait` in `start`.

A launch is "first" when this seat never ran, "resume" when it is given
back a session it had, and "fresh" when it ran before and gets a new one.
`--session ID` starts a session and `--resume ID` continues one. Starting
an id that is already in use is an error. Resuming an id this seat never
had is the launch "lost": the double carries on with nothing remembered.
With `"strict_resume": true` in the script it ends with an error there,
as a real CLI does for a session it never saved.

A shell action sees FAKE_BOARD, FAKE_LAUNCH, FAKE_SESSION and FAKE_MAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path


def fleet_root(prompt: str) -> Path:
    marker = "/run/instructions/"
    index = prompt.find(marker)
    if index >= 0:
        left = index
        while left > 0 and prompt[left - 1] not in " \n\t`\"'":
            left -= 1
        return Path(prompt[left:index])
    named = os.environ.get("FOIL_PROJECT", "").strip()
    home = os.environ.get("FOIL_HOME", "").strip()
    fleet = os.environ.get("FOIL_FLEET", "").strip() or "main"
    if named and home:
        candidate = Path(home) / "projects" / named
        fleet_dir = candidate / "fleets" / fleet
        if (fleet_dir / "board").is_dir() or (fleet_dir / "run").is_dir():
            return fleet_dir
        if candidate.is_dir():
            return candidate
    for parent in (Path.cwd(), *Path.cwd().parents):
        candidate = parent / ".foil"
        if candidate.is_dir():
            return candidate
    raise SystemExit("foil-fake: cannot find the project folder")


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def script(fleet: Path, seat: str) -> dict:
    path = fleet / "run" / "fake" / f"{seat}.json"
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


class Sessions:
    """What this seat was launched with, one JSON line per launch."""

    def __init__(self, fleet: Path, seat: str) -> None:
        self.path = fleet / "run" / "fake" / f"{seat}.launches"

    def all(self) -> list[dict]:
        return [json.loads(line) for line in _lines(self.path)]

    def known(self, session: str) -> bool:
        return any(item["session"] == session for item in self.all())

    def last_in(self, cwd: str) -> str:
        """The newest session started in this directory, or ""."""

        found = [item["session"] for item in self.all() if item["cwd"] == cwd]
        return found[-1] if found else ""

    def record(self, session: str, launch: str, facts: dict[str, str]) -> None:
        entry = {"session": session, "launch": launch, "cwd": str(Path.cwd()), **facts}
        _append(self.path, json.dumps(entry, sort_keys=True))


class _Typed:
    """Lines typed into the pane. A wait that ends with no line gives None."""

    def __init__(self) -> None:
        self._buffer = b""
        self._closed = False

    def read(self, timeout: float | None) -> str | None:
        while b"\n" not in self._buffer:
            if self._closed:
                raise EOFError
            ready, _, _ = select.select([0], [], [], timeout)
            if not ready:
                return None
            chunk = os.read(0, 4096)
            if not chunk:
                self._closed = True
                if not self._buffer:
                    raise EOFError
                self._buffer += b"\n"
            self._buffer += chunk
        raw, _, self._buffer = self._buffer.partition(b"\n")
        return raw.decode("utf-8", errors="replace")


class Fake:
    def __init__(
        self,
        seat: str,
        fleet: Path,
        *,
        session: str,
        launch: str,
        name: str = "foil-fake",
        facts: dict[str, str] | None = None,
    ) -> None:
        self.seat = seat
        self.fleet = fleet
        self.session = session
        self.launch = launch
        self.name = name
        self.facts = facts or {}
        directory = fleet / "run" / "fake"
        self.log_path = directory / f"{seat}.log"
        self.handled_path = directory / f"{seat}.handled"
        self.handled = set(_lines(self.handled_path))
        self.typed = _Typed()
        payload = script(fleet, seat)
        self.steps = [item for item in payload.get("steps") or [] if isinstance(item, dict)]
        self.start = [item for item in payload.get("start") or [] if isinstance(item, dict)]
        self.exit_on_input = payload.get("exit_on_input") is True

    def log(self, message: str) -> None:
        _append(self.log_path, message)

    def _target(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute():
            return path
        if relative.startswith("board/"):
            return self.fleet / relative
        return Path.cwd() / path

    def _act(self, action: dict, mail: str) -> bool:
        if "sh" in action:
            environment = {
                **os.environ,
                "FAKE_BOARD": str(self.fleet / "board"),
                "FAKE_LAUNCH": self.launch,
                "FAKE_SESSION": self.session,
                "FAKE_MAIL": mail,
            }
            result = subprocess.run(
                action["sh"],
                shell=True,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.log(f"sh {result.returncode} {action['sh']}\n{result.stdout}{result.stderr}")
            return result.returncode == 0
        if "write" in action:
            target = self._target(str(action["write"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(action.get("text", "")), encoding="utf-8")
            self.log(f"write {target}")
            return True
        if "send" in action:
            result = subprocess.run(
                ["foil", "send", str(action["send"]), str(action.get("text", ""))],
                check=False,
                capture_output=True,
                text=True,
            )
            self.log(f"send {result.returncode} {action['send']}\n{result.stdout}{result.stderr}")
            return result.returncode == 0
        if "wait" in action:
            time.sleep(float(action["wait"]))
            self.log(f"wait {action['wait']}")
            return True
        if "exit" in action:
            self.log(f"exit {action['exit']}")
            print(f"{self.name}: exit {action['exit']}", flush=True)
            raise SystemExit(int(action["exit"]))
        if "hang" in action:
            self.log("hang")
            while True:
                time.sleep(3600)
        if "dialog" in action:
            return self._dialog(str(action["dialog"]), str(action.get("accept", "")))
        self.log(f"unknown action {action}")
        return False

    def _dialog(self, text: str, accept: str) -> bool:
        print(text, flush=True)
        self.log(f"dialog {text}")
        while True:
            try:
                line = self.typed.read(None)
            except EOFError:
                raise SystemExit(0) from None
            if line is None or not line.strip():
                continue
            if line.strip() == accept:
                self.log("dialog accepted")
                return True
            self.log(f"dialog refused by {line.strip()}")
            print(f"{self.name}: not an answer", flush=True)
            raise SystemExit(1)

    def _matches(self, when: dict, text: str | None) -> bool:
        wanted = when.get("launch")
        if wanted is not None:
            kinds = wanted if isinstance(wanted, list) else [wanted]
            if self.launch not in kinds:
                return False
        needle = str(when.get("mail_contains", ""))
        if text is None:
            return not needle
        return bool(needle) and needle in text

    def _do(self, actions: list, mail: str) -> bool:
        return all(isinstance(item, dict) and self._act(item, mail) for item in actions)

    def _mark(self, key: str) -> None:
        _append(self.handled_path, key)
        self.handled.add(key)

    def _handle(self, mail: Path) -> None:
        key = str(mail.resolve())
        if key in self.handled or not mail.is_file():
            return
        text = mail.read_text(encoding="utf-8")
        self.log(f"mail {key}")
        for step in self.steps:
            when = step.get("when") or {}
            if "quiet_for" in when or not self._matches(when, text):
                continue
            if not self._do(step.get("do") or [], key):
                self.log(f"stopped {key}")
                return
        self._mark(key)

    def _quiet(self) -> Iterator[tuple[float, str, dict]]:
        for index, step in enumerate(self.steps):
            when = step.get("when") or {}
            key = f"quiet:{index}"
            if "quiet_for" in when and key not in self.handled and self._matches(when, None):
                yield float(when["quiet_for"]), key, step

    def _fire_quiet(self, since: float) -> float | None:
        """Run each quiet step that is due. Return the seconds until the next one."""

        left: float | None = None
        for seconds, key, step in list(self._quiet()):
            remaining = since + seconds - time.monotonic()
            if remaining > 0:
                left = remaining if left is None else min(left, remaining)
                continue
            self.log(f"quiet {seconds}")
            self._mark(key)
            self._do(step.get("do") or [], "")
        return left

    def run(self) -> int:
        self.log(f"launch {self.launch} session={self.session}")
        if not self._do(self.start, ""):
            self.log("start stopped")
        print(f"{self.name} {self.seat} ready", flush=True)
        self.log("ready")
        # Startup re-reads this seat's mailbox only. Room files are not scanned:
        # a hand-written room file wakes nobody, and a decline types nothing.
        mailbox = self.fleet / "board" / "mail" / self.seat
        if mailbox.is_dir():
            for mail in sorted(mailbox.glob("*.md")):
                self._handle(mail)
        since = time.monotonic()
        while True:
            try:
                raw = self.typed.read(self._fire_quiet(since))
            except EOFError:
                return 0
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                continue
            since = time.monotonic()
            if self.exit_on_input:
                return 0
            print(line, flush=True)
            nudged = _nudged_path(line)
            if nudged is not None:
                self._handle(nudged)
                since = time.monotonic()


def _nudged_path(line: str) -> Path | None:
    """The file named by a nudge. Mail and room use one line shape."""

    sender, separator, raw = line.partition(" ")
    if not separator or not sender.strip() or not raw.strip():
        return None
    path = Path(raw.strip())
    if not path.is_absolute():
        return None
    return path


def seat_id(name: str) -> str:
    seat = os.environ.get("FOIL_SEAT_ID", "")
    if not seat:
        print(f"{name}: FOIL_SEAT_ID is unset", file=sys.stderr)
        raise SystemExit(1)
    return seat


def refuse(name: str, seat: str, fleet: Path, message: str) -> None:
    """End a launch the CLI would not accept. The pane and the log both say why."""

    _append(fleet / "run" / "fake" / f"{seat}.log", f"refused: {message}")
    print(f"{name}: {message}", file=sys.stderr, flush=True)
    raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(prog="foil-fake")
    kind = parser.add_mutually_exclusive_group()
    kind.add_argument("--session", default="")
    kind.add_argument("--resume")
    parser.add_argument("--prompt", default="")
    args = parser.parse_args()
    seat = seat_id("foil-fake")
    fleet = fleet_root(args.prompt)
    sessions = Sessions(fleet, seat)
    if args.resume is not None:
        session, launch = args.resume, "resume"
        if not sessions.known(session):
            if script(fleet, seat).get("strict_resume") is True:
                refuse("foil-fake", seat, fleet, f"no session '{session}' to resume")
            launch = "lost"
    else:
        if args.session and sessions.known(args.session):
            refuse("foil-fake", seat, fleet, f"session '{args.session}' is already in use")
        session, launch = args.session, ("fresh" if sessions.all() else "first")
    sessions.record(session, launch, {"cli": "foil-fake"})
    return Fake(seat, fleet, session=session, launch=launch).run()


if __name__ == "__main__":
    raise SystemExit(main())
