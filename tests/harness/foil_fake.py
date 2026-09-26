#!/usr/bin/env python3
"""Test double for a seat CLI. Not part of the foil package."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _foil_root(prompt: str) -> Path:
    marker = ".foil/run/instructions/"
    if marker in prompt:
        raw = prompt.split("Read ", 1)[-1].split(" first.", 1)[0].strip()
        return Path(raw).parents[2]
    for parent in (Path.cwd(), *Path.cwd().parents):
        candidate = parent / ".foil"
        if candidate.is_dir():
            return candidate
    raise SystemExit("foil-fake: cannot find .foil")


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.rstrip() + "\n")


def _steps(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps = payload.get("steps", [])
    return steps if isinstance(steps, list) else []


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
            action["sh"],
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
        _log(
            log_path,
            f"sh {result.returncode} {action['sh']}\n{result.stdout}{result.stderr}",
        )
        return result.returncode == 0
    if "write" in action:
        target = _write_target(foil, str(action["write"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(action.get("text", "")), encoding="utf-8")
        _log(log_path, f"write {target}")
        return True
    if "send" in action:
        result = subprocess.run(
            ["foil", "send", str(action["send"]), str(action.get("text", ""))],
            check=False,
        )
        _log(log_path, f"send {result.returncode} {action['send']}")
        return result.returncode == 0
    if "wait" in action:
        time.sleep(float(action["wait"]))
        _log(log_path, f"wait {action['wait']}")
        return True
    _log(log_path, f"unknown action {action}")
    return False


def _handle(
    mail: Path,
    *,
    foil: Path,
    steps: list[dict],
    handled: set[str],
    handled_path: Path,
    log_path: Path,
) -> None:
    key = str(mail.resolve())
    if key in handled or not mail.is_file():
        return
    text = mail.read_text(encoding="utf-8")
    _log(log_path, f"mail {key}")
    for step in steps:
        when = step.get("when") or {}
        needle = str(when.get("mail_contains", ""))
        if not needle or needle not in text:
            continue
        for action in step.get("do") or []:
            if not isinstance(action, dict) or not _run_action(foil, action, log_path):
                _log(log_path, f"stopped {key}")
                return
    with handled_path.open("a", encoding="utf-8") as handle:
        handle.write(key + "\n")
    handled.add(key)


def main() -> int:
    parser = argparse.ArgumentParser(prog="foil-fake")
    parser.add_argument("--session", default="")
    parser.add_argument("--prompt", default="")
    args = parser.parse_args()
    seat = os.environ.get("FOIL_SEAT_ID", "")
    if not seat:
        print("foil-fake: FOIL_SEAT_ID is unset", file=sys.stderr)
        return 1
    foil = _foil_root(args.prompt)
    fake = foil / "run" / "fake"
    log_path = fake / f"{seat}.log"
    handled_path = fake / f"{seat}.handled"
    steps = _steps(fake / f"{seat}.json")
    handled = set()
    if handled_path.is_file():
        for line in handled_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                handled.add(line.strip())
    print(f"foil-fake {seat} ready", flush=True)
    _log(log_path, f"ready session={args.session}")
    mailbox = foil / "board" / "mail" / seat
    if mailbox.is_dir():
        for mail in sorted(mailbox.glob("*.md")):
            _handle(
                mail,
                foil=foil,
                steps=steps,
                handled=handled,
                handled_path=handled_path,
                log_path=log_path,
            )
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        print(line, flush=True)
        _path = line.split(" ", 1)[1] if " " in line else ""
        if not _path:
            continue
        _handle(
            Path(_path),
            foil=foil,
            steps=steps,
            handled=handled,
            handled_path=handled_path,
            log_path=log_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
