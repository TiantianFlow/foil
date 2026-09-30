"""L1: command-surface standing check against requirements section 6.

Walks the live argparse tree from `foil.cli` and compares every command,
subcommand, and flag to `docs/requirements.md` section 6.1. Shelling out to
`--help` is not the oracle.

Allowed globally: `--help` (and argparse's `-h`) on every parser, and
`foil --version` on the root parser only. Any extra, missing, or renamed
command or flag fails. Positionals in the section 6 synopses are part of
the surface: extra, missing, or renamed ones fail too.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foil.cli import _build_parser

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "docs" / "requirements.md"

# Sentinel: do not assert a flag default unless the spec sets one.
_NO_DEFAULT = object()

HELP_OPTIONS = frozenset({"-h", "--help"})


@dataclass(frozen=True)
class FlagSpec:
    """One optional flag from section 6.1."""

    option: str
    metavar: str | None = None
    default: Any = _NO_DEFAULT


@dataclass(frozen=True)
class PositionalSpec:
    """One positional from a section 6.1 synopsis."""

    metavar: str
    nargs: str | None = None  # None = required single; "?" = optional


@dataclass(frozen=True)
class CommandSpec:
    flags: tuple[FlagSpec, ...] = ()
    positionals: tuple[PositionalSpec, ...] = ()
    commands: dict[str, CommandSpec] | None = None


# docs/requirements.md §6.1 — seven top-level commands, twenty actions.
SECTION_6 = CommandSpec(
    flags=(FlagSpec("--version"),),
    commands={
        "init": CommandSpec(positionals=(PositionalSpec("DIR", "?"),)),
        "seat": CommandSpec(
            commands={
                "spawn": CommandSpec(
                    flags=(
                        FlagSpec("--name", metavar="NAME"),
                        FlagSpec("--task", metavar="TEXT"),
                    ),
                    positionals=(PositionalSpec("TEMPLATE"),),
                ),
                "kill": CommandSpec(
                    flags=(FlagSpec("--all"),),
                    positionals=(PositionalSpec("NAME", "?"),),
                ),
                "resume": CommandSpec(positionals=(PositionalSpec("NAME", "?"),)),
                "list": CommandSpec(flags=(FlagSpec("--json"),)),
                "peek": CommandSpec(
                    flags=(FlagSpec("--lines", metavar="N", default=40),),
                    positionals=(PositionalSpec("NAME"),),
                ),
            }
        ),
        "send": CommandSpec(
            positionals=(
                PositionalSpec("TO"),
                PositionalSpec("TEXT"),
            )
        ),
        "mail": CommandSpec(
            commands={
                "read": CommandSpec(
                    flags=(FlagSpec("--json"),),
                    positionals=(PositionalSpec("PATH"),),
                ),
                "list": CommandSpec(flags=(FlagSpec("--json"),)),
            }
        ),
        "board": CommandSpec(
            commands={
                "read": CommandSpec(
                    flags=(FlagSpec("--json"),),
                    positionals=(PositionalSpec("PATH"),),
                ),
                "list": CommandSpec(
                    flags=(FlagSpec("--json"),),
                    positionals=(PositionalSpec("PATTERN"),),
                ),
            }
        ),
        "memory": CommandSpec(
            commands={
                "add": CommandSpec(
                    flags=(FlagSpec("--replaces", metavar="ID"),),
                    positionals=(PositionalSpec("TEXT"),),
                ),
                "accept": CommandSpec(positionals=(PositionalSpec("ID"),)),
                "reject": CommandSpec(positionals=(PositionalSpec("ID"),)),
                "list": CommandSpec(
                    flags=(
                        FlagSpec("--all"),
                        FlagSpec("--json"),
                    )
                ),
            }
        ),
        "roster": CommandSpec(
            commands={
                "list": CommandSpec(flags=(FlagSpec("--json"),)),
                "show": CommandSpec(
                    flags=(FlagSpec("--json"),),
                    positionals=(PositionalSpec("ROLE"),),
                ),
                "add": CommandSpec(
                    flags=(FlagSpec("--from", metavar="FILE"),),
                    positionals=(PositionalSpec("ROLE"),),
                ),
                "update": CommandSpec(
                    positionals=(PositionalSpec("ROLE"), PositionalSpec("FIELD=VALUE"))
                ),
                "remove": CommandSpec(positionals=(PositionalSpec("ROLE"),)),
            }
        ),
    },
)

SECTION_6_SYNOPSES = (
    "foil init [DIR]",
    "foil seat spawn TEMPLATE",
    "foil seat kill NAME",
    "foil seat resume [NAME]",
    "foil seat list",
    "foil seat peek NAME",
    "foil send TO TEXT",
    "foil mail read PATH",
    "foil mail list",
    "foil board read PATH",
    "foil board list PATTERN",
    "foil memory add TEXT",
    "foil memory accept ID",
    "foil memory reject ID",
    "foil memory list",
    "foil roster list",
    "foil roster show ROLE",
    "foil roster add ROLE",
    "foil roster update ROLE FIELD=VALUE",
    "foil roster remove ROLE",
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _display_metavar(action: argparse.Action) -> str | None:
    if action.metavar is not None:
        if isinstance(action.metavar, tuple):
            return " ".join(str(part) for part in action.metavar)
        return str(action.metavar)
    if action.dest is argparse.SUPPRESS or action.dest is None:
        return None
    return str(action.dest).upper()


def _normalize_nargs(nargs: Any) -> str | None:
    if nargs is None:
        return None
    if nargs is argparse.OPTIONAL:
        return "?"
    if nargs is argparse.ZERO_OR_MORE:
        return "*"
    if nargs is argparse.ONE_OR_MORE:
        return "+"
    if nargs is argparse.REMAINDER:
        return "..."
    return str(nargs)


def _flag_index(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    index: dict[str, argparse.Action] = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        for option in action.option_strings:
            index[option] = action
    return index


def _positionals(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        action
        for action in parser._actions
        if not action.option_strings and not isinstance(action, argparse._SubParsersAction)
    ]


def _diff_parser(spec: CommandSpec, parser: argparse.ArgumentParser, path: str) -> list[str]:
    problems: list[str] = []
    flags = _flag_index(parser)
    actual_options = set(flags)
    help_present = bool(HELP_OPTIONS & actual_options)
    if not help_present:
        problems.append(f"{path}: missing flag --help")

    comparable = actual_options - HELP_OPTIONS
    expected_options = {flag.option for flag in spec.flags}
    for option in sorted(comparable - expected_options):
        problems.append(f"{path}: extra flag {option}")
    for option in sorted(expected_options - comparable):
        problems.append(f"{path}: missing flag {option}")

    for flag in spec.flags:
        action = flags.get(flag.option)
        if action is None:
            continue
        if flag.metavar is not None:
            actual_metavar = _display_metavar(action)
            if actual_metavar != flag.metavar:
                problems.append(
                    f"{path}: flag {flag.option} metavar {actual_metavar!r} != {flag.metavar!r}"
                )
        if flag.default is not _NO_DEFAULT and action.default != flag.default:
            problems.append(
                f"{path}: flag {flag.option} default {action.default!r} != {flag.default!r}"
            )

    expected_positionals = spec.positionals
    actual_positionals = _positionals(parser)
    if len(actual_positionals) != len(expected_positionals):
        actual_names = [_display_metavar(action) or action.dest for action in actual_positionals]
        expected_names = [item.metavar for item in expected_positionals]
        problems.append(
            f"{path}: positionals {actual_names} != {expected_names}"
        )
    else:
        for expected, action in zip(expected_positionals, actual_positionals, strict=True):
            actual_metavar = _display_metavar(action) or action.dest
            if actual_metavar != expected.metavar:
                problems.append(
                    f"{path}: positional {actual_metavar!r} != {expected.metavar!r}"
                )
            actual_nargs = _normalize_nargs(action.nargs)
            if actual_nargs != expected.nargs:
                problems.append(
                    f"{path}: positional {expected.metavar} nargs {actual_nargs!r} "
                    f"!= {expected.nargs!r}"
                )

    expected_commands = spec.commands or {}
    subparsers = _subparsers(parser)
    actual_commands = dict(subparsers.choices) if subparsers is not None else {}
    if expected_commands and subparsers is None:
        problems.append(f"{path}: missing subcommands {sorted(expected_commands)}")
    elif not expected_commands and actual_commands:
        for name in sorted(actual_commands):
            problems.append(f"{path}: extra command {name}")
    else:
        for name in sorted(set(actual_commands) - set(expected_commands)):
            problems.append(f"{path}: extra command {name}")
        for name in sorted(set(expected_commands) - set(actual_commands)):
            problems.append(f"{path}: missing command {name}")
        for name in sorted(set(expected_commands) & set(actual_commands)):
            child_path = name if path == "foil" else f"{path} {name}"
            problems.extend(
                _diff_parser(expected_commands[name], actual_commands[name], child_path)
            )
    return problems


def test_section_6_synopses_are_the_requirements_oracle() -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    missing = [synopsis for synopsis in SECTION_6_SYNOPSES if f"`{synopsis}`" not in text]
    assert missing == [], "section 6.1 is missing synopses:\n" + "\n".join(missing)


def test_command_surface_matches_requirements_section_6() -> None:
    parser = _build_parser()
    assert isinstance(parser, argparse.ArgumentParser)
    problems = _diff_parser(SECTION_6, parser, "foil")
    assert problems == [], "CLI surface != requirements section 6.1:\n" + "\n".join(problems)
