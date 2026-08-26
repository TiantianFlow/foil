"""Foil CLI entry point (CAP-001, CAP-012–CAP-016, CAP-025–CAP-028)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from foil.adapters import AdapterError
from foil.catalog import CatalogError, list_personas, map_persona
from foil.delivery import MessageDeliveryService, TmuxWakeService
from foil.dispatch import dispatch
from foil.doctor import DoctorError, doctor_report
from foil.fleet import OPERATOR_ACTOR, FleetError, FleetStore, resolve_caller
from foil.mailbox import Acknowledgement, MailboxError, MailboxMessage, MailboxStore
from foil.memory import (
    accept_memory,
    propose_memory,
    reject_memory,
    status_memory,
    supersede_memory,
)
from foil.notepad import ack_notepad, read_notepad, write_notepad
from foil.onboarding import InitializationError, initialize_project
from foil.registry import RegistryError, RegistryStore
from foil.runtime import RuntimeController
from foil.runtime import RuntimeError as LifecycleError
from foil.status import PollStatusReader, StatusError, UnsupportedStatusSchemaVersion
from foil.tmux import TmuxError


def _add_fleet_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        required=True,
        metavar="PATH",
        type=Path,
        help="Root directory for versioned registry, status, and audit state.",
    )
    parser.add_argument(
        "--fleet",
        required=True,
        metavar="FLEET_ID",
        help="Live fleet identity.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable JSON.",
    )
    parser.add_argument(
        "--actor",
        metavar="SEAT_ID",
        help=(
            "Calling seat when FOIL_SEAT_ID is unset. An in-seat caller cannot "
            "override FOIL_SEAT_ID. Cooperative same-user protection, not isolation."
        ),
    )


def _add_runtime_parsers(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser(
        "status",
        help="Reconcile the live registry with verified tmux liveness.",
    )
    _add_fleet_flags(status)

    resume = subparsers.add_parser(
        "resume",
        help="Resume persisted seats after interruption. Not a recipe replay.",
        description=(
            "Revive live tmux, use native resume when recorded, or start fresh. "
            "Membership comes from the live registry, not a TOML roster."
        ),
    )
    _add_fleet_flags(resume)
    resume.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore live tmux and native session IDs and start a new incarnation.",
    )
    resume.add_argument(
        "--seat",
        metavar="SEAT_ID",
        help="Limit resume to one seat identity.",
    )

    seat = subparsers.add_parser("seat", help="Lead-controlled live seat lifecycle.")
    seat_sub = seat.add_subparsers(dest="seat_command", required=True)

    spawn = seat_sub.add_parser("spawn", help="Create one seat in the live fleet.")
    _add_fleet_flags(spawn)
    spawn.add_argument("--seat", required=True, metavar="SEAT_ID")
    spawn.add_argument("--cli", required=True)
    spawn.add_argument("--lead", action="store_true")
    spawn.add_argument("--role", metavar="ROLE_ID")
    spawn.add_argument("--role-file", type=Path)
    spawn.add_argument(
        "--isolated",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Isolate in worktrees/<seat_id>. Workers default to isolated.",
    )
    spawn.add_argument(
        "--shared-cwd",
        action="store_true",
        help="Allow this seat to share a working directory with another seat.",
    )
    spawn.add_argument("--cwd", type=Path)
    spawn.add_argument("--model")
    spawn.add_argument("--display-name")
    spawn.add_argument("--resume-arg", action="append", dest="resume_args")
    spawn.add_argument("--session-list-arg", action="append", dest="session_list_args")
    spawn.add_argument("--session-id-pointer")
    spawn.add_argument("--session-cwd-pointer")
    spawn.add_argument(
        "--session-capture",
        default="generated_uuid",
        choices=("none", "generated_uuid", "command_json_list_delta"),
    )
    spawn.add_argument(
        "--permission",
        choices=("supervised", "auto"),
        default="supervised",
        help=(
            "Provider permission profile. supervised asks for approvals. "
            "auto uses adapter-declared flags. Default supervised."
        ),
    )
    spawn.add_argument(
        "launch_argv",
        nargs=argparse.REMAINDER,
        help="CLI arguments after -- . Example: -- --model claude-sonnet-5",
    )

    listed = seat_sub.add_parser("list", help="List live seats.")
    _add_fleet_flags(listed)

    inspect = seat_sub.add_parser("inspect", help="Inspect one live seat.")
    _add_fleet_flags(inspect)
    inspect.add_argument("--seat", required=True, metavar="SEAT_ID")

    stop = seat_sub.add_parser("stop", help="Stop one verified seat, or all seats.")
    _add_fleet_flags(stop)
    stop.add_argument("--seat", metavar="SEAT_ID")
    stop.add_argument("--all", action="store_true", dest="all_seats")

    remove = seat_sub.add_parser("remove", help="Stop and delete one seat record.")
    _add_fleet_flags(remove)
    remove.add_argument("--seat", required=True, metavar="SEAT_ID")

    wake = seat_sub.add_parser(
        "wake",
        help="Send a bounded tmux wake for queued mailbox messages.",
    )
    _add_fleet_flags(wake)
    wake.add_argument("--seat", required=True, metavar="SEAT_ID")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="foil",
        description=(
            "Foil · 运筹 — your agents' loyal opposition. Headless coordination for "
            "complementary CLI-agent fleets. "
            "Management commands read and write structured state on disk."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_runtime_parsers(subparsers)

    init = subparsers.add_parser(
        "init",
        help="Scaffold a role library and an empty live fleet.",
        description=(
            "Write role templates to .foil/roles/ and initialize an empty live fleet "
            "registry. Accepts an empty directory or an existing Git repository; "
            "tracked and untracked files and Git state are preserved. A non-empty "
            "directory outside Git and conflicting .foil or fleet-state collisions "
            "fail closed. No seats are created. Spawn a lead next with "
            "`foil seat spawn --lead`. In a new empty directory, create a local Git "
            "identity after init with `git init -b foil-demo`. State precedence is "
            "FOIL_STATE_DIR, the Git common directory, XDG_STATE_HOME, then the "
            "documented platform fallback."
        ),
    )
    init.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,
        metavar="DIRECTORY",
        help=(
            "Project directory to initialize: empty or an existing Git repository "
            "(default: current directory)."
        ),
    )

    poll_status = subparsers.add_parser(
        "poll-status",
        help="Read structured seat status files and emit machine-readable JSON.",
        description=(
            "Reconstruct current fleet seat status from versioned structured files "
            "under --state-dir. Output is deterministic JSON for agent callers; "
            "terminal buffers are never inspected (CAP-012–CAP-014)."
        ),
    )
    poll_status.add_argument(
        "--state-dir",
        required=True,
        metavar="PATH",
        type=Path,
        help="Root directory containing versioned Foil runtime state.",
    )
    poll_status.add_argument(
        "--fleet",
        required=True,
        metavar="FLEET_ID",
        help="Fleet identifier whose seat status files should be read.",
    )

    send_message = subparsers.add_parser(
        "send-message",
        help="Queue an immutable seat message. Wake is opt-in.",
        description=(
            "Atomically queue a durable mailbox message without injecting the body. "
            "Pass --wake or run `foil seat wake` after inspecting tmux."
        ),
    )
    _add_mailbox_location(send_message)
    send_message.add_argument("--sender", required=True, metavar="STABLE_ID")
    send_message.add_argument("--message-id", metavar="STABLE_ID")
    send_message.add_argument("--task", metavar="STABLE_ID")
    send_message.add_argument("--body", required=True)
    send_message.add_argument(
        "--wake",
        action="store_true",
        help="After queuing, send a bounded tmux wake. Default is persist only.",
    )

    ack_message = subparsers.add_parser(
        "ack-message",
        help="Write an immutable acknowledgement for a queued message.",
    )
    _add_mailbox_location(ack_message)
    ack_message.add_argument("--message", required=True, metavar="MESSAGE_ID")
    ack_message.add_argument("--actor", metavar="STABLE_ID")
    ack_message.add_argument("--ack-id", metavar="STABLE_ID")

    message_status = subparsers.add_parser(
        "message-status",
        help="Read pollable queued or acknowledged message delivery state.",
    )
    _add_mailbox_location(message_status)
    message_status.add_argument("--message", required=True, metavar="MESSAGE_ID")

    notepad_write = subparsers.add_parser(
        "notepad-write",
        help="Write a shared notepad brief that seats can read.",
    )
    _add_collaboration_location(notepad_write)
    notepad_write.add_argument("--notepad", required=True, metavar="NOTEPAD_ID")
    notepad_write.add_argument("--author", required=True, metavar="STABLE_ID")
    notepad_write.add_argument("--body", required=True)

    notepad_read = subparsers.add_parser(
        "notepad-read",
        help="Read a shared notepad brief.",
    )
    _add_collaboration_location(notepad_read)
    notepad_read.add_argument("--notepad", required=True, metavar="NOTEPAD_ID")

    notepad_ack = subparsers.add_parser(
        "notepad-ack",
        help="Acknowledge that a seat has read a shared notepad.",
    )
    _add_collaboration_location(notepad_ack)
    notepad_ack.add_argument("--notepad", required=True, metavar="NOTEPAD_ID")
    notepad_ack.add_argument("--actor", metavar="STABLE_ID")

    memory_propose = subparsers.add_parser(
        "memory-propose",
        help="Propose a reviewed memory lesson.",
    )
    _add_collaboration_location(memory_propose)
    memory_propose.add_argument("--lesson", required=True, metavar="LESSON_ID")
    memory_propose.add_argument("--author", required=True, metavar="STABLE_ID")
    memory_propose.add_argument("--task", required=True, metavar="TASK_ID")
    memory_propose.add_argument("--body", required=True)

    memory_accept = subparsers.add_parser(
        "memory-accept",
        help="Accept a proposed memory lesson.",
    )
    _add_collaboration_location(memory_accept)
    memory_accept.add_argument("--lesson", required=True, metavar="LESSON_ID")
    memory_accept.add_argument("--actor", metavar="STABLE_ID")

    memory_reject = subparsers.add_parser(
        "memory-reject",
        help="Reject a proposed or accepted memory lesson.",
    )
    _add_collaboration_location(memory_reject)
    memory_reject.add_argument("--lesson", required=True, metavar="LESSON_ID")
    memory_reject.add_argument("--actor", metavar="STABLE_ID")

    memory_supersede = subparsers.add_parser(
        "memory-supersede",
        help="Supersede a lesson with a replacement proposal.",
    )
    _add_collaboration_location(memory_supersede)
    memory_supersede.add_argument("--lesson", required=True, metavar="LESSON_ID")
    memory_supersede.add_argument("--author", required=True, metavar="STABLE_ID")
    memory_supersede.add_argument("--replacement", required=True, metavar="LESSON_ID")
    memory_supersede.add_argument("--body", required=True)
    memory_supersede.add_argument("--task", required=True, metavar="TASK_ID")

    memory_status = subparsers.add_parser(
        "memory-status",
        help="Read a memory lesson record.",
    )
    _add_collaboration_location(memory_status)
    memory_status.add_argument("--lesson", required=True, metavar="LESSON_ID")

    dispatch_cmd = subparsers.add_parser(
        "dispatch",
        help="Rank seats from structured non-interactive usage probes.",
        description=(
            "Probe each matching seat CLI with `usage --format json` and select the "
            "lowest active_load. Does not scrape interactive slash /usage."
        ),
    )
    _add_fleet_flags(dispatch_cmd)
    dispatch_cmd.add_argument("--capability", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="Check host tools, live-seat worktrees, and leftover fleet files.",
        description=(
            "Discover tmux, git, and seat CLIs without inspecting credentials. "
            "Report orphan tmux windows, stale status, leftover plans, and retained "
            "worktrees. Pass --apply to create missing isolated worktrees only."
        ),
    )
    _add_fleet_flags(doctor)
    doctor.add_argument(
        "--apply",
        action="store_true",
        help="Create missing independent worktrees as local Git clones.",
    )

    set_state = subparsers.add_parser(
        "set-state",
        help="Record an explicit waiting, idle, or working operator transition.",
        description=(
            "Write a structured operator state for a live seat. Live status honors "
            "waiting/idle without inspecting pane text."
        ),
    )
    _add_fleet_flags(set_state)
    set_state.add_argument("--seat", required=True, metavar="SEAT_ID")
    set_state.add_argument("--state", required=True, choices=("waiting", "idle", "working"))

    catalog_list = subparsers.add_parser(
        "catalog-list",
        help="List local Markdown persona files without downloading a catalog.",
    )
    catalog_list.add_argument("--path", required=True, metavar="PATH", type=Path)
    catalog_list.add_argument("--json", action="store_true")

    catalog_map = subparsers.add_parser(
        "catalog-map",
        help="Map a local Markdown persona onto Foil seat staffing fields.",
    )
    catalog_map.add_argument("--path", required=True, metavar="PATH", type=Path)
    catalog_map.add_argument("--persona", required=True)
    catalog_map.add_argument("--json", action="store_true")
    return parser


def _init_project(project_directory: Path) -> int:
    result = initialize_project(project_directory)
    _emit(result.to_dict())
    return 0


def _add_mailbox_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True, metavar="PATH", type=Path)
    parser.add_argument("--fleet", required=True, metavar="FLEET_ID")
    parser.add_argument("--seat", required=True, metavar="SEAT_ID")


def _add_collaboration_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True, metavar="PATH", type=Path)
    parser.add_argument("--fleet", required=True, metavar="FLEET_ID")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")


def _poll_status(state_dir: Path, fleet_id: str) -> int:
    reader = PollStatusReader(state_dir)
    snapshots = reader.read_fleet(fleet_id)
    payload = {
        "fleet_id": fleet_id,
        "seats": [snapshot.to_dict() for snapshot in snapshots],
    }
    _emit(payload)
    return 0


def _bound_actor(actor_flag: str | None, *, required: bool = True) -> str:
    caller = resolve_caller(actor_flag)
    if required and not caller:
        raise FleetError("actor is required")
    return caller


def _bound_named_id(flag: str | None, field_name: str) -> str:
    seat = resolve_caller(None)
    if seat != OPERATOR_ACTOR and flag and flag != seat:
        raise FleetError(
            f"in-seat caller {seat} cannot claim {field_name} {flag}"
        )
    if seat != OPERATOR_ACTOR:
        return seat
    if not flag:
        raise FleetError(f"{field_name} is required")
    return flag


def _send_message(args: argparse.Namespace) -> int:
    sender = _bound_named_id(args.sender, "sender")
    store = MailboxStore(args.state_dir)
    message_id = args.message_id or f"msg-{uuid.uuid4().hex}"
    try:
        existing = store.read_message(args.fleet, args.seat, message_id)
    except FileNotFoundError:
        existing = None
    if (
        existing is not None
        and existing.sender_id == sender
        and existing.body == args.body
        and existing.causal_task_id == args.task
    ):
        message = existing
    else:
        message = MailboxMessage(
            fleet_id=args.fleet,
            message_id=message_id,
            recipient_seat_id=args.seat,
            sender_id=sender,
            created_at=_now(),
            body=args.body,
            causal_task_id=args.task,
            extensions={},
        )
    result = MessageDeliveryService(
        store,
        RegistryStore(args.state_dir),
        TmuxWakeService(),
    ).send(message, wake=args.wake)
    _emit(result.to_dict())
    return 0


def _ack_message(args: argparse.Namespace) -> int:
    actor = _bound_actor(args.actor)
    store = MailboxStore(args.state_dir)
    acknowledgement_id = args.ack_id or f"ack-{uuid.uuid4().hex}"
    try:
        existing = store.read_acknowledgement(args.fleet, args.seat, args.message)
    except FileNotFoundError:
        existing = None
    if (
        existing is not None
        and existing.acknowledgement_id == acknowledgement_id
        and existing.acknowledged_by == actor
    ):
        acknowledgement = existing
    else:
        acknowledgement = Acknowledgement(
            fleet_id=args.fleet,
            message_id=args.message,
            acknowledgement_id=acknowledgement_id,
            recipient_seat_id=args.seat,
            acknowledged_by=actor,
            acknowledged_at=_now(),
            extensions={},
        )
    result = store.acknowledge(acknowledgement)
    _emit(
        {
            "acknowledgement": result.record.to_dict(),
            "duplicate": result.duplicate,
            "state": "acknowledged",
        }
    )
    return 0


def _message_status(args: argparse.Namespace) -> int:
    delivery = MailboxStore(args.state_dir).delivery(
        args.fleet,
        args.seat,
        args.message,
    )
    _emit(delivery.to_dict())
    return 0


def _controller(args: argparse.Namespace) -> RuntimeController:
    return RuntimeController(args.state_dir, args.fleet)


def _runtime_payload(
    fleet_id: str,
    seats: list,
    *,
    as_json: bool,
    extra: dict | None = None,
) -> int:
    payload = {"fleet_id": fleet_id, "seats": seats}
    if extra:
        payload.update(extra)
    if as_json:
        _emit(payload)
    else:
        for seat in seats:
            action = f" {seat['action']}" if "action" in seat else ""
            print(f"{seat['seat_id']}: {seat['state']}{action}")
    return 0


def _seat_command(args: argparse.Namespace) -> int:
    if args.seat_command == "wake":
        result = MessageDeliveryService(
            MailboxStore(args.state_dir),
            RegistryStore(args.state_dir),
            TmuxWakeService(),
        ).wake(args.fleet, args.seat)
        payload = {
            "fleet_id": args.fleet,
            "seat_id": args.seat,
            "wake": result.to_dict(),
        }
        if args.json:
            _emit(payload)
        else:
            print(result.state.value)
        return 0
    controller = _controller(args)
    actor = getattr(args, "actor", None)
    if args.seat_command == "spawn":
        seats = controller.spawn(
            seat_id=args.seat,
            cli=args.cli,
            launch_argv=tuple(args.launch_argv or ()),
            actor=actor,
            display_name=args.display_name,
            resume_argv=tuple(args.resume_args) if args.resume_args else None,
            session_capture=args.session_capture,
            session_list_argv=tuple(args.session_list_args)
            if args.session_list_args
            else None,
            session_id_pointer=args.session_id_pointer,
            session_cwd_pointer=args.session_cwd_pointer,
            role_id=args.role,
            role_path=str(args.role_file) if args.role_file else None,
            isolated=args.isolated,
            shared_cwd=args.shared_cwd,
            working_directory=args.cwd,
            model=args.model,
            lead=args.lead,
            permission=args.permission,
        )
        return _runtime_payload(args.fleet, seats, as_json=args.json)
    if args.seat_command == "list":
        payload = controller.list_seats()
        if args.json:
            _emit(payload)
        else:
            for seat in payload["seats"]:
                marker = " lead" if seat["is_lead"] else ""
                print(f"{seat['seat_id']}:{marker} {seat.get('cli') or ''}")
        return 0
    if args.seat_command == "inspect":
        _emit(controller.inspect(args.seat))
        return 0
    if args.seat_command == "stop":
        seats = controller.stop(
            seat_id=args.seat,
            all_seats=args.all_seats,
            actor=actor,
        )
        return _runtime_payload(args.fleet, seats, as_json=args.json)
    if args.seat_command == "remove":
        seats = controller.remove(seat_id=args.seat, actor=actor)
        return _runtime_payload(args.fleet, seats, as_json=args.json)
    raise ValueError(f"unsupported seat command: {args.seat_command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _init_project(args.directory)
        if args.command == "poll-status":
            return _poll_status(args.state_dir, args.fleet)
        if args.command == "send-message":
            return _send_message(args)
        if args.command == "ack-message":
            return _ack_message(args)
        if args.command == "message-status":
            return _message_status(args)
        if args.command == "notepad-write":
            _emit(
                write_notepad(
                    args.state_dir,
                    args.fleet,
                    args.notepad,
                    author=_bound_named_id(args.author, "author"),
                    body=args.body,
                )
            )
            return 0
        if args.command == "notepad-read":
            _emit(read_notepad(args.state_dir, args.fleet, args.notepad))
            return 0
        if args.command == "notepad-ack":
            _emit(
                ack_notepad(
                    args.state_dir,
                    args.fleet,
                    args.notepad,
                    actor=_bound_actor(args.actor),
                )
            )
            return 0
        if args.command == "memory-propose":
            _emit(
                propose_memory(
                    args.state_dir,
                    args.fleet,
                    args.lesson,
                    author=_bound_named_id(args.author, "author"),
                    body=args.body,
                    task_id=args.task,
                )
            )
            return 0
        if args.command == "memory-accept":
            actor = FleetStore(args.state_dir).require_reviewer(args.fleet, args.actor)
            _emit(
                accept_memory(
                    args.state_dir, args.fleet, args.lesson, actor=actor
                )
            )
            return 0
        if args.command == "memory-reject":
            actor = FleetStore(args.state_dir).require_reviewer(args.fleet, args.actor)
            _emit(
                reject_memory(
                    args.state_dir, args.fleet, args.lesson, actor=actor
                )
            )
            return 0
        if args.command == "memory-supersede":
            author = FleetStore(args.state_dir).require_reviewer(
                args.fleet, args.author
            )
            _emit(
                supersede_memory(
                    args.state_dir,
                    args.fleet,
                    args.lesson,
                    author=author,
                    replacement_id=args.replacement,
                    body=args.body,
                    task_id=args.task,
                )
            )
            return 0
        if args.command == "memory-status":
            _emit(status_memory(args.state_dir, args.fleet, args.lesson))
            return 0
        if args.command == "dispatch":
            _emit(
                dispatch(
                    args.state_dir,
                    args.fleet,
                    capability=args.capability,
                )
            )
            return 0
        if args.command == "doctor":
            report = doctor_report(args.state_dir, args.fleet, apply=args.apply)
            if args.json:
                _emit(report)
            else:
                print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "set-state":
            seats = _controller(args).set_state(args.seat, args.state)
            return _runtime_payload(args.fleet, seats, as_json=args.json)
        if args.command == "catalog-list":
            _emit(list_personas(args.path))
            return 0
        if args.command == "catalog-map":
            _emit(map_persona(args.path, args.persona))
            return 0
        if args.command == "status":
            controller = _controller(args)
            seats = controller.status()
            extra = {"lead_seat_id": controller.fleet.lead_seat_id}
            return _runtime_payload(args.fleet, seats, as_json=args.json, extra=extra)
        if args.command == "resume":
            seats = _controller(args).resume(
                seat_id=args.seat,
                force_fresh=args.fresh,
                actor=getattr(args, "actor", None),
            )
            return _runtime_payload(args.fleet, seats, as_json=args.json)
        if args.command == "seat":
            return _seat_command(args)
        parser.error(f"unsupported command: {args.command}")
    except UnsupportedStatusSchemaVersion as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (
        AdapterError,
        CatalogError,
        DoctorError,
        FleetError,
        InitializationError,
        LifecycleError,
        MailboxError,
        RegistryError,
        StatusError,
        TmuxError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
