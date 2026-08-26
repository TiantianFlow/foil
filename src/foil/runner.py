"""Fixed tmux entry point that execs a validated argv plan without a shell."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

MAX_PLAN_BYTES = 128 * 1024
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


class RunnerError(ValueError):
    """A runner plan is malformed or unsafe."""


def _load_plan(path: Path) -> dict[str, Any]:
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise RunnerError("runner plan must be a regular non-symlink file")
    if file_stat.st_size > MAX_PLAN_BYTES:
        raise RunnerError("runner plan exceeds size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {"argv", "cwd", "env"}
    optional = {"env_forward"}
    if not isinstance(payload, dict) or not required <= set(payload):
        raise RunnerError("runner plan has invalid fields")
    if set(payload) - required - optional:
        raise RunnerError("runner plan has invalid fields")
    forward = payload.get("env_forward", [])
    if not isinstance(forward, list) or not all(
        isinstance(name, str) and _ENVIRONMENT_NAME.fullmatch(name) for name in forward
    ):
        raise RunnerError("runner env_forward is invalid")
    return payload


def run(path: Path) -> None:
    payload = _load_plan(path)
    argv = payload["argv"]
    cwd = payload["cwd"]
    environment = payload["env"]
    forward = payload.get("env_forward", [])
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) and item and "\x00" not in item for item in argv)
    ):
        raise RunnerError("runner argv is invalid")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise RunnerError("runner cwd is invalid")
    if not isinstance(environment, dict) or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and "\x00" not in key
        and "\x00" not in value
        for key, value in environment.items()
    ):
        raise RunnerError("runner environment is invalid")
    # Forwarded variables are declared by name in the plan; values are
    # resolved here, at exec time, from the launch environment so they are
    # never persisted in the plan itself.
    child_environment = dict(environment)
    for name in forward:
        value = os.environ.get(name)
        if value is not None and "\x00" not in value:
            child_environment[name] = value
    os.chdir(cwd)
    os.execvpe(argv[0], argv, child_environment)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m foil.runner PLAN.json", file=sys.stderr)
        return 2
    try:
        run(Path(arguments[0]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"foil runner failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
