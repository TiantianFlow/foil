"""Predefined seat staffing recipes (.foil/seats.toml).

This file is a spawn recipe, not live membership. The registry still owns
who is running. A seat id looks up CLI, model, profile, role, role-file,
and workdir so `foil seat spawn --seat spec` can run without ad hoc flags.
Flags remain overrides. Personas stay untouched Markdown; this file only
points at them.
"""

from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from foil.registry import RegistryError, _assert_no_secret, _validate_id

SEATS_SCHEMA_VERSION = 1
SEATS_RELATIVE_PATH = Path(".foil") / "seats.toml"
_TRUE_FALSE = frozenset({"true", "false"})


class SeatStaffingError(ValueError):
    """A predefined seat recipe is missing, malformed, or incomplete."""


@dataclass(frozen=True, slots=True)
class SeatStaffing:
    seat_id: str
    lead: bool = False
    cli: str | None = None
    profile: str | None = None
    model: str | None = None
    role: str | None = None
    role_file: str | None = None
    cwd: str | None = None
    isolated: bool | None = None
    shared_cwd: bool = False
    permission: str | None = None
    display_name: str | None = None

    def has_launch_source(self) -> bool:
        return bool(self.cli or self.profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "lead": self.lead,
            "cli": self.cli,
            "profile": self.profile,
            "model": self.model,
            "role": self.role,
            "role_file": self.role_file,
            "cwd": self.cwd,
            "isolated": self.isolated,
            "shared_cwd": self.shared_cwd,
            "permission": self.permission,
            "display_name": self.display_name,
        }


def seats_path(project_root: Path | str) -> Path:
    return Path(project_root).expanduser().resolve() / SEATS_RELATIVE_PATH


def empty_roster_text() -> str:
    return "schema_version = 1\n"


def load_roster(project_root: Path | str) -> dict[str, SeatStaffing]:
    path = seats_path(project_root)
    if not path.exists():
        return {}
    return load_roster_file(path)


def load_roster_file(path: Path | str) -> dict[str, SeatStaffing]:
    roster_path = Path(path)
    if roster_path.is_symlink() or not roster_path.is_file():
        raise SeatStaffingError(f"seat config must be a regular non-symlink file: {roster_path}")
    try:
        text = roster_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SeatStaffingError(f"cannot read seat config: {roster_path}") from exc
    try:
        payload = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SeatStaffingError(f"seat config is not valid TOML: {roster_path}") from exc
    if not isinstance(payload, dict):
        raise SeatStaffingError("seat config must be a table")
    if payload.get("schema_version") != SEATS_SCHEMA_VERSION:
        raise SeatStaffingError("seat config schema_version must be 1")
    unknown = set(payload) - {"schema_version", "seats"}
    if unknown:
        raise SeatStaffingError(
            "seat config has unknown fields: " + ", ".join(sorted(unknown))
        )
    raw_seats = payload.get("seats", {})
    if raw_seats is None:
        raw_seats = {}
    if not isinstance(raw_seats, dict):
        raise SeatStaffingError("seats must be a table of seat ids")
    seats: dict[str, SeatStaffing] = {}
    for seat_id, raw in raw_seats.items():
        staffing = _parse_seat(seat_id, raw)
        seats[staffing.seat_id] = staffing
    try:
        _assert_no_secret({"seats": {key: value.to_dict() for key, value in seats.items()}})
    except RegistryError as exc:
        raise SeatStaffingError(f"unsafe seat config rejected: {exc}") from exc
    return seats


def lookup_seat(project_root: Path | str, seat_id: str) -> SeatStaffing | None:
    roster = load_roster(project_root)
    return roster.get(seat_id)


def upsert_seat(project_root: Path | str, staffing: SeatStaffing) -> Path:
    _validate_staffing(staffing)
    path = seats_path(project_root)
    roster = load_roster(project_root) if path.exists() else {}
    roster[staffing.seat_id] = staffing
    write_roster(project_root, roster)
    return path


def write_roster(project_root: Path | str, roster: dict[str, SeatStaffing]) -> Path:
    project = Path(project_root).expanduser().resolve()
    path = seats_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    for staffing in roster.values():
        _validate_staffing(staffing)
    try:
        _assert_no_secret({"seats": {key: value.to_dict() for key, value in roster.items()}})
    except RegistryError as exc:
        raise SeatStaffingError(f"unsafe seat config rejected: {exc}") from exc
    text = dump_roster(roster)
    fd, tmp_name = tempfile.mkstemp(prefix="seats.", suffix=".toml", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
        path.chmod(0o644)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return path


def dump_roster(roster: dict[str, SeatStaffing]) -> str:
    lines = ["schema_version = 1", ""]
    for seat_id in sorted(roster):
        staffing = roster[seat_id]
        lines.append(f"[seats.{_toml_key(staffing.seat_id)}]")
        if staffing.lead:
            lines.append("lead = true")
        _emit_optional(lines, "cli", staffing.cli)
        _emit_optional(lines, "profile", staffing.profile)
        _emit_optional(lines, "model", staffing.model)
        _emit_optional(lines, "role", staffing.role)
        _emit_optional(lines, "role_file", staffing.role_file)
        _emit_optional(lines, "cwd", staffing.cwd)
        if staffing.isolated is not None:
            lines.append(f"isolated = {'true' if staffing.isolated else 'false'}")
        if staffing.shared_cwd:
            lines.append("shared_cwd = true")
        _emit_optional(lines, "permission", staffing.permission)
        _emit_optional(lines, "display_name", staffing.display_name)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def apply_overrides(
    base: SeatStaffing | None,
    *,
    seat_id: str,
    lead: bool = False,
    cli: str | None = None,
    profile: str | None = None,
    model: str | None = None,
    role: str | None = None,
    role_file: str | None = None,
    cwd: str | None = None,
    isolated: bool | None = None,
    shared_cwd: bool = False,
    permission: str | None = None,
    display_name: str | None = None,
) -> SeatStaffing:
    """Merge ad hoc spawn flags over a predefined seat recipe.

    Each provided flag wins. Passing ``--role`` clears a configured
    ``role_file`` unless ``--role-file`` is also passed, and the reverse.
    """

    if base is None:
        merged = SeatStaffing(seat_id=seat_id)
    else:
        merged = base
    lead_value = lead or merged.lead
    role_value = merged.role
    role_file_value = merged.role_file
    if role is not None:
        role_value = role
        if role_file is None:
            role_file_value = None
    if role_file is not None:
        role_file_value = role_file
        if role is None:
            role_value = None
    return replace(
        merged,
        seat_id=seat_id,
        lead=lead_value,
        cli=cli if cli is not None else merged.cli,
        profile=profile if profile is not None else merged.profile,
        model=model if model is not None else merged.model,
        role=role_value,
        role_file=role_file_value,
        cwd=str(cwd) if cwd is not None else merged.cwd,
        isolated=isolated if isolated is not None else merged.isolated,
        shared_cwd=shared_cwd or merged.shared_cwd,
        permission=permission if permission is not None else merged.permission,
        display_name=display_name if display_name is not None else merged.display_name,
    )


def resolve_spawn_staffing(
    project_root: Path | str,
    seat_id: str,
    **overrides: Any,
) -> SeatStaffing:
    path = seats_path(project_root)
    roster = load_roster(project_root)
    base = roster.get(seat_id)
    merged = apply_overrides(base, seat_id=seat_id, **overrides)
    if merged.has_launch_source():
        _validate_staffing(merged)
        return merged
    if not path.exists():
        raise SeatStaffingError(
            f"seat {seat_id} has no predefined config and no --cli/--profile override; "
            "write .foil/seats.toml (foil seats set) or pass --cli or --profile"
        )
    if base is None:
        raise SeatStaffingError(
            f"no predefined seat config for {seat_id} in {path}; "
            "add the seat with foil seats set or pass --cli or --profile"
        )
    raise SeatStaffingError(
        f"seat {seat_id} config has neither cli nor profile; "
        "set one in .foil/seats.toml or pass --cli or --profile"
    )


def _parse_seat(seat_id: str, raw: Any) -> SeatStaffing:
    if not isinstance(raw, dict):
        raise SeatStaffingError(f"seats.{seat_id} must be a table")
    try:
        _validate_id(seat_id, "seat_id")
    except RegistryError as exc:
        raise SeatStaffingError(str(exc)) from exc
    unknown = set(raw) - {
        "lead",
        "cli",
        "profile",
        "model",
        "role",
        "role_file",
        "cwd",
        "isolated",
        "shared_cwd",
        "permission",
        "display_name",
    }
    if unknown:
        raise SeatStaffingError(
            f"seats.{seat_id} has unknown fields: " + ", ".join(sorted(unknown))
        )
    staffing = SeatStaffing(
        seat_id=seat_id,
        lead=_bool(raw.get("lead"), f"seats.{seat_id}.lead", default=False),
        cli=_optional_string(raw.get("cli"), f"seats.{seat_id}.cli"),
        profile=_optional_string(raw.get("profile"), f"seats.{seat_id}.profile"),
        model=_optional_string(raw.get("model"), f"seats.{seat_id}.model"),
        role=_optional_string(raw.get("role"), f"seats.{seat_id}.role"),
        role_file=_optional_string(raw.get("role_file"), f"seats.{seat_id}.role_file"),
        cwd=_optional_string(raw.get("cwd"), f"seats.{seat_id}.cwd"),
        isolated=_optional_bool(raw.get("isolated"), f"seats.{seat_id}.isolated"),
        shared_cwd=_bool(raw.get("shared_cwd"), f"seats.{seat_id}.shared_cwd", default=False),
        permission=_optional_string(raw.get("permission"), f"seats.{seat_id}.permission"),
        display_name=_optional_string(
            raw.get("display_name"), f"seats.{seat_id}.display_name"
        ),
    )
    _validate_staffing(staffing)
    return staffing


def _validate_staffing(staffing: SeatStaffing) -> None:
    try:
        _validate_id(staffing.seat_id, "seat_id")
    except RegistryError as exc:
        raise SeatStaffingError(str(exc)) from exc
    if staffing.role and staffing.role_file:
        raise SeatStaffingError(
            f"seat {staffing.seat_id} cannot set both role and role_file"
        )
    if staffing.permission is not None and staffing.permission not in {
        "supervised",
        "auto",
    }:
        raise SeatStaffingError(
            f"seat {staffing.seat_id} permission must be supervised or auto"
        )
    if staffing.shared_cwd and staffing.isolated is True:
        raise SeatStaffingError(
            f"seat {staffing.seat_id} cannot set shared_cwd with isolated = true"
        )


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SeatStaffingError(f"{field} must be a non-empty string")
    return value


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    return _bool(value, field, default=False)


def _bool(value: Any, field: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise SeatStaffingError(f"{field} must be a boolean")


def _toml_key(seat_id: str) -> str:
    return '"' + seat_id.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _emit_optional(lines: list[str], key: str, value: str | None) -> None:
    if value is None:
        return
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    lines.append(f'{key} = "{escaped}"')
