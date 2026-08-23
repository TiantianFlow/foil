"""Capstan CLI entry point (CAP-001, CAP-012–CAP-014, CAP-027)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from capstan.status import PollStatusReader, StatusError, UnsupportedStatusSchemaVersion


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capstan",
        description=(
            "Headless coordination for complementary CLI-agent fleets. "
            "Management commands read and write structured state on disk."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    if args.command != "poll-status":
        parser.error(f"unsupported command: {args.command}")

    try:
        return _poll_status(args.state_dir, args.fleet)
    except UnsupportedStatusSchemaVersion as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except StatusError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
