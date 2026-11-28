#!/usr/bin/env python3
"""Test-only scripted headless turn, linked under each built-in CLI name."""

import json
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


def _record(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tests.harness import argv, faults, script, sessions, streams

    name = Path(sys.argv[0]).name
    arguments = sys.argv[1:]
    home = Path.home()
    if name == "codex" and arguments[:1] == ["queue"]:
        sessions.append(home / ".codex-wake.jsonl", argv.queue(arguments))
        return 0
    seat = os.environ.get("FOIL_SEAT_ID", "")
    master = os.environ.get("FOIL_MASTER") == "1"
    if bool(seat) == master:
        raise SystemExit("foil-fake: expected a seat or master identity")
    prompt, session, first = argv.parse(name, arguments)
    session = session or str(uuid.uuid4())
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        raise SystemExit("foil-fake: invalid session id")

    named = prompt if "/run/instructions/" in prompt else " ".join(arguments)
    root = Path(os.environ["FOIL_HOME"]) / "master" if master else script.fleet_root(named)
    actor = "master" if master else seat
    fake = root / "run" / "fake"
    directory = fake / f"{actor}.turns"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = fake / f"{actor}.log"
    state_path = fake / f"{actor}.turns.json"
    state = script.load(state_path)
    known = state.setdefault("sessions", {})
    if (first and session in known) or (not first and session != state.get("current")):
        script.log(log_path, f"unexpected session {session}")
        return 9
    if first:
        known[session] = {"day": datetime.now(UTC).strftime("%Y/%m/%d"), "count": 0}
        state["current"] = session
    turn = state.get("count", 0)
    details = known[session]
    number = details["count"]
    details["count"] += 1
    state["count"] = turn + 1
    _record(state_path, state)

    cwd = str(Path.cwd())
    record = {
        "argv": [name, *arguments], "prompt": prompt, "session": session, "first": first,
        "stdin": "tty" if sys.stdin.isatty() else sys.stdin.read(), "cwd": cwd,
        "home": str(home), "start": time.monotonic(),
    }
    record_path = directory / f"{turn:03d}.json"
    path = sessions.location(name, home, cwd, session, details["day"])
    sessions.write_turn(name, path, session, cwd, "user", prompt, first=first)
    streams.started(name, session, first)
    _record(record_path, record)
    script.log(log_path, f"turn {number} {'start' if first else 'resume'} session={session}")
    if name == "foil-fake":
        print(f"foil-fake {actor} turn {number}", flush=True)

    config = script.load(root / "run" / "fake.json" if master else fake / f"{seat}.json")
    fault_list = config.get("faults") or []
    fault = fault_list[turn] if turn < len(fault_list) else {}
    fault = fault if isinstance(fault, dict) else {}
    stopped = faults.interrupt(fault, root, fake, actor)
    tools = []
    if stopped is None:
        tools = script.run(root, actor, fake, config.get("steps") or [], prompt, fault,
                           master=master, diagnostic=name == "foil-fake")
    code, text = stopped if stopped is not None else (0, streams.ASSISTANT)
    if text is not None:
        sessions.write_turn(name, path, session, cwd, "assistant", text, tools)
        streams.completed(name, text)
    record["end"] = time.monotonic()
    _record(record_path, record)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
