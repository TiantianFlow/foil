"""Capstan CLI entry point (CAP-001, CAP-012–CAP-016, CAP-025–CAP-028)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from capstan.delivery import MessageDeliveryService, TmuxWakeService
from capstan.mailbox import Acknowledgement, MailboxError, MailboxMessage, MailboxStore
from capstan.onboarding import InitializationError, initialize_project
from capstan.registry import RegistryError, RegistryStore
from capstan.status import PollStatusReader, StatusError, UnsupportedStatusSchemaVersion


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capstan",
        description=(
            "Capstan · 运筹: headless coordination for complementary CLI-agent fleets. "
            "Management commands read and write structured state on disk."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser(
        "init",
        help="Scaffold a default fleet in an empty project directory.",
        description=(
            "Scaffold .capstan/fleet.toml and the complete default role set in an empty "
            "directory, then initialize versioned registry state. State precedence is "
            "CAPSTAN_STATE_DIR, the Git common directory, XDG_STATE_HOME, then the "
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
        help="Root directory containing versioned Capstan runtime state.",
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
    return parser


def _init_project(project_directory: Path) -> int:
    result = initialize_project(project_directory)
    _emit(result.to_dict())
    return 0


def _add_mailbox_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", required=True, metavar="PATH", type=Path)
    parser.add_argument("--fleet", required=True, metavar="FLEET_ID")
    parser.add_argument("--seat", required=True, metavar="SEAT_ID")


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
        parser.error(f"unsupported command: {args.command}")
    except UnsupportedStatusSchemaVersion as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (MailboxError, RegistryError, StatusError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except InitializationError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
