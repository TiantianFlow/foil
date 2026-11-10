"""Shared pieces for the scenarios: fleet paths, seat scripts, and the timeout report."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from foil.lifecycle import peek_text, seat_rows
from foil.project import foil_root
from foil.store import load_registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_OUTSIDE_AGENT = Path(__file__).resolve().parents[1] / "harness" / "outside_agent.py"


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


def handled(repo: Path, seat: str, fleet: str = "main") -> int:
    """How many mails and room files the double in that seat has finished with.

    A scenario that stops a seat on purpose waits for this first. A double
    that is stopped inside a step takes that mail again on its next launch.
    """

    path = fleet_dir(repo, fleet) / "run" / "fake" / f"{seat}.handled"
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("/"))


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


def states(repo: Path, fleet: str = "main") -> dict[str, str]:
    """Each seat's state as `seat list` gives it: alive, dead or killed."""

    return {row["name"]: row["state"] for row in seat_rows(repo, load_registry(repo, fleet))}


def wait(
    repo: Path,
    predicate: Callable[[], object],
    timeout: float = 60,
    what: str = "",
    *,
    dead_ok: bool = False,
) -> None:
    """Poll until the predicate holds. A failure says what was awaited and shows the fleet.

    A seat whose process exited ends the wait at once, unless the scenario
    expects that: nothing more will come from it.
    """

    label = f" waiting for {what}" if what else ""
    deadline = time.monotonic() + timeout
    checked = 0.0
    while True:
        if predicate():
            return
        now = time.monotonic()
        if now >= deadline:
            break
        if not dead_ok and now - checked >= 0.5:
            checked = now
            dead = sorted(
                name
                for fleet in fleets(repo)
                for name, state in states(repo, fleet).items()
                if state == "dead"
            )
            if dead and not predicate():
                names = ", ".join(dead)
                raise AssertionError(f"{names} died{label}\n{timeout_report(repo)}")
        time.sleep(0.1)
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


@dataclass
class OutsideAgent:
    """The human's own agent as a running process. See tests/harness/outside_agent.py."""

    repo: Path
    process: subprocess.Popen[str]
    transcript: Path

    def entries(self) -> list[dict]:
        if not self.transcript.is_file():
            return []
        return json.loads(self.transcript.read_text(encoding="utf-8"))["entries"]

    def finish(self, timeout: float = 90) -> list[dict]:
        """Wait for the script to end, and return what each command did."""

        try:
            _out, error = self.process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            _out, error = self.process.communicate()
            error = f"still running after {timeout:g}s\n{error}"
        if self.process.returncode != 0:
            done = json.dumps(self.entries()[-3:], indent=1)
            raise AssertionError(
                f"the outside agent stopped: {error}\n{done}\n{timeout_report(self.repo)}"
            )
        return self.entries()


def outside_agent(
    repo: Path, scratch: Path, name: str, steps: list[dict], *, fleet: str | None = None
) -> OutsideAgent:
    """Start a caller outside the fleet as its own process, in the repository.

    Its environment is the test's, without a seat identity. `fleet` sets
    FOIL_FLEET for it alone.
    """

    script = scratch / f"{name}.json"
    script.write_text(json.dumps({"steps": steps}), encoding="utf-8")
    transcript = scratch / f"{name}-transcript.json"
    environment = {key: value for key, value in os.environ.items() if key != "FOIL_SEAT_ID"}
    if fleet is not None:
        environment["FOIL_FLEET"] = fleet
    process = subprocess.Popen(
        [sys.executable, str(_OUTSIDE_AGENT), str(script), str(transcript)],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return OutsideAgent(repo, process, transcript)
