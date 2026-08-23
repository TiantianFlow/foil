import json
import stat
from pathlib import Path

import pytest

from capstan.registry import (
    RegistryError,
    RegistryStore,
    SeatRecord,
    TmuxTarget,
    UnsupportedSchemaVersion,
)


def make_record(**changes) -> SeatRecord:
    values = {
        "fleet_id": "fleet-1",
        "seat_id": "seat-1",
        "working_directory": "/tmp/project",
        "agent_kind": "fixture-cli",
        "native_session_id": "native-session-1",
        "tmux": TmuxTarget(
            session_name="capstan-example-12345678",
            window_name="builder-12345678",
            session_id="$1",
            window_id="@1",
        ),
        "git_branch": "feat/example",
        "worktree_path": "/tmp/project",
        "usage_pool_id": "pool-1",
        "incarnation_id": "incarnation-1",
        "updated_at": "2026-08-23T14:00:00Z",
        "extensions": {"future": {"port": 4317, "url": "http://127.0.0.1:4317"}},
    }
    values.update(changes)
    return SeatRecord(**values)


def test_registry_round_trip_preserves_required_and_extension_fields(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    expected = make_record()

    store.write_seat(expected)

    assert store.read_seat("fleet-1", "seat-1") == expected
    assert store.list_seats("fleet-1") == [expected]


def test_registry_write_is_private_and_leaves_no_partial_file(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)

    path = store.write_seat(make_record())

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(path.parent.glob("*.tmp")) == []


def test_unknown_top_level_fields_are_rejected(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    path = store.write_seat(make_record())
    payload = json.loads(path.read_text())
    payload["surprise"] = True
    path.write_text(json.dumps(payload))

    with pytest.raises(RegistryError, match="unknown fields"):
        store.read_seat("fleet-1", "seat-1")


def test_unsupported_registry_schema_is_rejected(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    path = store.write_seat(make_record())
    payload = json.loads(path.read_text())
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload))

    with pytest.raises(UnsupportedSchemaVersion, match="99"):
        store.read_seat("fleet-1", "seat-1")


@pytest.mark.parametrize("unsafe_id", ["../seat", "seat/name", "", "."])
def test_unsafe_registry_ids_are_rejected(tmp_path: Path, unsafe_id: str) -> None:
    store = RegistryStore(tmp_path)

    with pytest.raises(RegistryError, match="seat_id"):
        store.write_seat(make_record(seat_id=unsafe_id))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("native_session_id", "sk-secret-canary"),
        ("extensions", {"authorization": "Bearer secret-canary"}),
        ("extensions", {"nested": {"cookie": "secret-canary"}}),
    ],
)
def test_secret_shaped_registry_values_are_rejected(
    tmp_path: Path, field: str, value
) -> None:
    store = RegistryStore(tmp_path)

    with pytest.raises(RegistryError, match="secret"):
        store.write_seat(make_record(**{field: value}))


def test_symlinked_seat_record_is_rejected(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    path = store.seat_path("fleet-1", "seat-1")
    path.parent.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}")
    path.symlink_to(target)

    with pytest.raises(RegistryError, match="symlink"):
        store.read_seat("fleet-1", "seat-1")
