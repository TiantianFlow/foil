"""Verification for durable registry behavior (CAP-013, CAP-015–CAP-016, CAP-034)."""

import json
import stat
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from foil.registry import (
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
            session_name="foil-example-12345678",
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


def test_previous_incarnation_id_is_optional_on_read(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    path = store.write_seat(make_record(previous_incarnation_id="incarnation-0"))
    payload = json.loads(path.read_text())
    del payload["previous_incarnation_id"]
    path.write_text(json.dumps(payload))

    record = store.read_seat("fleet-1", "seat-1")
    assert record.previous_incarnation_id is None
    assert record.to_dict()["previous_incarnation_id"] is None
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
        ("native_session_id", "gho_canary_oauth_token"),
        ("native_session_id", "ghu_canary_user_token"),
        ("native_session_id", "ghs_canary_server_token"),
        ("native_session_id", "ghr_canary_refresh_token"),
        ("extensions", {"authorization": "Bearer secret-canary"}),
        ("extensions", {"nested": {"cookie": "secret-canary"}}),
        ("extensions", {"api_key": "custom-secret"}),
        ("extensions", {"apikey": "custom-secret"}),
        ("extensions", {"private-key": "custom-secret"}),
        ("extensions", {"privatekey": "custom-secret"}),
        ("extensions", {"access_token": "custom-secret"}),
        ("extensions", {"auth_token": "custom-secret"}),
    ],
)
def test_secret_shaped_registry_values_are_rejected(tmp_path: Path, field: str, value) -> None:
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


def test_symlinked_seat_directory_is_rejected(tmp_path: Path) -> None:
    store = RegistryStore(tmp_path)
    seat_dir = tmp_path / "v1" / "fleets" / "fleet-1" / "seats"
    seat_dir.parent.mkdir(parents=True)
    target_dir = tmp_path / "outside_dir"
    target_dir.mkdir()
    seat_dir.symlink_to(target_dir)

    with pytest.raises(RegistryError, match="symlink"):
        store.list_seats("fleet-1")


@pytest.mark.parametrize("bad_payload", ["not_a_dict", None, [1, 2, 3]])
def test_seat_record_from_dict_rejects_non_dict(bad_payload) -> None:
    with pytest.raises(RegistryError, match="object"):
        SeatRecord.from_dict(bad_payload)


@pytest.mark.parametrize("bad_version", [True, False, 1.0, "1"])
def test_seat_record_rejects_non_integer_schema_version(bad_version) -> None:
    payload = make_record().to_dict()
    payload["schema_version"] = bad_version
    with pytest.raises(UnsupportedSchemaVersion):
        SeatRecord.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("working_directory", 123),
        ("working_directory", ""),
        ("worktree_path", 123),
        ("worktree_path", ""),
        ("agent_kind", 123),
        ("agent_kind", ""),
        ("agent_kind", True),
        ("git_branch", 123),
        ("git_branch", ""),
        ("git_branch", True),
        ("native_session_id", 123),
        ("tmux", "not_a_tmux_target"),
        ("tmux", {}),
        ("extensions", {123: "non_string_key"}),
    ],
)
def test_seat_record_rejects_invalid_field_types(field: str, bad_value) -> None:
    with pytest.raises(RegistryError):
        make_record(**{field: bad_value})


@pytest.mark.parametrize("bad_payload", ["not_a_dict", None, [1, 2, 3]])
def test_tmux_target_from_dict_rejects_non_dict(bad_payload) -> None:
    with pytest.raises(RegistryError, match="object"):
        TmuxTarget.from_dict(bad_payload)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        (
            {"session_name": 123, "window_name": "w", "session_id": "$1", "window_id": "@1"},
            "session_name",
        ),
        (
            {"session_name": "s", "window_name": 123, "session_id": "$1", "window_id": "@1"},
            "window_name",
        ),
        (
            {"session_name": "s", "window_name": "w", "session_id": 123, "window_id": "@1"},
            "session_id",
        ),
        (
            {"session_name": "s", "window_name": "w", "session_id": "$1", "window_id": 123},
            "window_id",
        ),
    ],
)
def test_tmux_target_rejects_invalid_types(kwargs: dict, match: str) -> None:
    with pytest.raises(RegistryError, match=match):
        TmuxTarget(**kwargs)


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
    pool_id=safe_id_strategy(),
    incarnation_id=safe_id_strategy(),
)
def test_registry_property_round_trip(
    tmp_path_factory,
    fleet_id: str,
    seat_id: str,
    pool_id: str,
    incarnation_id: str,
) -> None:
    tmp_path = tmp_path_factory.mktemp("reg_prop")
    store = RegistryStore(tmp_path)
    rec = make_record(
        fleet_id=fleet_id,
        seat_id=seat_id,
        usage_pool_id=pool_id,
        incarnation_id=incarnation_id,
    )
    store.write_seat(rec)
    read = store.read_seat(fleet_id, seat_id)
    assert read == rec
