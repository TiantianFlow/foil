"""Live fleet membership persisted only for resume (not as a replay recipe)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.registry import (
    SCHEMA_VERSION,
    RegistryError,
    _atomic_write_json,
    _ensure_private_directory,
    _exclusive_lock,
    _read_json_file,
    _validate_id,
    _validate_timestamp,
)

LEAD_CAPABILITIES = (
    "seat.spawn",
    "seat.stop",
    "seat.remove",
    "seat.list",
    "seat.inspect",
    "mailbox",
    "notepad",
    "memory",
)
WORKER_CAPABILITIES = (
    "mailbox",
    "notepad",
    "memory.propose",
)
OPERATOR_ACTOR = "operator"


class FleetError(RegistryError):
    """Fleet membership or authorization is invalid."""


def resolve_caller(
    actor_flag: str | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the calling identity.

    When ``FOIL_SEAT_ID`` is set, that seat is the caller. An in-seat process
    cannot override that identity with ``--actor``. This is cooperative
    same-user protection, not hostile-process isolation.
    """

    environment = os.environ if environ is None else environ
    seat = str(environment.get("FOIL_SEAT_ID") or "").strip()
    flag = str(actor_flag or "").strip()
    if seat:
        _validate_id(seat, "FOIL_SEAT_ID")
        if flag and flag != seat:
            raise FleetError(
                f"in-seat caller {seat} cannot override identity with --actor {flag}"
            )
        return seat
    if flag in {"", OPERATOR_ACTOR}:
        return OPERATOR_ACTOR
    _validate_id(flag, "actor")
    return flag


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class FleetRecord:
    fleet_id: str
    project_root: str
    updated_at: str
    display_name: str = "Foil fleet"
    lead_seat_id: str | None = None
    state: str = "initialized"
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _validate_id(self.fleet_id, "fleet_id")
        if self.lead_seat_id is not None:
            _validate_id(self.lead_seat_id, "lead_seat_id")
        if not isinstance(self.project_root, str) or not Path(self.project_root).is_absolute():
            raise FleetError("project_root must be an absolute path")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise FleetError("display_name must be a non-empty string")
        if not isinstance(self.state, str) or not self.state:
            raise FleetError("state must be a non-empty string")
        if not isinstance(self.extensions, dict):
            raise FleetError("extensions must be an object")
        _validate_timestamp(self.updated_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "display_name": self.display_name,
            "project_root": self.project_root,
            "lead_seat_id": self.lead_seat_id,
            "state": self.state,
            "updated_at": self.updated_at,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> FleetRecord:
        if not isinstance(payload, dict):
            raise FleetError("fleet record must be an object")
        version = payload.get("schema_version")
        if type(version) is not int or version != SCHEMA_VERSION:
            raise FleetError(f"unsupported fleet schema version: {version}")
        return cls(
            fleet_id=payload["fleet_id"],
            project_root=payload["project_root"],
            updated_at=payload.get("updated_at") or _now(),
            display_name=payload.get("display_name") or "Foil fleet",
            lead_seat_id=payload.get("lead_seat_id"),
            state=payload.get("state") or "initialized",
            extensions=payload.get("extensions") or {},
        )


class FleetStore:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def path(self, fleet_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "fleet.json"
        )

    def lock_path(self, fleet_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "locks"
            / "fleet.lock"
        )

    def read(self, fleet_id: str) -> FleetRecord:
        return FleetRecord.from_dict(
            _read_json_file(self.path(fleet_id), error_type=FleetError)
        )

    def write(self, record: FleetRecord) -> Path:
        with _exclusive_lock(self.lock_path(record.fleet_id)):
            return self._write_unlocked(record)

    def _write_unlocked(self, record: FleetRecord) -> Path:
        path = self.path(record.fleet_id)
        _ensure_private_directory(path.parent)
        return _atomic_write_json(path, record.to_dict())

    @contextmanager
    def operation_lock(self, fleet_id: str) -> Iterator[None]:
        """Hold the fleet lock across one membership mutation."""

        with _exclusive_lock(self.lock_path(fleet_id)):
            yield

    def require_lifecycle_actor(
        self,
        fleet_id: str,
        actor: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        caller = resolve_caller(actor, environ=environ)
        if caller == OPERATOR_ACTOR:
            return caller
        fleet = self.read(fleet_id)
        if fleet.lead_seat_id != caller:
            raise FleetError(f"seat {caller} is not authorized for fleet lifecycle")
        return caller

    def require_reviewer(
        self,
        fleet_id: str,
        actor: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        environment = os.environ if environ is None else environ
        if str(environment.get("FOIL_SEAT_ID") or "").strip():
            caller = resolve_caller(actor, environ=environment)
            fleet = self.read(fleet_id)
            if fleet.lead_seat_id != caller:
                raise FleetError(f"seat {caller} is not authorized to review memory")
            return caller
        if actor in {None, "", OPERATOR_ACTOR}:
            return OPERATOR_ACTOR
        _validate_id(actor, "actor")
        return actor

    def require_lead_actor(
        self,
        fleet_id: str,
        actor: str | None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        return self.require_lifecycle_actor(fleet_id, actor, environ=environ)
