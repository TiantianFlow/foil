#!/usr/bin/env python3
"""PATH stand-ins for the shipped grok and opencode adapter argv contracts.

CI cannot authenticate real agent CLIs. These binaries keep the same names,
flags, session-capture shapes, and idle-at-prompt behavior so Foil's generated
runtime.toml and builtin adapters are exercised unchanged.

Foil's runner sanitizes the seat environment, so the shim directory carries an
`agent-home` file rather than relying on FOIL_E2E_* variables inside tmux.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import suppress
from pathlib import Path


def _home() -> Path:
    raw = os.environ.get("FOIL_E2E_AGENT_HOME")
    if not raw:
        marker = Path(sys.argv[0]).resolve().parent / "agent-home"
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
    if not raw:
        raise SystemExit("FOIL_E2E_AGENT_HOME is required")
    path = Path(raw)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log(home: Path, name: str, args: list[str]) -> None:
    record = {
        "cli": name,
        "argv": args,
        "cwd": str(Path.cwd()),
        "pid": os.getpid(),
    }
    with (home / "invocations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def _sessions_path(home: Path) -> Path:
    return home / "opencode-sessions.json"


def _load_sessions(home: Path) -> list[dict[str, str]]:
    path = _sessions_path(home)
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _save_sessions(home: Path, sessions: list[dict[str, str]]) -> None:
    _sessions_path(home).write_text(json.dumps(sessions), encoding="utf-8")


def _idle(home: Path, name: str, args: list[str]) -> None:
    marker = home / f"ready-{name}-{os.getpid()}.json"
    marker.write_text(
        json.dumps(
            {
                "cli": name,
                "argv": args,
                "cwd": str(Path.cwd()),
                "pid": os.getpid(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    stdin_log = home / "stdin.jsonl"
    while True:
        line = sys.stdin.readline()
        if line == "":
            time.sleep(0.25)
            continue
        with stdin_log.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"cli": name, "line": line, "pid": os.getpid()}) + "\n"
            )


def _run_opencode(home: Path, args: list[str]) -> int:
    if args == ["--version"]:
        print("1.18.21")
        return 0
    if args[:4] == ["session", "list", "--format", "json"]:
        print(json.dumps(_load_sessions(home)))
        return 0
    if args[:2] == ["usage", "--format"] or args == ["usage", "--json"]:
        payload_path = home / "usage-opencode.json"
        if payload_path.is_file():
            print(payload_path.read_text(encoding="utf-8"))
        else:
            print(json.dumps({"availability": "ok", "active_load": 0}))
        return 0
    if not args or args[0] != ".":
        print("unsupported opencode argv", file=sys.stderr)
        return 2
    if "--session" not in args:
        delay_path = home / "opencode-capture-delay-seconds"
        if delay_path.is_file():
            with suppress(ValueError):
                time.sleep(float(delay_path.read_text(encoding="utf-8").strip() or "0"))
        session_id = f"oc-{uuid.uuid4().hex[:16]}"
        sessions = _load_sessions(home)
        sessions.append({"id": session_id, "directory": str(Path.cwd())})
        _save_sessions(home, sessions)
    _idle(home, "opencode", args)
    return 0


def _run_grok(home: Path, args: list[str]) -> int:
    if args == ["--version"]:
        print("1.0.5")
        return 0
    if args[:2] == ["usage", "--format"] or args == ["usage", "--json"]:
        payload_path = home / "usage-grok.json"
        if payload_path.is_file():
            print(payload_path.read_text(encoding="utf-8"))
        else:
            print(json.dumps({"availability": "ok", "active_load": 0}))
        return 0
    if "--session-id" not in args and "--resume" not in args:
        print("unsupported grok argv", file=sys.stderr)
        return 2
    _idle(home, "grok", args)
    return 0


def main() -> int:
    name = Path(sys.argv[0]).name
    args = sys.argv[1:]
    home = _home()
    _log(home, name, args)
    if name == "grok":
        return _run_grok(home, args)
    if name == "opencode":
        return _run_opencode(home, args)
    print(f"unknown fake agent: {name}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
