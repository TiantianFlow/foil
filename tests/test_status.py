"""Verification for structured status reading (CAP-012–CAP-016, CAP-030, CAP-032)."""

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
    terminal_log = tmp_path / "v1" / "fleets" / "fleet-1" / "status" / "seats" / "terminal.log"
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


def test_symlinked_status_directory_is_rejected(tmp_path: Path) -> None:
    reader = PollStatusReader(tmp_path)
    status_dir = tmp_path / "v1" / "fleets" / "fleet-1" / "status" / "seats"
    status_dir.parent.mkdir(parents=True)
    target_dir = tmp_path / "outside_status_dir"
    target_dir.mkdir()
    status_dir.symlink_to(target_dir)

    with pytest.raises(StatusError, match="symlink"):
        reader.read_fleet("fleet-1")


@pytest.mark.parametrize("bad_payload", ["not_a_dict", None, [1, 2, 3]])
def test_status_snapshot_from_dict_rejects_non_dict(bad_payload) -> None:
    with pytest.raises(StatusError, match="object"):
        StatusSnapshot.from_dict(bad_payload)


@pytest.mark.parametrize("bad_version", [True, False, 1.0, "1"])
def test_status_snapshot_rejects_non_integer_schema_version(bad_version) -> None:
    payload = make_status().to_dict()
    payload["schema_version"] = bad_version
    with pytest.raises(UnsupportedStatusSchemaVersion):
        StatusSnapshot.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("fleet_id", 123),
        ("seat_id", 123),
        ("updated_at", 123),
        ("state", "idle"),  # string instead of SeatState enum
        ("evidence", "not_a_dict"),
        ("evidence", {123: "non_string_key"}),
        ("extensions", "not_a_dict"),
        ("extensions", {123: "non_string_key"}),
    ],
)
def test_status_snapshot_rejects_invalid_field_types(field: str, bad_value) -> None:
    kwargs = {
        "fleet_id": "fleet-1",
        "seat_id": "seat-1",
        "state": SeatState.IDLE,
        "updated_at": "2026-08-23T14:00:00Z",
        "evidence": {"kind": "test"},
        "extensions": {},
    }
    kwargs[field] = bad_value
    with pytest.raises(StatusError):
        StatusSnapshot(**kwargs)


_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_SAFE_ID_FOLLOW_CHARS = _SAFE_ID_CHARS + "._-"


@st.composite
def safe_id_strategy(draw) -> str:
    first = draw(st.sampled_from(_SAFE_ID_CHARS))
    rest = draw(st.text(alphabet=_SAFE_ID_FOLLOW_CHARS, min_size=0, max_size=30))
    return first + rest


@given(
    fleet_id=safe_id_strategy(),
    seat_id=safe_id_strategy(),
    state=st.sampled_from(list(SeatState)),
)
def test_status_property_round_trip(
    tmp_path_factory,
    fleet_id: str,
    seat_id: str,
    state: SeatState,
) -> None:
    tmp_path = tmp_path_factory.mktemp("status_prop")
    reader = PollStatusReader(tmp_path)
    snapshot = StatusSnapshot(
        fleet_id=fleet_id,
        seat_id=seat_id,
        state=state,
        updated_at="2026-08-23T14:00:00Z",
        evidence={"kind": "property_test"},
        extensions={"note": "ok"},
    )
    reader.write_fixture(snapshot)
    results = reader.read_fleet(fleet_id)
    assert results == [snapshot]
