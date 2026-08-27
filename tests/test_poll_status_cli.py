"""Contract tests for the poll-status CLI (CAP-001, CAP-012–CAP-014, CAP-027)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from foil.status import PollStatusReader, SeatState, StatusSnapshot


def make_status(seat_id: str = "seat-1", state: SeatState = SeatState.IDLE) -> StatusSnapshot:
    return StatusSnapshot(
        fleet_id="fleet-1",
        seat_id=seat_id,
        state=state,
        updated_at="2026-08-23T14:00:00Z",
        evidence={"kind": "lifecycle_event", "event_id": "event-1"},
        extensions={},
    )


def run_foil(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "foil", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_foil_help_lists_poll_status() -> None:
    result = run_foil("--help")

    assert result.returncode == 0
    assert "Foil · 运筹" in result.stdout
    assert "poll-status" in result.stdout
    assert "FOIL_STATE_DIR" not in result.stdout


def test_poll_status_help_documents_required_flags() -> None:
    result = run_foil("poll-status", "--help")

    assert result.returncode == 0
    lowered = result.stdout.lower()
    assert "--state-dir" in lowered
    assert "--fleet" in lowered
    assert "json" in lowered
    assert "structured" in lowered or "status file" in lowered


def test_poll_status_emits_deterministic_json_for_valid_state(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    reader.write_fixture(make_status("seat-b", SeatState.WORKING))
    reader.write_fixture(make_status("seat-a", SeatState.WAITING))

    result = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload == {
        "fleet_id": "fleet-1",
        "seats": [
            make_status("seat-a", SeatState.WAITING).to_dict(),
            make_status("seat-b", SeatState.WORKING).to_dict(),
        ],
    }
    assert result.stdout == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def test_poll_status_empty_fleet_emits_empty_seats_array(tmp_path: Path) -> None:
    result = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"fleet_id": "fleet-1", "seats": []}


def test_poll_status_rejects_invalid_state_with_stderr(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    path = reader.write_fixture(make_status())
    payload = json.loads(path.read_text())
    payload["state"] = "guessing"
    path.write_text(json.dumps(payload))

    result = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip()
    assert "state" in result.stderr.lower()


def test_poll_status_diagnostics_do_not_reflect_unknown_input(tmp_path: Path) -> None:
    state_canary = "state-canary-" + "x" * 4096
    field_canary = "field-canary-" + "y" * 4096
    reader = PollStatusReader(tmp_path)
    path = reader.write_fixture(make_status())
    payload = json.loads(path.read_text())
    payload["state"] = state_canary
    path.write_text(json.dumps(payload))

    invalid_state = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert invalid_state.returncode != 0
    assert state_canary not in invalid_state.stderr
    assert len(invalid_state.stderr) < 200

    payload = make_status().to_dict()
    payload[field_canary] = True
    path.write_text(json.dumps(payload))

    invalid_field = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert invalid_field.returncode != 0
    assert field_canary not in invalid_field.stderr
    assert len(invalid_field.stderr) < 200


def test_poll_status_ignores_terminal_log_files(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    reader.write_fixture(make_status(state=SeatState.IDLE))
    terminal_log = tmp_path / "v1" / "fleets" / "fleet-1" / "status" / "seats" / "terminal.log"
    terminal_log.write_text("ERROR PROCESSING WAITING COMPLETED")

    result = run_foil("poll-status", "--state-dir", str(tmp_path), "--fleet", "fleet-1")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["seats"] == [make_status(state=SeatState.IDLE).to_dict()]


def test_poll_status_fail_closed_without_a_live_fleet(tmp_path: Path) -> None:
    result = run_foil("poll-status", "--state-dir", str(tmp_path))

    assert result.returncode != 0
    assert "fleet" in result.stderr.lower()
