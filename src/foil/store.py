"""Atomic JSON, locks, and the project registry."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from foil.errors import FoilError, assert_no_secret
from foil.project import foil_root

SCHEMA_VERSION = 1
MAX_BYTES = 1024 * 1024
SEAT_FIELDS = (
    "name",
    "template",
    "harness",
    "window_id",
    "state",
    "worktree",
    "branch",
    "session_id",
)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def actor() -> str:
    seat = os.environ.get("FOIL_SEAT_ID", "").strip()
    if not seat:
        return "user"
    if not SAFE_ID.fullmatch(seat):
        raise FoilError("foil: unknown seat")
    return seat


def scan(value: Any) -> None:
    assert_no_secret(value)
    if isinstance(value, str):
        assert_no_secret(value.splitlines())
        return
    if isinstance(value, Mapping):
        for child in value.values():
            scan(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            scan(child)


def registry_path(toplevel: Path) -> Path:
    return foil_root(toplevel) / "run" / "registry.json"


def empty_registry() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "fleet_id": uuid.uuid4().hex,
        "tmux_session": "",
        "lead": "",
        "seats": {},
    }


def private_dir(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise FoilError("foil: refusing symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    path.chmod(0o700)


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    private_dir(path.parent)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _spill(directory: Path, prefix: str, payload: bytes) -> Path:
    private_dir(directory)
    descriptor, name = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=".tmp")
    temporary = Path(name)
    closed = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            closed = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if not closed:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_bytes(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    temporary = _spill(path.parent, f".{path.name}.", payload)
    try:
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    path.chmod(0o600)
    _fsync_dir(path.parent)


def create_exclusive(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise FileExistsError(path)
    temporary = _spill(path.parent, f".{path.name}.", payload)
    try:
        os.link(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.unlink(missing_ok=True)
    path.chmod(0o600)


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise FoilError("foil: unsupported schema")
    scan(payload)
    encoded = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    if len(encoded) > MAX_BYTES:
        raise FoilError("foil: record is too large")
    write_bytes(path, encoded)


def read_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise FoilError("foil: missing record") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise FoilError("foil: refusing symlink")
    if info.st_size > MAX_BYTES:
        raise FoilError("foil: record is too large")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise FoilError("foil: refusing symlink") from exc
    try:
        raw = os.read(descriptor, info.st_size + 1)
    finally:
        os.close(descriptor)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FoilError("foil: invalid record") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise FoilError("foil: unsupported schema")
    return payload


def _seat_map(raw: Any) -> dict[str, dict[str, str]]:
    if isinstance(raw, dict):
        items = [(key, value) for key, value in raw.items()]
    elif isinstance(raw, list):
        items = [(item.get("name"), item) for item in raw if isinstance(item, dict)]
        if len(items) != len(raw):
            raise FoilError("foil: invalid record")
    else:
        raise FoilError("foil: invalid record")
    seats: dict[str, dict[str, str]] = {}
    for name, value in items:
        if not isinstance(name, str) or not SAFE_ID.fullmatch(name) or not isinstance(value, dict):
            raise FoilError("foil: invalid record")
        record = {key: "" for key in SEAT_FIELDS}
        record["name"] = name
        for key in SEAT_FIELDS:
            field = value.get(key, "")
            if field is None:
                field = ""
            if not isinstance(field, str):
                raise FoilError("foil: invalid record")
            record[key] = field
        seats[name] = record
    return seats


def load_registry(toplevel: Path) -> dict[str, Any]:
    path = registry_path(toplevel)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.exists():
        return empty_registry()
    payload = read_json(path)
    record = empty_registry()
    record["fleet_id"] = payload.get("fleet_id") or record["fleet_id"]
    for key in ("tmux_session", "lead"):
        value = payload.get(key, "")
        if not isinstance(value, str):
            raise FoilError("foil: invalid record")
        record[key] = value
    if not isinstance(record["fleet_id"], str):
        raise FoilError("foil: invalid record")
    record["seats"] = _seat_map(payload.get("seats", {}))
    return record


def save_registry(toplevel: Path, payload: Mapping[str, Any]) -> None:
    path = registry_path(toplevel)
    with exclusive_lock(path.with_suffix(".lock")):
        write_json(path, payload)


def ensure_registry(toplevel: Path) -> None:
    path = registry_path(toplevel)
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if path.is_file():
        return
    save_registry(toplevel, empty_registry())


def find_seat(toplevel: Path, name: str) -> dict[str, str] | None:
    if not SAFE_ID.fullmatch(name):
        return None
    return load_registry(toplevel)["seats"].get(name)
