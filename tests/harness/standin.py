#!/usr/bin/env python3
"""Stand-ins for the agent CLIs Foil ships presets for. Not part of the foil package.

Installed under the names `claude`, `codex`, `gemini`, `opencode` and
`grok`, one file plays whichever name it was started as. It reads the
command line the way that CLI does, ends with an error where that CLI
would, and then behaves as the fake agent, with the same scripts.

What each grammar holds is the flags Foil's packaged preset uses and
their neighbours, with the number of values that CLI's help gives each
one. So a stand-in catches a mistake in the shape of a launch: a flag
the CLI does not have, a flag that takes the prompt as its value, a
value missing or not among the choices, no prompt, a session the CLI
was never given, or a setting that did not arrive in the environment.
It needs no login. It does not know what a vendor changed last week:
the live tier runs the real CLIs.

Two rules are stricter than the CLIs, to make a wrong launch visible: a
resume with nothing to resume is an error for every stand-in, and
`--resume` with no id is one for `claude`, which would open a picker.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foil_fake  # noqa: E402

NAMES = ("claude", "codex", "gemini", "opencode", "grok")


class Refused(Exception):
    """The CLI would not start with this command line."""


@dataclass(frozen=True)
class Flag:
    """`takes` is "none", "one", "optional" or "many" values."""

    takes: str = "none"
    choices: tuple[str, ...] = ()


@dataclass
class Launch:
    """What a command line asks for, before the seat's history is known."""

    prompt: str = ""
    session: str = ""  # an id the caller chose
    resume: str = ""  # "" for a new session, "id" for `session`, "last" for this directory
    facts: dict[str, str] = field(default_factory=dict)


def options(
    argv: list[str], flags: dict[str, Flag], aliases: dict[str, str] | None = None
) -> tuple[dict[str, list[str]], list[str]]:
    """Split a command line into each flag's values and the positional words."""

    found: dict[str, list[str]] = {}
    words: list[str] = []
    rest = list(argv)
    while rest:
        token = rest.pop(0)
        if len(token) < 2 or not token.startswith("-"):
            words.append(token)
            continue
        name, equals, attached = token.partition("=")
        name = (aliases or {}).get(name, name)
        flag = flags.get(name)
        if flag is None:
            raise Refused(f"unknown option '{token.partition('=')[0]}'")
        values = [attached] if equals else []
        if equals and flag.takes == "none":
            raise Refused(f"option '{name}' takes no value")
        if not equals and flag.takes == "one":
            if not rest or rest[0] in flags or rest[0] in (aliases or {}):
                raise Refused(f"option '{name}' needs a value")
            values.append(rest.pop(0))
        elif not equals and flag.takes == "optional":
            if rest and not rest[0].startswith("-"):
                values.append(rest.pop(0))
        elif not equals and flag.takes == "many":
            # Every word up to the next flag is a value. A prompt that follows is one too.
            while rest and not rest[0].startswith("-"):
                values.append(rest.pop(0))
            if not values:
                raise Refused(f"option '{name}' needs a value")
        for value in values:
            if flag.choices and value not in flag.choices:
                raise Refused(f"option '{name}' does not take '{value}'")
        found.setdefault(name, []).extend(values)
    return found, words


def _one(found: dict[str, list[str]], name: str, default: str = "") -> str:
    values = found.get(name)
    return values[-1] if values else default


def _prompt(words: list[str], *, needed: bool) -> str:
    if len(words) > 1:
        raise Refused(f"unexpected argument '{words[1][:40]}'")
    if needed and (not words or not words[0].strip()):
        raise Refused("no prompt")
    return words[0] if words else ""


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise Refused(f"'{value}' is not a session id") from None


def claude(argv: list[str], environment: dict[str, str]) -> Launch:
    modes = ("acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan")
    found, words = options(
        argv,
        {
            "--model": Flag("one"),
            "--session-id": Flag("one"),
            "--resume": Flag("optional"),
            "--continue": Flag(),
            "--permission-mode": Flag("one", modes),
            "--disallowedTools": Flag("many"),
            "--allowedTools": Flag("many"),
            "--add-dir": Flag("many"),
            "--dangerously-skip-permissions": Flag(),
        },
        {"-r": "--resume", "-c": "--continue", "--disallowed-tools": "--disallowedTools"},
    )
    facts = {
        "model": _one(found, "--model"),
        "permission": _one(found, "--permission-mode", "default"),
        "disallowed": ",".join(found.get("--disallowedTools", [])),
    }
    if "--resume" in found:
        if "--session-id" in found:
            raise Refused("--session-id cannot be used with --resume")
        if not found["--resume"]:
            raise Refused("--resume needs a session id: nobody is here to pick one")
        return Launch(_prompt(words, needed=False), _uuid(found["--resume"][-1]), "id", facts)
    if "--continue" in found:
        return Launch(_prompt(words, needed=False), "", "last", facts)
    session = _uuid(found["--session-id"][-1]) if "--session-id" in found else ""
    return Launch(_prompt(words, needed=True), session, "", facts)


def codex(argv: list[str], environment: dict[str, str]) -> Launch:
    found, words = options(
        argv,
        {
            "--model": Flag("one"),
            "--ask-for-approval": Flag("one", ("untrusted", "on-failure", "on-request", "never")),
            "--sandbox": Flag("one", ("read-only", "workspace-write", "danger-full-access")),
            "--full-auto": Flag(),
            "--cd": Flag("one"),
            "--last": Flag(),
        },
        {"-m": "--model", "-a": "--ask-for-approval", "-s": "--sandbox", "-C": "--cd"},
    )
    facts = {"model": _one(found, "--model"), "permission": _one(found, "--ask-for-approval")}
    if words[:1] != ["resume"]:
        if "--last" in found:
            raise Refused("unknown option '--last'")
        return Launch(_prompt(words, needed=True), "", "", facts)
    words = words[1:]
    if "--last" in found:
        return Launch(_prompt(words, needed=False), "", "last", facts)
    if not words:
        raise Refused("resume needs --last or a session id: nobody is here to pick one")
    return Launch(_prompt(words[1:], needed=False), _uuid(words[0]), "id", facts)


def gemini(argv: list[str], environment: dict[str, str]) -> Launch:
    found, words = options(
        argv,
        {
            "--model": Flag("one"),
            "--approval-mode": Flag("one", ("default", "auto_edit", "yolo", "plan")),
            "--yolo": Flag(),
            "--resume": Flag("optional"),
            "--prompt-interactive": Flag("one"),
            "--include-directories": Flag("many"),
            "--sandbox": Flag(),
        },
        {"-m": "--model", "-y": "--yolo", "-r": "--resume", "-i": "--prompt-interactive"},
    )
    if "--yolo" in found and "--approval-mode" in found:
        raise Refused("--yolo cannot be used with --approval-mode")
    mode = "yolo" if "--yolo" in found else _one(found, "--approval-mode", "default")
    facts = {"model": _one(found, "--model"), "permission": mode}
    typed = found.get("--prompt-interactive", [])
    if typed and words:
        raise Refused("a prompt cannot be given twice")
    resume = "last" if "--resume" in found else ""
    return Launch(_prompt(typed or words, needed=not resume), "", resume, facts)


def opencode(argv: list[str], environment: dict[str, str]) -> Launch:
    found, words = options(
        argv,
        {
            "--model": Flag("one"),
            "--prompt": Flag("one"),
            "--continue": Flag(),
            "--session": Flag("one"),
            "--agent": Flag("one"),
            "--auto": Flag(),
        },
        {"-m": "--model", "-c": "--continue", "-s": "--session"},
    )
    if len(words) > 1:
        raise Refused(f"unexpected argument '{words[1][:40]}'")
    if words and not Path(words[0]).is_dir():
        raise Refused(f"'{words[0][:40]}' is not a project directory")
    model = _one(found, "--model")
    if model and "/" not in model.strip("/"):
        raise Refused(f"model '{model}' is not provider/model")
    question = ""
    raw = environment.get("OPENCODE_CONFIG_CONTENT")
    if raw is not None:
        try:
            config = json.loads(raw)
        except json.JSONDecodeError:
            raise Refused("OPENCODE_CONFIG_CONTENT is not JSON") from None
        if not isinstance(config, dict):
            raise Refused("OPENCODE_CONFIG_CONTENT is not a table")
        permission = config.get("permission")
        question = str(permission.get("question", "")) if isinstance(permission, dict) else ""
    facts = {
        "model": model,
        "permission": "auto" if "--auto" in found else "",
        "question": question,
    }
    prompt = _one(found, "--prompt")
    if "--session" in found:
        return Launch(prompt, found["--session"][-1], "id", facts)
    if "--continue" in found:
        return Launch(prompt, "", "last", facts)
    if not prompt.strip():
        raise Refused("no prompt")
    return Launch(prompt, "", "", facts)


def grok(argv: list[str], environment: dict[str, str]) -> Launch:
    found, words = options(
        argv,
        {
            "--model": Flag("one"),
            "--session-id": Flag("one"),
            "--resume": Flag("one"),
            "--always-approve": Flag(),
        },
        {"-m": "--model", "-r": "--resume"},
    )
    facts = {
        "model": _one(found, "--model"),
        "permission": "always-approve" if "--always-approve" in found else "",
    }
    if "--resume" in found:
        if "--session-id" in found:
            raise Refused("--session-id cannot be used with --resume")
        return Launch(_prompt(words, needed=False), found["--resume"][-1], "id", facts)
    return Launch(_prompt(words, needed=True), _one(found, "--session-id"), "", facts)


GRAMMARS = {"claude": claude, "codex": codex, "gemini": gemini, "opencode": opencode, "grok": grok}


def parse(name: str, argv: list[str], environment: dict[str, str] | None = None) -> Launch:
    """Read one CLI's arguments, without its own name. Raises Refused."""

    return GRAMMARS[name](list(argv), dict(os.environ if environment is None else environment))


def install(bindir: Path) -> None:
    """Put the five stand-ins in bindir."""

    bindir.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        (bindir / name).symlink_to(Path(__file__).resolve())


def main() -> int:
    name = Path(sys.argv[0]).name
    seat = foil_fake.seat_id(name)
    try:
        launch = parse(name, sys.argv[1:])
    except Refused as exc:
        foil_fake.refuse(name, seat, foil_fake.fleet_root(""), str(exc))
    fleet = foil_fake.fleet_root(launch.prompt)
    sessions = foil_fake.Sessions(fleet, seat)
    session = launch.session
    if launch.resume == "last":
        session = sessions.last_in(str(Path.cwd()))
        if not session:
            foil_fake.refuse(name, seat, fleet, "no session in this directory to resume")
    elif launch.resume and not sessions.known(session):
        foil_fake.refuse(name, seat, fleet, f"no session '{session}' to resume")
    elif not launch.resume and session and sessions.known(session):
        foil_fake.refuse(name, seat, fleet, f"session '{session}' is already in use")
    if launch.resume:
        kind = "resume"
    else:
        session = session or str(uuid.uuid4())
        kind = "fresh" if sessions.all() else "first"
    facts = {"cli": name, **launch.facts}
    sessions.record(session, kind, facts)
    return foil_fake.Fake(seat, fleet, session=session, launch=kind, name=name, facts=facts).run()


if __name__ == "__main__":
    raise SystemExit(main())
