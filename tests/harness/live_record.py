"""The record a live run leaves: which CLI version last ran which scenario, and how it ended.

The record is one Markdown table with one row per harness and scenario.
A later run of the same pair replaces that row, so the table stays the
latest result for each pair and does not grow with every run.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

RECORD = Path(__file__).resolve().parents[2] / "docs" / "live-runs.md"
COLUMNS = ("Harness", "CLI version", "Scenario", "Result", "Date", "Foil")
_RULE = "|" + "---|" * len(COLUMNS)
_HEAD = "| " + " | ".join(COLUMNS) + " |"
_INTRO = (
    "# Live runs\n"
    "\n"
    "The live tier writes this table: the latest result of each scenario for each\n"
    "harness CLI it ran against, with that CLI's version. CONTRIBUTING.md says\n"
    "how to run the tier. A row is a run on one machine, not a promise about\n"
    "another version of that CLI.\n"
    "\n"
)


def record_path() -> Path:
    """Where this run writes. `FOIL_E2E_RECORD` names another file."""

    chosen = os.environ.get("FOIL_E2E_RECORD", "").strip()
    return Path(chosen) if chosen else RECORD


def cell(text: str) -> str:
    """One line of plain text that cannot end or split a table cell."""

    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    return line.replace("|", "/")[:60]


def cli_version(command: str) -> str:
    """The first line `COMMAND --version` prints, or `unknown`."""

    try:
        ran = subprocess.run(
            [command, "--version"], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return cell(ran.stdout) or "unknown"


def scenario(test: str) -> str:
    """`test_scenario_3_live` is scenario `3`. Any other test keeps its name."""

    name = test.removeprefix("test_").removesuffix("_live")
    number = name.removeprefix("scenario_")
    return number if number.isdigit() else name


def rows(text: str) -> list[tuple[str, ...]]:
    """The table's rows. Anything that is not a full row is dropped."""

    found = []
    for line in text.splitlines():
        if not line.startswith("| ") or line == _HEAD:
            continue
        cells = tuple(part.strip() for part in line.strip().strip("|").split("|"))
        if len(cells) == len(COLUMNS):
            found.append(cells)
    return found


def _order(row: tuple[str, ...]) -> tuple[str, int, str]:
    return row[0], int(row[2]) if row[2].isdigit() else 10**6, row[2]


def merged(old: list[tuple[str, ...]], new: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Old rows with each new row in place of the one for its harness and scenario."""

    latest = {(row[0], row[2]): row for row in (*old, *new)}
    return sorted(latest.values(), key=_order)


def render(table: list[tuple[str, ...]]) -> str:
    lines = [_HEAD, _RULE, *("| " + " | ".join(row) + " |" for row in table)]
    return _INTRO + "\n".join(lines) + "\n"


def write(path: Path, new: list[tuple[str, ...]]) -> None:
    """Put the rows into the record. Two runs that write at once both land."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        table = merged(rows(handle.read()), new)
        handle.seek(0)
        handle.truncate()
        handle.write(render(table))


def harnesses(repo: Path) -> dict[str, str]:
    """Each harness this scenario used, with the command that starts it.

    They are the ones the registry recorded for the seats that were
    launched. A scenario that launched no seat used the lead's mapping.
    """

    from foil.presets import load_preset, read_maps
    from foil.project import foil_root
    from foil.store import load_registry

    used: list[str] = []
    fleets = foil_root(repo) / "fleets"
    for fleet in sorted(path.name for path in fleets.iterdir()) if fleets.is_dir() else []:
        seats = load_registry(repo, fleet)["seats"]
        used.extend(str(seat.get("harness") or "") for seat in seats.values())
    if not any(used):
        used = [item["id"] for item in read_maps(repo) if item["role"] == "lead"][:1]
    return {name: load_preset(repo, name)["start"][0] for name in sorted(set(used)) if name}


def record(repo: Path, test: str, passed: bool) -> None:
    """Write one row per harness for a live test that has just ended."""

    from foil import __version__

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    result = "passed" if passed else "failed"
    new = [
        (cell(name), cli_version(command), scenario(test), result, today, __version__)
        for name, command in harnesses(repo).items()
    ]
    if new:
        write(record_path(), new)
