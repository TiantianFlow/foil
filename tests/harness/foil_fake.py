#!/usr/bin/env python3
"""Test double for a headless seat CLI: one run is one turn. Not part of the foil package.

As `foil-fake` it takes `--session ID PROMPT` for a first turn and
`--resume ID PROMPT` for a later one. Linked as `claude`, `codex`, `gemini`,
`opencode`, or `grok`, it accepts that preset's argv and, for the two
output-session presets, prints the session id the way that CLI does.

The script is `run/fake/<seat>.json`: `steps` run when a listed mail or room
file contains `mail_contains`, and `faults` apply to turns in order. Each turn's
argv, prompt, and stdin go to `run/fake/<seat>.turns/<n>.json` for tests to check;
`<seat>.log` is for debugging only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

_PATH = re.compile(r"(/[^\s`'\"]+\.md)")
_STAND_INS = {"claude", "codex", "gemini", "opencode", "grok"}


def _foil_root(prompt: str) -> Path:
    marker = "/run/instructions/"
    index = prompt.find(marker)
    if index >= 0:
        left = index
        while left > 0 and prompt[left - 1] not in " \n\t`\"'":
            left -= 1
        return Path(prompt[left:index])
    raise SystemExit("foil-fake: the prompt names no instruction file")


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _script(path: Path) -> dict:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_target(foil: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute():
        return path
    if relative.startswith("board/"):
        return foil / relative
    return Path.cwd() / path


def _run_action(foil: Path, action: dict, log_path: Path) -> bool:
    if "sh" in action:
        result = subprocess.run(
            action["sh"], shell=True, check=False, capture_output=True, text=True
        )
        _log(log_path, f"sh {result.returncode} {action['sh']}\n{result.stdout}{result.stderr}")
        return result.returncode == 0
    if "write" in action:
        target = _write_target(foil, str(action["write"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(action.get("text", "")), encoding="utf-8")
        _log(log_path, f"write {target}")
        return True
    if "send" in action:
        result = subprocess.run(
            ["foil", "send", str(action["send"]), str(action.get("text", ""))], check=False
        )
        _log(log_path, f"send {result.returncode} {action['send']}")
        return result.returncode == 0
    if "wait" in action:
        time.sleep(float(action["wait"]))
        _log(log_path, f"wait {action['wait']}")
        return True
    _log(log_path, f"unknown action {action}")
    return False


def _handle(path: Path, *, foil: Path, steps: list, handled: set[str], fake: Path, seat: str,
            prompt: str) -> None:
    key = str(path.resolve())
    log_path = fake / f"{seat}.log"
    inline = f"--- {path}" in prompt
    if key in handled:
        _log(log_path, f"again {key}")
        return
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    _log(log_path, f"mail {key}" + (" inline" if inline else ""))
    print(f"foil-fake {seat} mail {key}", flush=True)
    os.environ["FOIL_FAKE_ITEM"] = key
    for step in steps:
        when = step.get("when") or {}
        needle = str(when.get("mail_contains", ""))
        if not needle or needle not in text:
            continue
        for action in step.get("do") or []:
            if not isinstance(action, dict) or not _run_action(foil, action, log_path):
                _log(log_path, f"stopped {key}")
                return
    with (fake / f"{seat}.handled").open("a", encoding="utf-8") as handle:
        handle.write(key + "\n")
    handled.add(key)


# Per stand-in: flags that take a value, flags that take none, and leading words.
_SHAPES = {
    "claude": ({"-p", "--disallowedTools", "--session-id", "--resume", "--model",
                "--permission-mode"}, set(), []),
    "gemini": ({"-p", "--session-id", "--resume", "--model", "--approval-mode"},
               {"--skip-trust"}, []),
    "grok": ({"-p", "--session-id", "--resume", "--model"}, {"--always-approve"}, []),
    "codex": ({"--model"}, {"--json", "--dangerously-bypass-approvals-and-sandbox"}, ["exec"]),
    "opencode": ({"--format", "--session", "--model"}, {"--auto"}, ["run"]),
}


def _flags(name: str, argv: list[str]) -> tuple[dict[str, str], set[str], list[str]]:
    valued, bare, words = _SHAPES[name]
    if argv[: len(words)] != words:
        raise SystemExit(f"{name} stand-in: expected {' '.join(words)}")
    values: dict[str, str] = {}
    flags: set[str] = set()
    positional: list[str] = []
    rest = argv[len(words):]
    index = 0
    while index < len(rest):
        token = rest[index]
        if token in valued and index + 1 < len(rest) and token not in values:
            values[token] = rest[index + 1]
            index += 2
            continue
        if token in bare and token not in flags:
            flags.add(token)
        elif token.startswith("-"):
            raise SystemExit(f"{name} stand-in: unknown flag {token}")
        else:
            positional.append(token)
        index += 1
    return values, flags, positional


def _parse(name: str, argv: list[str]) -> tuple[str, str, bool, list[str]]:
    """(prompt, session id, first turn, argv to log) for the fake or a stand-in's argv."""

    if name == "foil-fake":
        if len(argv) != 3 or argv[0] not in ("--session", "--resume"):
            raise SystemExit("foil-fake: usage: --session|--resume ID PROMPT")
        return argv[2], argv[1], argv[0] == "--session", []
    values, flags, positional = _flags(name, argv)
    if name in ("claude", "gemini", "grok"):
        if positional or "-p" not in values:
            raise SystemExit(f"{name} stand-in: the prompt goes in -p")
        if ("--session-id" in values) == ("--resume" in values):
            raise SystemExit(f"{name} stand-in: one of --session-id and --resume")
        if name == "claude" and values.get("--disallowedTools") != "AskUserQuestion":
            raise SystemExit("claude stand-in: --disallowedTools AskUserQuestion")
        if name == "gemini" and "--skip-trust" not in flags:
            raise SystemExit("gemini stand-in: --skip-trust")
        session = values.get("--session-id") or values["--resume"]
        return values["-p"], session, "--session-id" in values, [name, *argv]
    if name == "codex":
        if values.get("--json", "") or "--json" not in flags:
            raise SystemExit("codex stand-in: --json")
        if len(positional) == 1:
            return positional[0], "", True, [name, *argv]
        if len(positional) == 3 and positional[0] == "resume":
            return positional[2], positional[1], False, [name, *argv]
        raise SystemExit("codex stand-in: PROMPT or resume ID PROMPT")
    if values.get("--format") != "json" or len(positional) != 1:
        raise SystemExit("opencode stand-in: --format json and one PROMPT")
    session = values.get("--session", "")
    return positional[0], session, not session, [name, *argv]


def main() -> int:
    name = Path(sys.argv[0]).name
    if name not in _STAND_INS:
        name = "foil-fake"
    prompt, session, first, flags = _parse(name, sys.argv[1:])
    seat = os.environ.get("FOIL_SEAT_ID", "")
    if not seat:
        print("foil-fake: FOIL_SEAT_ID is unset", file=sys.stderr)
        return 1
    foil = _foil_root(prompt)
    fake = foil / "run" / "fake"
    log_path = fake / f"{seat}.log"
    fake.mkdir(parents=True, exist_ok=True)
    if flags:
        _log(log_path, "argv " + json.dumps(flags))
    if not session:
        session = str(uuid.uuid4())
        key = "thread_id" if name == "codex" else "sessionID"
        print(json.dumps({"type": "session", key: session}), flush=True)
    sessions_path = fake / f"{seat}.sessions"
    known = set(sessions_path.read_text().split()) if sessions_path.is_file() else set()
    if first:
        known.add(session)
        sessions_path.write_text("\n".join(sorted(known)) + "\n")
    elif session not in known:
        _log(log_path, f"unknown session {session}")
        print(f"foil-fake: no session {session}", file=sys.stderr)
        return 9
    # Faults count every turn of the seat; the printed number counts this session's turns.
    counts_path = fake / f"{seat}.turns.json"
    counts = json.loads(counts_path.read_text()) if counts_path.is_file() else {}
    turn = counts.get("", 0)
    number = counts.get(session, 0)
    counts.update({"": turn + 1, session: number + 1})
    counts_path.write_text(json.dumps(counts))
    stdin = sys.stdin.read() if not sys.stdin.isatty() else "tty"
    _log(log_path, f"turn {number} {'start' if first else 'resume'} session={session} "
         f"stdin={'empty' if stdin == '' else 'open'}")
    print(f"foil-fake {seat} turn {number}", flush=True)
    record = {
        "argv": [name, *sys.argv[1:]],
        "prompt": prompt,
        "session": session,
        "first": first,
        "stdin": stdin,
        "cwd": str(Path.cwd()),
    }
    (fake / f"{seat}.turns").mkdir(exist_ok=True)
    (fake / f"{seat}.turns" / f"{turn:03d}.json").write_text(json.dumps(record))
    script = _script(fake / f"{seat}.json")
    faults = script.get("faults") or []
    fault = faults[turn] if turn < len(faults) and isinstance(faults[turn], dict) else {}
    if fault.get("leave_child"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        (fake / f"{seat}.child").write_text(str(child.pid))
    if fault.get("hang"):
        time.sleep(3600)
    if "delay" in fault:
        time.sleep(float(fault["delay"]))
    if "exit" in fault:
        _log(log_path, f"exit {fault['exit']}")
        return int(fault["exit"])
    if fault.get("silent"):
        _log(log_path, "silent")
        return 0
    handled_path = fake / f"{seat}.handled"
    handled = set(handled_path.read_text().split()) if handled_path.is_file() else set()
    board = foil / "board"
    for raw in dict.fromkeys(_PATH.findall(prompt)):
        path = Path(raw)
        try:
            relative = path.relative_to(board).as_posix()
        except ValueError:
            continue
        if relative.startswith((f"mail/{seat}/", "room/")):
            _handle(path, foil=foil, steps=script.get("steps") or [], handled=handled,
                    fake=fake, seat=seat, prompt=prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
