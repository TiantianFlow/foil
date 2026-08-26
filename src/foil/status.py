"""Structured status snapshots and polling (CAP-012–CAP-016, CAP-030)."""

from __future__ import annotations

import stat
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from foil.registry import (
    SCHEMA_VERSION,
    _assert_no_secret,
    _atomic_write_json,
    _read_json_file,
    _validate_id,
    _validate_timestamp,
)


class StatusError(ValueError):
    """Status input or storage is unsafe or malformed."""


class UnsupportedStatusSchemaVersion(StatusError):
    """The status schema cannot be interpreted by this version."""


class SeatState(StrEnum):
    LAUNCHING = "launching"
    WORKING = "working"
    WAITING = "waiting"
    IDLE = "idle"
    EXITED = "exited"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    fleet_id: str
    seat_id: str
    state: SeatState
    updated_at: str
    evidence: dict[str, Any]
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        try:
            _validate_id(self.fleet_id, "fleet_id")
            _validate_id(self.seat_id, "seat_id")
            _validate_timestamp(self.updated_at)
            if not isinstance(self.evidence, dict):
                raise StatusError("evidence must be an object")
            if not isinstance(self.extensions, dict):
                raise StatusError("extensions must be an object")
            _assert_no_secret(self.evidence, path="evidence")
            _assert_no_secret(self.extensions, path="extensions")
        except ValueError as exc:
            raise StatusError(str(exc)) from exc
        if not isinstance(self.state, SeatState):
            raise StatusError("state must be a SeatState")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "seat_id": self.seat_id,
            "state": self.state.value,
            "updated_at": self.updated_at,
            "evidence": self.evidence,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> StatusSnapshot:
        if not isinstance(payload, dict):
            raise StatusError("status record must be an object")
        expected = {
            "schema_version",
            "fleet_id",
            "seat_id",
            "state",
            "updated_at",
            "evidence",
            "extensions",
        }
        unknown = set(payload) - expected
        missing = expected - set(payload)
        if unknown:
            raise StatusError("status record has unknown fields")
        if missing:
            raise StatusError(f"missing status fields: {sorted(missing)}")
        version = payload["schema_version"]
        if type(version) is not int or version != SCHEMA_VERSION:
            raise UnsupportedStatusSchemaVersion(f"unsupported status schema version: {version}")
        try:
            state = SeatState(payload["state"])
        except (TypeError, ValueError) as exc:
            raise StatusError("state is not a supported value") from exc
        return cls(
            fleet_id=payload["fleet_id"],
            seat_id=payload["seat_id"],
            state=state,
            updated_at=payload["updated_at"],
            evidence=payload["evidence"],
            extensions=payload["extensions"],
        )


class PollStatusReader:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def status_dir(self, fleet_id: str) -> Path:
        try:
            _validate_id(fleet_id, "fleet_id")
        except ValueError as exc:
            raise StatusError(str(exc)) from exc
        return self.state_root / f"v{SCHEMA_VERSION}" / "fleets" / fleet_id / "status" / "seats"

    def status_path(self, fleet_id: str, seat_id: str) -> Path:
        try:
            _validate_id(seat_id, "seat_id")
        except ValueError as exc:
            raise StatusError(str(exc)) from exc
        return self.status_dir(fleet_id) / f"{seat_id}.json"

    def write_fixture(self, snapshot: StatusSnapshot) -> Path:
        """Write a snapshot for contract fixtures until the projector lands."""

        return _atomic_write_json(
            self.status_path(snapshot.fleet_id, snapshot.seat_id),
            snapshot.to_dict(),
        )

    def delete_snapshot(self, fleet_id: str, seat_id: str) -> None:
        path = self.status_path(fleet_id, seat_id)
        with suppress(FileNotFoundError):
            path.unlink()

    def read_fleet(self, fleet_id: str) -> list[StatusSnapshot]:
        status_dir = self.status_dir(fleet_id)
        try:
            dir_stat = status_dir.lstat()
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(dir_stat.st_mode):
            raise StatusError(f"refusing symlinked status directory: {status_dir}")
        if not stat.S_ISDIR(dir_stat.st_mode):
            raise StatusError(f"status directory is not a directory: {status_dir}")
        snapshots: list[StatusSnapshot] = []
        for path in sorted(status_dir.glob("*.json"), key=lambda item: item.name):
            payload = _read_json_file(path, error_type=StatusError)
            snapshot = StatusSnapshot.from_dict(payload)
            if snapshot.fleet_id != fleet_id or path.stem != snapshot.seat_id:
                raise StatusError("status identity does not match its path")
            snapshots.append(snapshot)
        return snapshots
