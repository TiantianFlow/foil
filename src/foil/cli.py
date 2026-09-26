"""Argparse surface matching requirements section 6, plus N8 handling."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from foil import __version__
from foil.board import send_mail
from foil.errors import FoilError
from foil.lifecycle import (
    init_project,
    kill_seats,
    list_seats,
    peek_seat,
    resume_seats,
    spawn_seat,
)
from foil.memory import accept_lesson, add_lesson, list_lessons, reject_lesson
from foil.project import discover_project, is_initialized

OUTSIDE = "outside"
LEAD = "lead"
WORKER = "worker"

_ALLOWED = {
    "init": {OUTSIDE},
    "spawn": {OUTSIDE, LEAD},
    "kill": {OUTSIDE, LEAD},
    "kill-all": {OUTSIDE},
    "resume": {OUTSIDE, LEAD},
    "list": {OUTSIDE, LEAD, WORKER},
    "peek": {OUTSIDE, LEAD, WORKER},
    "send": {OUTSIDE, LEAD, WORKER},
    "memory-add": {OUTSIDE, LEAD, WORKER},
    "memory-list": {OUTSIDE, LEAD, WORKER},
    "memory-accept": {OUTSIDE, LEAD},
    "memory-reject": {OUTSIDE, LEAD},
}


def _caller() -> str:
    seat = os.environ.get("FOIL_SEAT_ID")
    if not seat:
        return OUTSIDE
    if seat == "lead":
        return LEAD
    return WORKER


def authorize(action: str, *, name: str | None = None) -> None:
    kind = _caller()
    if kind not in _ALLOWED[action]:
        raise FoilError("foil: not allowed")
    if action == "kill" and kind == LEAD and name == "lead":
        raise FoilError("foil: cannot kill the lead")


class FoilParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, f"foil: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = FoilParser(prog="foil")
    parser.add_argument("--version", action="version", version=f"foil {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Create the Foil folder")
    init_p.add_argument("DIR", nargs="?", metavar="DIR")

    seat = sub.add_parser("seat", help="Manage seats")
    seat_sub = seat.add_subparsers(dest="seat_command", required=True)

    spawn = seat_sub.add_parser("spawn", help="Create a seat from a template")
    spawn.add_argument("TEMPLATE", metavar="TEMPLATE")
    spawn.add_argument("--name", metavar="NAME")
    spawn.add_argument("--task", metavar="TEXT")

    kill = seat_sub.add_parser("kill", help="Stop a seat")
    kill.add_argument("NAME", nargs="?", metavar="NAME")
    kill.add_argument("--all", action="store_true")

    resume = seat_sub.add_parser("resume", help="Restart dead seats")
    resume.add_argument("NAME", nargs="?", metavar="NAME")

    listed = seat_sub.add_parser("list", help="List seats")
    listed.add_argument("--json", action="store_true")

    peek = seat_sub.add_parser("peek", help="Print the pane tail")
    peek.add_argument("NAME", metavar="NAME")
    peek.add_argument("--lines", metavar="N", type=int, default=40)

    send = sub.add_parser("send", help="Write mail and nudge the recipient")
    send.add_argument("TO", metavar="TO")
    send.add_argument("TEXT", metavar="TEXT")

    memory = sub.add_parser("memory", help="Project lessons")
    mem_sub = memory.add_subparsers(dest="memory_command", required=True)

    add = mem_sub.add_parser("add", help="Propose a lesson")
    add.add_argument("TEXT", metavar="TEXT")
    add.add_argument("--replaces", metavar="ID")

    accept = mem_sub.add_parser("accept", help="Accept a proposal")
    accept.add_argument("ID", metavar="ID")

    reject = mem_sub.add_parser("reject", help="Reject a proposal")
    reject.add_argument("ID", metavar="ID")

    mem_list = mem_sub.add_parser("list", help="List lessons")
    mem_list.add_argument("--all", action="store_true")
    mem_list.add_argument("--json", action="store_true")
    return parser


def _text_arg(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return value


def _require_project() -> Path:
    root = discover_project()
    if not is_initialized(root):
        raise FoilError("foil: not initialized")
    return root


def _run(args: argparse.Namespace) -> int:
    if args.command == "init":
        authorize("init")
        init_project(args.DIR)
        return 0
    root = _require_project()
    if args.command == "seat":
        action = args.seat_command
        if action == "spawn":
            authorize("spawn")
            spawn_seat(root, args.TEMPLATE, name=args.name, task=args.task)
        elif action == "kill":
            if args.all:
                authorize("kill-all")
                if args.NAME:
                    raise FoilError("foil: NAME and --all cannot be combined")
                kill_seats(root, all_seats=True)
            else:
                if not args.NAME:
                    raise FoilError("foil: NAME is required")
                authorize("kill", name=args.NAME)
                kill_seats(root, name=args.NAME)
        elif action == "resume":
            authorize("resume")
            resume_seats(root, args.NAME)
        elif action == "list":
            authorize("list")
            list_seats(root, as_json=args.json)
        else:
            authorize("peek")
            peek_seat(root, args.NAME, lines=args.lines)
        return 0
    if args.command == "send":
        authorize("send")
        send_mail(root, args.TO, _text_arg(args.TEXT))
        return 0
    action = args.memory_command
    if action == "add":
        authorize("memory-add")
        add_lesson(root, _text_arg(args.TEXT), replaces=args.replaces)
    elif action == "accept":
        authorize("memory-accept")
        accept_lesson(root, args.ID)
    elif action == "reject":
        authorize("memory-reject")
        reject_lesson(root, args.ID)
    else:
        authorize("memory-list")
        list_lessons(root, all_lessons=args.all, as_json=args.json)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args(None if argv is None else list(argv))
        return _run(args)
    except FoilError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("foil: unexpected error", file=sys.stderr)
        return 1
