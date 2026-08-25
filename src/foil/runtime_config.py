"""Validated runtime fleet configuration (CAP-005, CAP-024, CAP-030, CAP-033)."""

from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from foil.adapters import CaptureKind

CONFIG_SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SEAT_CAPTURE_KINDS = {
    CaptureKind.NONE.value,
    CaptureKind.GENERATED_UUID.value,
    CaptureKind.COMMAND_JSON_LIST_DELTA.value,
}


class ConfigError(ValueError):
    """Fleet configuration is malformed or unsafe."""


def _id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ConfigError(f"{field_name} is not a safe stable ID")
    return value


def _text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > 4096:
        raise ConfigError(f"{field_name} must be a bounded non-empty string")
    return value


def _absolute_path(value: Any, field_name: str) -> Path:
    path = Path(_text(value, field_name))
    if not path.is_absolute():
        raise ConfigError(f"{field_name} must be absolute")
    return path


def _table_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} must be a non-empty array of tables")
    if not all(isinstance(item, dict) for item in value):
        raise ConfigError(f"{field_name} entries must be tables")
    return value


def _only(table: dict[str, Any], expected: set[str], field_name: str) -> None:
    unknown = set(table) - expected
    if unknown:
        raise ConfigError(f"{field_name} contains unknown fields")


def _optional_argv(value: Any, field_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{field_name} must be a non-empty array of strings")
    if not all(isinstance(item, str) and item and "\x00" not in item for item in value):
        raise ConfigError(f"{field_name} entries must be non-empty strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class UsagePoolConfig:
    pool_id: str
    adapter_id: str | None
    model: str | None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SeatConfig:
    seat_id: str
    display_name: str
    usage_pool_id: str
    working_directory: Path
    worktree_path: Path
    git_branch: str
    cli: str | None = None
    launch_argv: tuple[str, ...] | None = None
    resume_argv: tuple[str, ...] | None = None
    session_capture: str | None = None


@dataclass(frozen=True, slots=True)
class FleetConfig:
    fleet_id: str
    display_name: str
    adapter_paths: tuple[Path, ...]
    usage_pools: tuple[UsagePoolConfig, ...]
    seats: tuple[SeatConfig, ...]
    schema_version: int = CONFIG_SCHEMA_VERSION

    def pool_for(self, seat: SeatConfig) -> UsagePoolConfig:
        return next(pool for pool in self.usage_pools if pool.pool_id == seat.usage_pool_id)


def load_fleet_config(path: Path | str) -> FleetConfig:
    """Load a fail-closed runtime configuration without mutating runtime state."""

    config_path = Path(path)
    try:
        file_stat = config_path.lstat()
    except OSError as exc:
        raise ConfigError(f"cannot read fleet config: {config_path}") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ConfigError("fleet config must be a regular non-symlink file")
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("fleet config is not valid TOML") from exc
    if not isinstance(payload, dict):
        raise ConfigError("fleet config must be an object")
    version = payload.get("schema_version")
    if type(version) is not int or version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"unsupported fleet config schema version: {version}")
    _only(
        payload,
        {
            "schema_version",
            "fleet_id",
            "display_name",
            "adapter_paths",
            "usage_pools",
            "seats",
        },
        "fleet config",
    )

    raw_paths = payload.get("adapter_paths", [])
    if not isinstance(raw_paths, list) or not all(isinstance(item, str) for item in raw_paths):
        raise ConfigError("adapter_paths must be an array of paths")
    adapter_paths = tuple(
        path if path.is_absolute() else (config_path.parent / path).resolve()
        for path in (Path(item) for item in raw_paths)
    )

    pools: list[UsagePoolConfig] = []
    pool_ids: set[str] = set()
    for index, raw in enumerate(_table_list(payload.get("usage_pools"), "usage_pools")):
        _only(raw, {"id", "adapter", "model", "extensions"}, f"usage_pools[{index}]")
        pool_id = _id(raw.get("id"), "usage_pool.id")
        if pool_id in pool_ids:
            raise ConfigError(f"duplicate usage pool: {pool_id}")
        pool_ids.add(pool_id)
        extensions = raw.get("extensions", {})
        if not isinstance(extensions, dict):
            raise ConfigError("usage_pool.extensions must be an object")
        raw_adapter = raw.get("adapter")
        raw_model = raw.get("model")
        adapter_id = _id(raw_adapter, "usage_pool.adapter") if raw_adapter is not None else None
        if raw_adapter is not None and raw_model is None:
            raise ConfigError(f"usage pool {pool_id} is missing model")
        model = _text(raw_model, "usage_pool.model") if raw_model is not None else None
        pools.append(
            UsagePoolConfig(
                pool_id=pool_id,
                adapter_id=adapter_id,
                model=model,
                extensions=extensions,
            )
        )

    seats: list[SeatConfig] = []
    seat_ids: set[str] = set()
    for index, raw in enumerate(_table_list(payload.get("seats"), "seats")):
        _only(
            raw,
            {
                "id",
                "display_name",
                "usage_pool_id",
                "working_directory",
                "worktree_path",
                "git_branch",
                "cli",
                "launch_argv",
                "resume_argv",
                "session_capture",
            },
            f"seats[{index}]",
        )
        seat_id = _id(raw.get("id"), "seat.id")
        if seat_id in seat_ids:
            raise ConfigError(f"duplicate seat: {seat_id}")
        seat_ids.add(seat_id)
        usage_pool_id = _id(raw.get("usage_pool_id"), "seat.usage_pool_id")
        if usage_pool_id not in pool_ids:
            raise ConfigError(f"seat {seat_id} references unknown usage pool")
        cli = _id(raw.get("cli"), "seat.cli") if raw.get("cli") is not None else None
        launch_argv = _optional_argv(raw.get("launch_argv"), "seat.launch_argv")
        resume_argv = _optional_argv(raw.get("resume_argv"), "seat.resume_argv")
        session_capture = raw.get("session_capture")
        if session_capture is not None:
            session_capture = _text(session_capture, "seat.session_capture")
            if session_capture not in _SEAT_CAPTURE_KINDS:
                raise ConfigError(f"seat {seat_id} has an unknown session_capture")
        if cli is None:
            pool = next(item for item in pools if item.pool_id == usage_pool_id)
            if pool.adapter_id is None:
                raise ConfigError(f"seat {seat_id} needs cli= or a pool adapter")
        elif launch_argv is None:
            raise ConfigError(f"seat {seat_id} declares cli= without launch_argv")
        seats.append(
            SeatConfig(
                seat_id=seat_id,
                display_name=_text(raw.get("display_name"), "seat.display_name"),
                usage_pool_id=usage_pool_id,
                working_directory=_absolute_path(
                    raw.get("working_directory"), "seat.working_directory"
                ),
                worktree_path=_absolute_path(raw.get("worktree_path"), "seat.worktree_path"),
                git_branch=_text(raw.get("git_branch"), "seat.git_branch"),
                cli=cli,
                launch_argv=launch_argv,
                resume_argv=resume_argv,
                session_capture=session_capture,
            )
        )

    return FleetConfig(
        fleet_id=_id(payload.get("fleet_id"), "fleet_id"),
        display_name=_text(payload.get("display_name"), "display_name"),
        adapter_paths=adapter_paths,
        usage_pools=tuple(pools),
        seats=tuple(sorted(seats, key=lambda seat: seat.seat_id)),
    )
