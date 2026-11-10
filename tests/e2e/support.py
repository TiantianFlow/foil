"""Shared pieces for the scenarios: fleet paths, seat scripts, and the timeout report."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from foil.lifecycle import peek_text, seat_rows
from foil.project import foil_root
from foil.store import load_registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fleet_dir(repo: Path, fleet: str = "main") -> Path:
    return foil_root(repo) / "fleets" / fleet


def board(repo: Path, fleet: str = "main") -> Path:
    return fleet_dir(repo, fleet) / "board"


def fleets(repo: Path) -> list[str]:
    root = foil_root(repo) / "fleets"
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def scripts(repo: Path, fixture: str, seats: dict[str, str], fleet: str = "main") -> None:
    """Give each seat its script from the fixture's `scripts/` folder."""

    destination = fleet_dir(repo, fleet) / "run" / "fake"
    destination.mkdir(parents=True, exist_ok=True)
    for seat, name in seats.items():
        shutil.copy(FIXTURES / fixture / "scripts" / name, destination / f"{seat}.json")


def launches(repo: Path, seat: str, fleet: str = "main") -> list[dict]:
    """What the double in that seat was started with, oldest first."""

    path = fleet_dir(repo, fleet) / "run" / "fake" / f"{seat}.launches"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _fleet_report(repo: Path, fleet: str) -> list[str]:
    chunks = [f"===== fleet {fleet}: seats"]
    try:
        registry = load_registry(repo, fleet)
        rows = seat_rows(repo, registry)
    except Exception as exc:
        return [*chunks, f"could not list seats: {exc}"]
    if not rows:
        chunks.append("no seats")
    for row in rows:
        launched = f"{row['harness']}/{row['model']}" if row["model"] else row["harness"]
        chunks.append(f"{row['name']}\t{row['template']}\t{row['state']}\t{launched}")
    for row in rows:
        if row["state"] == "killed":
            continue
        chunks.append(f"----- pane of {row['name']} ({row['state']})")
        try:
            chunks.append(peek_text(repo, row["name"], lines=40, fleet=fleet).rstrip("\n"))
        except Exception as exc:
            chunks.append(str(exc))
    logs = fleet_dir(repo, fleet) / "run" / "fake"
    if logs.is_dir():
        for path in sorted(logs.glob("*.log")):
            chunks.append(f"----- {path.name}\n{path.read_text(encoding='utf-8').rstrip()}")
    return chunks


def timeout_report(repo: Path) -> str:
    """Every fleet's seat list, each seat's pane, and each double's log.

    A wait that times out prints this. It must not raise: the timeout is
    the failure to show.
    """

    try:
        names = fleets(repo)
    except Exception as exc:
        return f"no report: {exc}"
    chunks: list[str] = []
    for fleet in names:
        chunks.extend(_fleet_report(repo, fleet))
    return "\n".join(chunks) if chunks else "no fleet"


def wait(
    repo: Path, predicate: Callable[[], object], timeout: float = 60, what: str = ""
) -> None:
    """Poll until the predicate holds. A timeout says what was awaited and shows the fleet."""

    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    label = f" waiting for {what}" if what else ""
    raise AssertionError(f"timed out after {timeout:g}s{label}\n{timeout_report(repo)}")


def close(repo: Path) -> None:
    """Remove every fleet's tmux session, whatever the scenario left."""

    for fleet in fleets(repo):
        try:
            session = str(load_registry(repo, fleet).get("tmux_session") or "")
        except Exception:
            continue
        if session:
            subprocess.run(
                ["tmux", "kill-session", "-t", session], check=False, capture_output=True
            )
