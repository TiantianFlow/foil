"""Durable per-seat continuity registry (CAP-013, CAP-015–CAP-016, CAP-030, CAP-034)."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_RECORD_BYTES = 1024 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SECRET_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "private-key",
    "privatekey",
    "secret",
)
_SECRET_VALUE_PREFIXES = (
    "bearer ",
    "gho_",
    "ghp_",
    "ghr_",
    "ghs_",
    "ghu_",
    "github_pat_",
    "sk-",
    "xox",
)


class RegistryError(ValueError):
    """Registry input or storage is unsafe or malformed."""


class UnsupportedSchemaVersion(RegistryError):
    """The record schema cannot be interpreted by this version."""


def _validate_id(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise RegistryError(f"{field_name} is not a safe stable ID")


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str):
        raise RegistryError("updated_at must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError("updated_at must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise RegistryError("updated_at must include a timezone")


def _assert_no_secret(value: Any, *, path: str = "record") -> None:
    """Reject common secret-shaped data as heuristic defense-in-depth, not a credential boundary."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise RegistryError(f"key at {path} must be a string")
            key_text = str(key)
            key_lower = key_text.lower()
            if any(part in key_lower for part in _SECRET_KEY_PARTS):
                raise RegistryError(f"secret-bearing key is forbidden at {path}.{key_text}")
            _assert_no_secret(child, path=f"{path}.{key_text}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_no_secret(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.strip().lower()
        if any(lowered.startswith(prefix) for prefix in _SECRET_VALUE_PREFIXES):
            raise RegistryError(f"secret-like value is forbidden at {path}")


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextmanager
def _exclusive_lock(path: Path):
    _ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    _ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    fd_closed = False
    try:
        os.fchmod(descriptor, 0o600)
        encoded = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            fd_closed = True
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except Exception:
        if not fd_closed:
            with suppress(OSError):
                os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return path


def _read_json_file(path: Path, *, error_type: type[ValueError]) -> dict[str, Any]:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(file_stat.st_mode):
        raise error_type(f"refusing symlinked record: {path}")
    if not stat.S_ISREG(file_stat.st_mode):
        raise error_type(f"record is not a regular file: {path}")
    if file_stat.st_size > MAX_RECORD_BYTES:
        raise error_type(f"record exceeds {MAX_RECORD_BYTES} bytes: {path}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise error_type(f"cannot safely open record: {path}") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise error_type(f"record is not a regular file: {path}")
        chunks: list[bytes] = []
        remaining = MAX_RECORD_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > MAX_RECORD_BYTES:
        raise error_type(f"record exceeds {MAX_RECORD_BYTES} bytes: {path}")
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise error_type(f"record is not valid JSON: {path}") from exc
    if not isinstance(decoded, dict):
        raise error_type(f"record root must be an object: {path}")
    return decoded


@dataclass(frozen=True, slots=True)
class TmuxTarget:
    session_name: str
    window_name: str
    session_id: str | None
    window_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.session_name, str) or not self.session_name:
            raise RegistryError("tmux session_name must be a non-empty string")
        if not isinstance(self.window_name, str) or not self.window_name:
            raise RegistryError("tmux window_name must be a non-empty string")
        if self.session_id is not None and not isinstance(self.session_id, str):
            raise RegistryError("tmux session_id must be a string or null")
        if self.window_id is not None and not isinstance(self.window_id, str):
            raise RegistryError("tmux window_id must be a string or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "window_name": self.window_name,
            "session_id": self.session_id,
            "window_id": self.window_id,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> TmuxTarget:
        if not isinstance(payload, dict):
            raise RegistryError("tmux must be an object")
        expected = {"session_name", "window_name", "session_id", "window_id"}
        unknown = set(payload) - expected
        missing = expected - set(payload)
        if unknown or missing:
            raise RegistryError(
                f"tmux fields invalid; unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class SeatRecord:
    fleet_id: str
    seat_id: str
    working_directory: str
    agent_kind: str
    native_session_id: str | None
    tmux: TmuxTarget
    git_branch: str
    worktree_path: str
    usage_pool_id: str
    incarnation_id: str
    updated_at: str
    previous_incarnation_id: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _validate_id(self.fleet_id, "fleet_id")
        _validate_id(self.seat_id, "seat_id")
        _validate_id(self.usage_pool_id, "usage_pool_id")
        _validate_id(self.incarnation_id, "incarnation_id")
        if self.previous_incarnation_id is not None:
            _validate_id(self.previous_incarnation_id, "previous_incarnation_id")
        if not isinstance(self.agent_kind, str) or not self.agent_kind:
            raise RegistryError("agent_kind must be a non-empty string")
        if not isinstance(self.working_directory, str) or not self.working_directory:
            raise RegistryError("working_directory must be a non-empty string")
        if not Path(self.working_directory).is_absolute():
            raise RegistryError("working_directory must be absolute")
        if not isinstance(self.worktree_path, str) or not self.worktree_path:
            raise RegistryError("worktree_path must be a non-empty string")
        if not Path(self.worktree_path).is_absolute():
            raise RegistryError("worktree_path must be absolute")
        if not isinstance(self.git_branch, str) or not self.git_branch:
            raise RegistryError("git_branch must be a non-empty string")
        if not isinstance(self.tmux, TmuxTarget):
            raise RegistryError("tmux must be a TmuxTarget")
        if self.native_session_id is not None:
            if not isinstance(self.native_session_id, str):
                raise RegistryError("native_session_id must be a string or null")
            if not self.native_session_id or len(self.native_session_id) > 512:
                raise RegistryError("native_session_id has invalid length")
            _assert_no_secret(self.native_session_id, path="native_session_id")
        if not isinstance(self.extensions, dict):
            raise RegistryError("extensions must be an object")
        _assert_no_secret(self.extensions, path="extensions")
        _validate_timestamp(self.updated_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "seat_id": self.seat_id,
            "working_directory": self.working_directory,
            "agent_kind": self.agent_kind,
            "native_session_id": self.native_session_id,
            "tmux": self.tmux.to_dict(),
            "git_branch": self.git_branch,
            "worktree_path": self.worktree_path,
            "usage_pool_id": self.usage_pool_id,
            "incarnation_id": self.incarnation_id,
            "updated_at": self.updated_at,
            "previous_incarnation_id": self.previous_incarnation_id,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> SeatRecord:
        if not isinstance(payload, dict):
            raise RegistryError("seat record must be an object")
        expected = {
            "schema_version",
            "fleet_id",
            "seat_id",
            "working_directory",
            "agent_kind",
            "native_session_id",
            "tmux",
            "git_branch",
            "worktree_path",
            "usage_pool_id",
            "incarnation_id",
            "updated_at",
            "extensions",
        }
        optional = {"previous_incarnation_id"}
        unknown = set(payload) - expected - optional
        missing = expected - set(payload)
        if unknown:
            raise RegistryError(f"unknown fields: {sorted(unknown)}")
        if missing:
            raise RegistryError(f"missing fields: {sorted(missing)}")
        version = payload["schema_version"]
        if type(version) is not int or version != SCHEMA_VERSION:
            raise UnsupportedSchemaVersion(f"unsupported registry schema version: {version}")
        values = {key: payload[key] for key in expected - {"schema_version", "tmux"}}
        values["tmux"] = TmuxTarget.from_dict(payload["tmux"])
        values["previous_incarnation_id"] = payload.get("previous_incarnation_id")
        return cls(**values)


class RegistryStore:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def seat_path(self, fleet_id: str, seat_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        _validate_id(seat_id, "seat_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "seats"
            / f"{seat_id}.json"
        )

    def _seat_lock_path(self, fleet_id: str, seat_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        _validate_id(seat_id, "seat_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "locks"
            / f"seat-{seat_id}.lock"
        )

    def write_seat(self, record: SeatRecord) -> Path:
        payload = record.to_dict()
        _assert_no_secret(
            {
                "extensions": payload["extensions"],
                "native_session_id": payload["native_session_id"],
            }
        )
        path = self.seat_path(record.fleet_id, record.seat_id)
        lock_path = self._seat_lock_path(record.fleet_id, record.seat_id)
        with _exclusive_lock(lock_path):
            return _atomic_write_json(path, payload)

    def read_seat(self, fleet_id: str, seat_id: str) -> SeatRecord:
        path = self.seat_path(fleet_id, seat_id)
        payload = _read_json_file(path, error_type=RegistryError)
        record = SeatRecord.from_dict(payload)
        if record.fleet_id != fleet_id or record.seat_id != seat_id:
            raise RegistryError("record identity does not match its path")
        return record

    def delete_seat(self, fleet_id: str, seat_id: str) -> None:
        path = self.seat_path(fleet_id, seat_id)
        lock_path = self._seat_lock_path(fleet_id, seat_id)
        with _exclusive_lock(lock_path):
            try:
                path.unlink()
            except FileNotFoundError as exc:
                raise RegistryError(f"seat {seat_id} is not registered") from exc

    def list_seats(self, fleet_id: str) -> list[SeatRecord]:
        _validate_id(fleet_id, "fleet_id")
        seat_dir = self.state_root / f"v{SCHEMA_VERSION}" / "fleets" / fleet_id / "seats"
        try:
            dir_stat = seat_dir.lstat()
        except FileNotFoundError:
            return []
        if stat.S_ISLNK(dir_stat.st_mode):
            raise RegistryError(f"refusing symlinked seat directory: {seat_dir}")
        if not stat.S_ISDIR(dir_stat.st_mode):
            raise RegistryError(f"seat directory is not a directory: {seat_dir}")
        return [
            self.read_seat(fleet_id, path.stem)
            for path in sorted(seat_dir.glob("*.json"), key=lambda item: item.name)
        ]
