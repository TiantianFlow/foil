import json
from pathlib import Path

import pytest

from capstan.status import (
    PollStatusReader,
    SeatState,
    StatusError,
    StatusSnapshot,
    UnsupportedStatusSchemaVersion,
)


def make_status(seat_id: str = "seat-1", state: SeatState = SeatState.IDLE):
    return StatusSnapshot(
        fleet_id="fleet-1",
        seat_id=seat_id,
        state=state,
        updated_at="2026-08-23T14:00:00Z",
        evidence={"kind": "lifecycle_event", "event_id": "event-1"},
        extensions={},
    )


def test_poll_reader_reconstructs_sorted_status_from_files(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    reader.write_fixture(make_status("seat-b", SeatState.WORKING))
    reader.write_fixture(make_status("seat-a", SeatState.WAITING))

    statuses = reader.read_fleet("fleet-1")

    assert [status.seat_id for status in statuses] == ["seat-a", "seat-b"]
    assert [status.state for status in statuses] == [
        SeatState.WAITING,
        SeatState.WORKING,
    ]


def test_arbitrary_terminal_text_cannot_change_status(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    reader.write_fixture(make_status(state=SeatState.IDLE))
    terminal_log = (
        tmp_path / "v1" / "fleets" / "fleet-1" / "status" / "seats" / "terminal.log"
    )
    terminal_log.write_text("ERROR PROCESSING WAITING COMPLETED")

    assert reader.read_fleet("fleet-1") == [make_status(state=SeatState.IDLE)]


def test_unknown_state_is_rejected(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    path = reader.write_fixture(make_status())
    payload = json.loads(path.read_text())
    payload["state"] = "guessing"
    path.write_text(json.dumps(payload))

    with pytest.raises(StatusError, match="state"):
        reader.read_fleet("fleet-1")


def test_unsupported_status_schema_is_rejected(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    path = reader.write_fixture(make_status())
    payload = json.loads(path.read_text())
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload))

    with pytest.raises(UnsupportedStatusSchemaVersion, match="99"):
        reader.read_fleet("fleet-1")


def test_symlinked_status_is_rejected(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    status_dir = tmp_path / "v1" / "fleets" / "fleet-1" / "status" / "seats"
    status_dir.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}")
    (status_dir / "seat-1.json").symlink_to(target)

    with pytest.raises(StatusError, match="symlink"):
        reader.read_fleet("fleet-1")
