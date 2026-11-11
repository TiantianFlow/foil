"""The record a live run leaves in docs/live-runs.md."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil import __version__
from foil.cli import main
from tests.e2e.support import close
from tests.e2e.test_scenarios import _prepare
from tests.harness import live_record, standin

ROOT = Path(__file__).resolve().parents[2]


def test_a_later_run_replaces_the_row_for_its_harness_and_scenario(tmp_path: Path) -> None:
    path = tmp_path / "record.md"
    old = ("codex", "codex 1", "2", "failed", "2026-01-01", "0.1.0")
    other = ("claude", "claude 7", "10", "passed", "2026-01-01", "0.1.0")
    live_record.write(path, [old, other, ("codex", "codex 1", "1", "passed", "2026-01-01", "0.1")])
    new = ("codex", "codex 2", "2", "passed", "2026-02-02", "0.2.0")
    live_record.write(path, [new])
    text = path.read_text(encoding="utf-8")
    # Sorted by harness, then by scenario number: 2 comes before 10.
    assert live_record.rows(text) == [
        other,
        ("codex", "codex 1", "1", "passed", "2026-01-01", "0.1"),
        new,
    ]
    assert text == live_record.render(live_record.rows(text))
    assert text.startswith("# Live runs\n")


def test_a_row_keeps_the_table_whole() -> None:
    assert live_record.cell("\n 1.2.3 | build 4 \nsecond line\n") == "1.2.3 / build 4"
    assert len(live_record.cell("x" * 200)) == 60
    assert live_record.rows("| a | b |\nnot a row\n| 1 | 2 | 3 | 4 | 5 | 6 |\n") == [
        ("1", "2", "3", "4", "5", "6")
    ]


def test_a_scenario_is_named_by_its_number() -> None:
    assert live_record.scenario("test_scenario_3_live") == "3"
    assert live_record.scenario("test_resume_after_reboot_live") == "resume_after_reboot"


def test_a_cli_that_cannot_say_its_version_is_unknown(tmp_path: Path) -> None:
    silent = tmp_path / "silent"
    silent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    silent.chmod(0o755)
    assert live_record.cli_version(str(silent)) == "unknown"
    assert live_record.cli_version(str(tmp_path / "absent")) == "unknown"


def test_the_committed_record_is_the_table_the_tier_writes() -> None:
    text = live_record.RECORD.read_text(encoding="utf-8")
    assert text == live_record.render(live_record.rows(text))


def test_a_failed_scenario_is_recorded_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch, fake=False)
    path = tmp_path / "record.md"
    monkeypatch.setenv("FOIL_E2E_RECORD", str(path))
    live_record.record(repo, "test_scenario_1_live", False)
    ((harness, version, scenario, result, _date, foil),) = live_record.rows(
        path.read_text(encoding="utf-8")
    )
    # No seat was launched, so the harness is the lead's. The suite's stub prints nothing.
    assert harness in standin.NAMES
    assert (version, scenario, result, foil) == ("unknown", "1", "failed", __version__)


@pytest.mark.e2e
def test_the_row_names_the_harness_the_seats_were_launched_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    path = tmp_path / "record.md"
    monkeypatch.setenv("FOIL_E2E_RECORD", str(path))
    try:
        assert main(["seat", "spawn", "lead"]) == 0
        # The mapping changes after the launch. The seat still runs what it was started with.
        assert main(["roster", "update", "lead", "harness=codex"]) == 0
        live_record.record(repo, "test_scenario_2_live", True)
    finally:
        close(repo)
    assert [row[:4] for row in live_record.rows(path.read_text(encoding="utf-8"))] == [
        ("fake", "unknown", "2", "passed")
    ]


@pytest.mark.e2e
def test_a_live_run_writes_one_row_per_scenario(tmp_path: Path) -> None:
    """Run two live scenarios for real, against a stand-in for the CLI."""

    bindir = tmp_path / "bin"
    bindir.mkdir()
    standin.install(bindir)
    path = tmp_path / "record.md"
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("PYTEST_", "FOIL_"))
    }
    environment.update(
        PATH=os.pathsep.join((str(bindir), os.environ.get("PATH", ""))),
        FOIL_E2E_LIVE="1",
        FOIL_E2E_HARNESS="codex",
        FOIL_E2E_RECORD=str(path),
        FOIL_HOME=str(tmp_path / "home"),
    )
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/e2e/test_live.py",
            "-k",
            "scenario_2 or scenario_6",
            "-n",
            "0",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={tmp_path / 'inner'}",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    version = f"codex {standin.VERSION}"
    # Scenario 2 launched seats and scenario 6 launched none. Both name the harness.
    written = path.read_text(encoding="utf-8") if path.is_file() else ""
    assert live_record.rows(written) == [
        ("codex", version, "2", "passed", today, __version__),
        ("codex", version, "6", "passed", today, __version__),
    ]
