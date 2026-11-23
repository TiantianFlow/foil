"""Argparse surface matching requirements section 6, plus N8 handling.

Parsing and the call's identity live here. The operation is ``foil.api``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from foil import __version__, api
from foil.errors import FoilError
from foil.joined import notice
from foil.project import checkout_for_key, discover_project, is_initialized, refuse_legacy_layout
from foil.scope import bound
from foil.store import in_seat, load_registry

_USER = "user"
_LEAD = "lead"


class _Call:
    """The project, fleet, and caller one command resolved to."""

    def __init__(
        self, project: Path | None, fleet: str, caller: str, *, inside: bool, pinned: bool
    ) -> None:
        self.project = project
        self.fleet = fleet
        self.caller = caller
        self.inside = inside
        # False is the outside default, which is lead or user per fleet.
        self.pinned = pinned


class FoilParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, f"foil: {message}\n")


def _shared(parser: argparse.ArgumentParser, *, key: bool = True) -> None:
    """``--as`` and ``--project KEY`` on the root and on each leaf.

    ``default=SUPPRESS`` lets a flag on the root survive a leaf that omits it.
    Roster add, update, and remove keep the valueless ``--project`` switch, so
    they do not take ``KEY`` on the leaf. The key goes before the subcommand.
    """

    parser.add_argument("--as", dest="as_caller", metavar="CALLER", default=argparse.SUPPRESS)
    if key:
        parser.add_argument(
            "--project", dest="project_key", metavar="KEY", default=argparse.SUPPRESS
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = FoilParser(prog="foil")
    parser.add_argument("--version", action="version", version=f"foil {__version__}")
    _shared(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Create the Foil folder")
    init_p.add_argument("DIR", nargs="?", metavar="DIR")
    _shared(init_p)

    seat = sub.add_parser("seat", help="Manage seats")
    seat_sub = seat.add_subparsers(dest="seat_command", required=True)

    spawn = seat_sub.add_parser("spawn", help="Create a seat from a template")
    spawn.add_argument("TEMPLATE", metavar="TEMPLATE")
    spawn.add_argument("--name", metavar="NAME")
    spawn.add_argument("--task", metavar="TEXT")
    _shared(spawn)

    kill = seat_sub.add_parser("kill", help="Stop a seat")
    kill.add_argument("NAME", nargs="?", metavar="NAME")
    kill.add_argument("--all", action="store_true")
    _shared(kill)

    archive = seat_sub.add_parser("archive", help="Free a killed seat's name")
    archive.add_argument("NAME", metavar="NAME")
    _shared(archive)

    resume = seat_sub.add_parser("resume", help="Restart dead seats and retry paused ones")
    resume.add_argument("NAME", nargs="?", metavar="NAME")
    _shared(resume)

    renew = seat_sub.add_parser("renew", help="Give a seat a fresh session after its handoff")
    renew.add_argument("NAME", metavar="NAME")
    _shared(renew)

    listed = seat_sub.add_parser("list", help="List seats")
    listed.add_argument("--json", action="store_true")
    listed.add_argument("--archived", action="store_true")
    listed.add_argument("--all-fleets", action="store_true")
    _shared(listed)

    peek = seat_sub.add_parser("peek", help="Print the pane tail")
    peek.add_argument("NAME", metavar="NAME")
    peek.add_argument("--lines", metavar="N", type=int, default=40)
    peek.add_argument("--json", action="store_true")
    _shared(peek)

    join = seat_sub.add_parser("join", help="Take the lead seat for this session")
    join.add_argument("SEAT", metavar="lead", choices=["lead"])
    join.add_argument("--harness", metavar="ID")
    join.add_argument("--session", metavar="ID")
    join.add_argument("--take", action="store_true")
    _shared(join)

    leave = seat_sub.add_parser("leave", help="End the joined lead")
    _shared(leave)

    send = sub.add_parser("send", help="Write mail; it wakes the recipient")
    send.add_argument("TO", metavar="TO")
    send.add_argument("TEXT", metavar="TEXT")
    _shared(send)

    answer = sub.add_parser("answer", help="Answer one closed question")
    answer.add_argument("ID", metavar="ID")
    answer.add_argument("TEXT", nargs="?", metavar="TEXT")
    _shared(answer)

    post = sub.add_parser("post", help="Write a room file; it wakes its members")
    post.add_argument("--re", metavar="PATH")
    post.add_argument("--decline", action="store_true")
    post.add_argument("--members", metavar="NAMES")
    post.add_argument("--close", action="store_true")
    post.add_argument("TEXT", nargs="?", metavar="TEXT")
    _shared(post)

    mail = sub.add_parser("mail", help="Read mail files")
    mail_sub = mail.add_subparsers(dest="mail_command", required=True)

    mail_read_p = mail_sub.add_parser("read", help="Read a mail file")
    mail_read_p.add_argument("PATH", metavar="PATH")
    mail_read_p.add_argument("--json", action="store_true")
    _shared(mail_read_p)

    mail_list_p = mail_sub.add_parser("list", help="List mail for current seat")
    mail_list_p.add_argument("--json", action="store_true")
    mail_list_p.add_argument("--sent", action="store_true")
    _shared(mail_list_p)

    mail_wait_p = mail_sub.add_parser("wait", help="Wait for new mail")
    mail_wait_p.add_argument("--timeout", metavar="SECONDS", type=int, default=1800)
    mail_wait_p.add_argument("--wake", action="store_true")
    mail_wait_p.add_argument("--all-fleets", action="store_true")
    mail_wait_p.add_argument("--json", action="store_true")
    _shared(mail_wait_p)

    board = sub.add_parser("board", help="Read board files")
    board_sub = board.add_subparsers(dest="board_command", required=True)

    board_read_p = board_sub.add_parser("read", help="Read a board file")
    board_read_p.add_argument("PATH", metavar="PATH")
    board_read_p.add_argument("--json", action="store_true")
    _shared(board_read_p)

    board_list_p = board_sub.add_parser("list", help="List board files")
    board_list_p.add_argument("PATTERN", metavar="PATTERN")
    board_list_p.add_argument("--json", action="store_true")
    _shared(board_list_p)

    memory = sub.add_parser("memory", help="Project lessons")
    mem_sub = memory.add_subparsers(dest="memory_command", required=True)

    add = mem_sub.add_parser("add", help="Propose a lesson")
    add.add_argument("TEXT", metavar="TEXT")
    add.add_argument("--replaces", metavar="ID")
    _shared(add)

    accept = mem_sub.add_parser("accept", help="Accept a proposal")
    accept.add_argument("ID", metavar="ID")
    _shared(accept)

    reject = mem_sub.add_parser("reject", help="Reject a proposal")
    reject.add_argument("ID", metavar="ID")
    _shared(reject)

    mem_list = mem_sub.add_parser("list", help="List lessons")
    mem_list.add_argument("--all", action="store_true")
    mem_list.add_argument("--json", action="store_true")
    _shared(mem_list)

    roster = sub.add_parser("roster", help="Manage role templates")
    roster_sub = roster.add_subparsers(dest="roster_command", required=True)

    roster_list = roster_sub.add_parser("list", help="List templates and personas")
    roster_list.add_argument("--json", action="store_true")
    _shared(roster_list)

    roster_show = roster_sub.add_parser("show", help="Show one template")
    roster_show.add_argument("ROLE", metavar="ROLE")
    roster_show.add_argument("--json", action="store_true")
    _shared(roster_show)

    roster_add = roster_sub.add_parser("add", help="Add a new template")
    roster_add.add_argument("ROLE", metavar="ROLE")
    roster_add.add_argument("--from", dest="from_file", metavar="FILE")
    roster_add.add_argument("--project", action="store_true")
    roster_add.add_argument("--base", metavar="REV")
    _shared(roster_add, key=False)

    roster_update = roster_sub.add_parser("update", help="Update a template field")
    roster_update.add_argument("ROLE", metavar="ROLE")
    roster_update.add_argument("FIELD_VALUE", metavar="FIELD=VALUE")
    roster_update.add_argument("--project", action="store_true")
    roster_update.add_argument("--base", metavar="REV")
    _shared(roster_update, key=False)

    roster_remove = roster_sub.add_parser("remove", help="Remove a template")
    roster_remove.add_argument("ROLE", metavar="ROLE")
    roster_remove.add_argument("--project", action="store_true")
    roster_remove.add_argument("--base", metavar="REV")
    _shared(roster_remove, key=False)

    view = sub.add_parser("view", help="Serve a read-only page for every project on this host")
    view.add_argument("--port", metavar="N")
    _shared(view)

    journal = sub.add_parser("journal", help="List this fleet's journal")
    journal.add_argument("--follow", action="store_true")
    journal.add_argument("--since", metavar="T")
    journal.add_argument("--json", action="store_true")
    _shared(journal)

    decide = sub.add_parser("decide", help="Record one decision in the journal")
    decide.add_argument("TEXT", metavar="TEXT")
    decide.add_argument("--why", metavar="TEXT", required=True)
    _shared(decide)

    fleet = sub.add_parser("fleet", help="Pause, resume, or merge a fleet")
    fleet_sub = fleet.add_subparsers(dest="fleet_command", required=True)
    pause = fleet_sub.add_parser("pause", help="Start no new turn")
    _shared(pause)
    resume = fleet_sub.add_parser("resume", help="Take the queued items")
    _shared(resume)
    merge = fleet_sub.add_parser("merge", help="Merge the goal branch into this checkout")
    merge.add_argument("--into", metavar="BRANCH")
    _shared(merge)

    inbox = sub.add_parser("inbox", help="List what needs the human")
    inbox.add_argument("--all-fleets", action="store_true")
    inbox.add_argument("--all-projects", action="store_true")
    inbox.add_argument("--json", action="store_true")
    _shared(inbox)

    brief = sub.add_parser("brief", help="Print one fleet's status from its files")
    brief.add_argument("--all-projects", action="store_true")
    brief.add_argument("--json", action="store_true")
    _shared(brief)

    master = sub.add_parser("master", help="Run the master for this store")
    master_sub = master.add_subparsers(dest="master_command", required=True)
    master_start = master_sub.add_parser("start", help="Start the master")
    master_start.add_argument("--harness", metavar="ID/MODEL")
    _shared(master_start)
    master_stop = master_sub.add_parser("stop", help="Stop the master")
    _shared(master_stop)
    master_renew = master_sub.add_parser("renew", help="Replace the master's harness")
    master_renew.add_argument("--harness", metavar="ID/MODEL")
    _shared(master_renew)

    doctor = sub.add_parser("doctor", help="Check the Bridge")
    _shared(doctor)

    bridge = sub.add_parser("bridge", help="The local Bridge")
    _shared(bridge)
    bridge_sub = bridge.add_subparsers(dest="bridge_command", required=False)
    install = bridge_sub.add_parser("install", help="Write the boot unit")
    _shared(install)

    return parser


def _text_arg(value: str) -> str:
    if value == "-":
        return sys.stdin.read()
    return value


def _fleet_name() -> str:
    return os.environ.get("FOIL_FLEET", "").strip() or "main"


def _discover() -> Path:
    return discover_project(named=os.environ.get("FOIL_PROJECT", "").strip())


def _require() -> Path:
    root = _discover()
    refuse_legacy_layout(root)
    if not is_initialized(root):
        raise FoilError("foil: not initialized")
    return root


def _initialized(project: Path) -> bool:
    try:
        return is_initialized(project)
    except FoilError:
        return False


def _lead_joined(project: Path | None, fleet: str) -> bool:
    if project is None or not _initialized(project):
        return False
    try:
        record = load_registry(project, fleet)["seats"].get("lead")
    except (FoilError, OSError):
        return False
    return bool(record) and record.get("state") == "joined"


def _project_for(args: argparse.Namespace, key: str | None, *, inside: bool) -> Path | None:
    layer = args.command == "roster" and getattr(args, "project", False)
    if key:
        if args.command == "init" and args.DIR:
            raise FoilError("foil: init DIR and --project cannot be combined")
        found = checkout_for_key(key)
        if layer and not inside and not _initialized(found):
            raise FoilError("foil: --project needs a checkout")
        return found
    if args.command == "init":
        return None
    if args.command in {"view", "doctor", "bridge"}:
        try:
            return _discover()
        except FoilError:
            return None
    if args.command == "master":
        try:
            return _discover()
        except FoilError:
            return None
    if args.command == "send" and getattr(args, "TO", None) == "master" and not key:
        try:
            return _discover()
        except FoilError:
            return None
    if args.command in {"inbox", "brief"} and getattr(args, "all_projects", False) and not key:
        try:
            return _discover()
        except FoilError:
            return None
    if args.command == "roster" and not layer:
        try:
            return _require()
        except FoilError as exc:
            message = str(exc)
            if "not a git repository" not in message and "not initialized" not in message:
                raise
            return Path.cwd()
    if layer and not inside:
        try:
            _discover()
        except FoilError:
            raise FoilError("foil: --project needs a checkout") from None
        if not _initialized(_discover()):
            raise FoilError("foil: --project needs a checkout")
    return _require()


def _resolve(args: argparse.Namespace) -> _Call:
    """Read the process identity once and turn it into this call's arguments."""

    fleet = _fleet_name()
    seat = os.environ.get("FOIL_SEAT_ID", "").strip()
    inside = bool(seat)
    chosen = getattr(args, "as_caller", None)
    if chosen is not None:
        if inside:
            raise FoilError("foil: --as is outside the fleet only")
        if chosen not in {_USER, _LEAD}:
            raise FoilError("foil: --as is user or lead")
    project = _project_for(args, getattr(args, "project_key", None), inside=inside)
    if chosen == _LEAD:
        if not _lead_joined(project, fleet):
            raise FoilError("foil: --as lead needs a joined lead")
        caller = _LEAD
    elif chosen == _USER:
        caller = _USER
    elif inside:
        caller = seat
    elif _lead_joined(project, fleet):
        caller = _LEAD
    else:
        caller = _USER
    # No --as outside the fleet follows each fleet's joined lead. A seat and
    # an explicit --as stay the name resolved above, including across fleets.
    pinned = inside or chosen is not None
    return _Call(project, fleet, caller, inside=inside, pinned=pinned)


def _run(args: argparse.Namespace, call: _Call) -> int:
    project, fleet, caller = call.project, call.fleet, call.caller
    inside, pinned = call.inside, call.pinned
    if args.command == "init":
        directory = args.DIR
        if directory is None and project is not None:
            directory = str(project)
        api.init(project, fleet, caller, inside=inside, pinned=pinned, directory=directory)
        return 0
    if args.command == "view":
        return api.view(
            project, fleet, caller, inside=inside, pinned=pinned, port=args.port
        )["code"]
    if args.command == "seat":
        action = args.seat_command
        if action == "spawn":
            api.seat_spawn(
                project,
                fleet,
                caller,
                args.TEMPLATE,
                inside=inside,
                pinned=pinned,
                name=args.name,
                task=args.task,
            )
        elif action == "kill":
            api.seat_kill(
                project,
                fleet,
                caller,
                inside=inside,
                pinned=pinned,
                name=args.NAME,
                all_seats=args.all,
            )
        elif action == "archive":
            api.seat_archive(project, fleet, caller, args.NAME, inside=inside, pinned=pinned)
        elif action == "resume":
            resumed = api.seat_resume(
                project, fleet, caller, args.NAME, inside=inside, pinned=pinned
            )
            return resumed["code"]
        elif action == "renew":
            api.seat_renew(project, fleet, caller, args.NAME, inside=inside, pinned=pinned)
        elif action == "list":
            api.seat_list(
                project,
                fleet,
                caller,
                inside=inside, pinned=pinned,
                as_json=args.json,
                archived=args.archived,
                all_fleets=args.all_fleets,
            )
        elif action == "join":
            api.seat_join(
                project,
                fleet,
                caller,
                inside=inside, pinned=pinned,
                harness=args.harness,
                session=args.session,
                take=args.take,
            )
        elif action == "leave":
            api.seat_leave(project, fleet, caller, inside=inside, pinned=pinned)
        else:
            api.seat_peek(
                project,
                fleet,
                caller,
                args.NAME,
                inside=inside, pinned=pinned,
                lines=args.lines,
                as_json=args.json,
            )
        return 0
    if args.command == "answer":
        api.answer(
            project,
            fleet,
            caller,
            args.ID,
            "" if args.TEXT is None else args.TEXT,
            inside=inside,
            pinned=pinned,
            read_text=None if args.TEXT is None else _text_arg,
        )
        return 0
    if args.command == "send":
        api.send(
            project,
            fleet,
            caller,
            args.TO,
            args.TEXT,
            inside=inside,
            pinned=pinned,
            read_text=_text_arg,
        )
        return 0
    if args.command == "post":
        api.post(
            project,
            fleet,
            caller,
            args.TEXT,
            inside=inside, pinned=pinned,
            reply=args.re,
            decline=args.decline,
            members=args.members,
            close=args.close,
            read_text=_text_arg,
        )
        return 0
    if args.command == "mail":
        action = args.mail_command
        if action == "read":
            api.mail_read(
                project, fleet, caller, args.PATH, inside=inside, pinned=pinned, as_json=args.json
            )
        elif action == "wait":
            api.mail_wait_for(
                project,
                fleet,
                caller,
                inside=inside, pinned=pinned,
                timeout=args.timeout,
                wake=args.wake,
                all_fleets=args.all_fleets,
                as_json=args.json,
            )
        else:
            api.mail_list(
                project,
                fleet,
                caller,
                inside=inside,
                pinned=pinned,
                as_json=args.json,
                sent=args.sent,
            )
        return 0
    if args.command == "board":
        if args.board_command == "read":
            api.board_read(
                project, fleet, caller, args.PATH, inside=inside, pinned=pinned, as_json=args.json
            )
        else:
            api.board_list(
                project,
                fleet,
                caller,
                args.PATTERN,
                inside=inside,
                pinned=pinned,
                as_json=args.json,
            )
        return 0
    if args.command == "memory":
        action = args.memory_command
        if action == "add":
            api.memory_add(
                project,
                fleet,
                caller,
                args.TEXT,
                inside=inside, pinned=pinned,
                replaces=args.replaces,
                read_text=_text_arg,
            )
        elif action == "accept":
            api.memory_accept(project, fleet, caller, args.ID, inside=inside, pinned=pinned)
        elif action == "reject":
            api.memory_reject(project, fleet, caller, args.ID, inside=inside, pinned=pinned)
        else:
            api.memory_list(
                project,
                fleet,
                caller,
                inside=inside,
                pinned=pinned,
                all_lessons=args.all,
                as_json=args.json,
            )
        return 0
    if args.command == "journal":
        api.journal(
            project,
            fleet,
            caller,
            inside=inside,
            pinned=pinned,
            since=args.since,
            follow=args.follow,
            as_json=args.json,
        )
        return 0
    if args.command == "decide":
        api.decide(
            project,
            fleet,
            caller,
            args.TEXT,
            inside=inside,
            pinned=pinned,
            why=args.why,
        )
        return 0
    if args.command == "fleet":
        if args.fleet_command == "pause":
            api.fleet_pause(project, fleet, caller, inside=inside, pinned=pinned)
        elif args.fleet_command == "merge":
            api.fleet_merge(
                project, fleet, caller, inside=inside, pinned=pinned, into=args.into
            )
        else:
            api.fleet_resume(project, fleet, caller, inside=inside, pinned=pinned)
        return 0
    if args.command == "inbox":
        api.inbox(
            project,
            fleet,
            caller,
            inside=inside,
            pinned=pinned,
            all_fleets=args.all_fleets,
            all_projects=args.all_projects,
            as_json=args.json,
        )
        return 0
    if args.command == "master":
        if args.master_command == "start":
            api.master_start(
                project, fleet, caller, inside=inside, pinned=pinned, harness=args.harness
            )
        elif args.master_command == "stop":
            api.master_stop(project, fleet, caller, inside=inside, pinned=pinned)
        else:
            api.master_renew(
                project, fleet, caller, inside=inside, pinned=pinned, harness=args.harness
            )
        return 0
    if args.command == "brief":
        api.brief(
            project,
            fleet,
            caller,
            inside=inside,
            pinned=pinned,
            all_projects=args.all_projects,
            as_json=args.json,
        )
        return 0
    if args.command == "doctor":
        return api.doctor(project, fleet, caller, inside=inside, pinned=pinned)["code"]
    if args.command == "bridge":
        if getattr(args, "bridge_command", None) == "install":
            api.bridge_install(project, fleet, caller, inside=inside, pinned=pinned)
        else:
            api.bridge(project, fleet, caller, inside=inside, pinned=pinned)
        return 0
    action = args.roster_command
    layer = getattr(args, "project", False)
    if action == "list":
        api.roster_list(project, fleet, caller, inside=inside, pinned=pinned, as_json=args.json)
    elif action == "show":
        api.roster_show(
            project, fleet, caller, args.ROLE, inside=inside, pinned=pinned, as_json=args.json
        )
    elif action == "add":
        api.roster_add(
            project,
            fleet,
            caller,
            args.ROLE,
            inside=inside, pinned=pinned,
            from_file=args.from_file,
            project_layer=layer,
            base=args.base,
        )
    elif action == "update":
        api.roster_update(
            project,
            fleet,
            caller,
            args.ROLE,
            args.FIELD_VALUE,
            inside=inside, pinned=pinned,
            project_layer=layer,
            base=args.base,
        )
    else:
        api.roster_remove(
            project,
            fleet,
            caller,
            args.ROLE,
            inside=inside,
            pinned=pinned,
            project_layer=layer,
            base=args.base,
        )
    return 0


def _paused_notice(call: _Call | None) -> None:
    """A joined lead hears that the fleet is paused on its next command."""

    if call is None or call.inside or call.caller != _LEAD or call.project is None:
        return
    try:
        if not is_initialized(call.project):
            return
        from foil.delivery import tell_paused
        from foil.store import fleet_dir

        line = tell_paused(fleet_dir(call.project, call.fleet))
    except (FoilError, OSError):
        return
    if line:
        print(line, file=sys.stderr)


def _notice(args: argparse.Namespace) -> None:
    """The joined lead's new-items line, after every other command outside the fleet (F41)."""
    quiet = (
        args.command in {"view", "doctor", "bridge"}
        or (args.command == "seat" and args.seat_command in {"join", "leave"})
        or (args.command == "mail" and args.mail_command == "wait")
    )
    if quiet or in_seat():
        return
    try:
        root = discover_project()
        if is_initialized(root):
            notice(root)
    except (FoilError, OSError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    args = None
    call = None
    try:
        parser = _build_parser()
        args = parser.parse_args(None if argv is None else list(argv))
        call = _resolve(args)
        with bound(call.project, call.fleet, call.caller, inside=call.inside, pinned=call.pinned):
            code = _run(args, call)
            sys.stdout.flush()
            _paused_notice(call)
            _notice(args)
            return code
    except FoilError as exc:
        print(str(exc), file=sys.stderr)
        if args is not None and call is not None:
            with bound(
                call.project, call.fleet, call.caller, inside=call.inside, pinned=call.pinned
            ):
                _paused_notice(call)
                _notice(args)
        elif args is not None:
            _notice(args)
        return 1
    except BrokenPipeError:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 1
    except Exception:
        print("foil: unexpected error", file=sys.stderr)
        return 1
