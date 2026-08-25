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
from foil.runtime_config import ConfigError, load_fleet_config
from foil.status import PollStatusReader, StatusError, UnsupportedStatusSchemaVersion
from foil.tmux import TmuxError


def _add_runtime_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register lifecycle commands without owning the top-level parser."""

    commands = {
        "launch": "Launch every configured seat in verified detached tmux windows.",
        "status": "Reconcile registry, structured native IDs, and verified tmux liveness.",
        "stop": "Stop only tmux windows whose stable user-option markers match.",
    }
    for command, description in commands.items():
        runtime = subparsers.add_parser(command, help=description, description=description)
        _add_lifecycle_flags(runtime)

    resume = subparsers.add_parser(
        "resume",
        help="Revive live tmux, resume native context, or start a fresh incarnation.",
        description=(
            "Revive live tmux, resume native context, or log a fresh start. "
            "Pass --fresh to request a clean context and incarnation lineage."
        ),
    )
    _add_lifecycle_flags(resume)
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


def _add_lifecycle_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        required=True,
        metavar="PATH",
        type=Path,
        help="Versioned fleet TOML containing usage pools and seat identities.",
    )
    parser.add_argument(
        "--state-dir",
        required=True,
        metavar="PATH",
        type=Path,
        help="Root directory for versioned registry, status, and audit state.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit deterministic machine-readable JSON.",
    )


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
        help="Scaffold a default fleet in an empty project directory.",
        description=(
            "Scaffold the complete role plan in .foil/fleet.toml and a directly "
            "consumable two-seat .foil/runtime.toml in an empty directory, then "
            "initialize versioned registry state. After init, create the expected local "
            "Git identity with `git init -b foil-demo`. State precedence is "
            "FOIL_STATE_DIR, the Git common directory, XDG_STATE_HOME, then the "
            "documented platform fallback (CAP-016, CAP-025)."
        ),
    )
    init.add_argument(
        "directory",
        nargs="?",
        default=Path("."),
        type=Path,
        metavar="DIRECTORY",
        help="Empty project directory to initialize (default: current directory).",
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
        help="Queue an immutable seat message and attempt a bounded tmux wake.",
        description=(
            "Atomically queue a durable mailbox message, report duplicate delivery, "
            "and generically wake a validated live tmux seat without injecting the body."
        ),
    )
    _add_mailbox_location(send_message)
    send_message.add_argument("--sender", required=True, metavar="STABLE_ID")
    send_message.add_argument("--message-id", metavar="STABLE_ID")
    send_message.add_argument("--task", metavar="STABLE_ID")
    send_message.add_argument("--body", required=True)

    ack_message = subparsers.add_parser(
        "ack-message",
        help="Write an immutable acknowledgement for a queued message.",
    )
    _add_mailbox_location(ack_message)
    ack_message.add_argument("--message", required=True, metavar="MESSAGE_ID")
    ack_message.add_argument("--actor", required=True, metavar="STABLE_ID")
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
    notepad_ack.add_argument("--actor", required=True, metavar="STABLE_ID")

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
    memory_accept.add_argument("--actor", required=True, metavar="STABLE_ID")

    memory_reject = subparsers.add_parser(
        "memory-reject",
        help="Reject a proposed or accepted memory lesson.",
    )
    _add_collaboration_location(memory_reject)
    memory_reject.add_argument("--lesson", required=True, metavar="LESSON_ID")
    memory_reject.add_argument("--actor", required=True, metavar="STABLE_ID")

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
    dispatch_cmd.add_argument("--config", required=True, metavar="PATH", type=Path)
    dispatch_cmd.add_argument("--state-dir", required=True, metavar="PATH", type=Path)
    dispatch_cmd.add_argument("--json", action="store_true")
    dispatch_cmd.add_argument("--capability", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="Check prerequisites and print a dry-run launch plan.",
        description=(
            "Discover tmux, git, and seat CLIs without inspecting credentials. "
            "Pass --apply to create independent builder worktrees."
        ),
    )
    doctor.add_argument("--config", required=True, metavar="PATH", type=Path)
    doctor.add_argument("--json", action="store_true")
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
    _add_lifecycle_flags(set_state)
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


def _send_message(args: argparse.Namespace) -> int:
    store = MailboxStore(args.state_dir)
    message_id = args.message_id or f"msg-{uuid.uuid4().hex}"
    try:
        existing = store.read_message(args.fleet, args.seat, message_id)
    except FileNotFoundError:
        existing = None
    if (
        existing is not None
        and existing.sender_id == args.sender
        and existing.body == args.body
        and existing.causal_task_id == args.task
    ):
        message = existing
    else:
        message = MailboxMessage(
            fleet_id=args.fleet,
            message_id=message_id,
            recipient_seat_id=args.seat,
            sender_id=args.sender,
            created_at=_now(),
            body=args.body,
            causal_task_id=args.task,
            extensions={},
        )
    result = MessageDeliveryService(
        store,
        RegistryStore(args.state_dir),
        TmuxWakeService(),
    ).send(message)
    _emit(result.to_dict())
    return 0


def _ack_message(args: argparse.Namespace) -> int:
    store = MailboxStore(args.state_dir)
    acknowledgement_id = args.ack_id or f"ack-{uuid.uuid4().hex}"
    try:
        existing = store.read_acknowledgement(args.fleet, args.seat, args.message)
    except FileNotFoundError:
        existing = None
    if (
        existing is not None
        and existing.acknowledgement_id == acknowledgement_id
        and existing.acknowledged_by == args.actor
    ):
        acknowledgement = existing
    else:
        acknowledgement = Acknowledgement(
            fleet_id=args.fleet,
            message_id=args.message,
            acknowledgement_id=acknowledgement_id,
            recipient_seat_id=args.seat,
            acknowledged_by=args.actor,
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


def _runtime_payload(config, seats: list, *, as_json: bool) -> int:
    payload = {"fleet_id": config.fleet_id, "seats": seats}
    if as_json:
        _emit(payload)
    else:
        for seat in seats:
            action = f" {seat['action']}" if "action" in seat else ""
            print(f"{seat['seat_id']}: {seat['state']}{action}")
    return 0


def _runtime_command(
    command: str,
    config_path: Path,
    state_dir: Path,
    *,
    as_json: bool,
    **kwargs,
) -> int:
    config = load_fleet_config(config_path)
    controller = RuntimeController(config, state_dir)
    operation = getattr(controller, command)
    seats = operation(**kwargs)
    return _runtime_payload(config, seats, as_json=as_json)


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
                    author=args.author,
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
                    actor=args.actor,
                )
            )
            return 0
        if args.command == "memory-propose":
            _emit(
                propose_memory(
                    args.state_dir,
                    args.fleet,
                    args.lesson,
                    author=args.author,
                    body=args.body,
                    task_id=args.task,
                )
            )
            return 0
        if args.command == "memory-accept":
            _emit(
                accept_memory(
                    args.state_dir, args.fleet, args.lesson, actor=args.actor
                )
            )
            return 0
        if args.command == "memory-reject":
            _emit(
                reject_memory(
                    args.state_dir, args.fleet, args.lesson, actor=args.actor
                )
            )
            return 0
        if args.command == "memory-supersede":
            _emit(
                supersede_memory(
                    args.state_dir,
                    args.fleet,
                    args.lesson,
                    author=args.author,
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
            config = load_fleet_config(args.config)
            _emit(dispatch(config, capability=args.capability))
            return 0
        if args.command == "doctor":
            config = load_fleet_config(args.config)
            report = doctor_report(config, apply=args.apply)
            if args.json:
                _emit(report)
            else:
                print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "set-state":
            return _runtime_command(
                "set_state",
                args.config,
                args.state_dir,
                as_json=args.json,
                seat_id=args.seat,
                state=args.state,
            )
        if args.command == "catalog-list":
            _emit(list_personas(args.path))
            return 0
        if args.command == "catalog-map":
            _emit(map_persona(args.path, args.persona))
            return 0
        if args.command in {"launch", "status", "stop"}:
            return _runtime_command(
                args.command,
                args.config,
                args.state_dir,
                as_json=args.json,
            )
        if args.command == "resume":
            return _runtime_command(
                "resume",
                args.config,
                args.state_dir,
                as_json=args.json,
                seat_id=args.seat,
                force_fresh=args.fresh,
            )
        parser.error(f"unsupported command: {args.command}")
    except UnsupportedStatusSchemaVersion as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (
        AdapterError,
        CatalogError,
        ConfigError,
        DoctorError,
        InitializationError,
        LifecycleError,
        MailboxError,
        RegistryError,
        StatusError,
        TmuxError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
