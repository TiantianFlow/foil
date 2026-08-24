"""Capstan CLI entry point (CAP-001, CAP-012–CAP-016, CAP-025–CAP-028)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from capstan.onboarding import InitializationError, initialize_project
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
    return parser


def _init_project(project_directory: Path) -> int:
    result = initialize_project(project_directory)
    sys.stdout.write(json.dumps(result.to_dict(), sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


def _poll_status(state_dir: Path, fleet_id: str) -> int:
    reader = PollStatusReader(state_dir)
    snapshots = reader.read_fleet(fleet_id)
    payload = {
        "fleet_id": fleet_id,
        "seats": [snapshot.to_dict() for snapshot in snapshots],
    }
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            return _init_project(args.directory)
        if args.command == "poll-status":
            return _poll_status(args.state_dir, args.fleet)
        parser.error(f"unsupported command: {args.command}")
    except UnsupportedStatusSchemaVersion as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except StatusError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except InitializationError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
