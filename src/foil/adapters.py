"""Versioned declarative CLI adapter records (CAP-008–CAP-009, CAP-033–CAP-034)."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any

ADAPTER_SCHEMA_VERSION = 1
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_ALLOWED_PLACEHOLDERS = frozenset(
    {
        "adapter_state_dir",
        "model",
        "native_session_id",
        "seat_id",
        "working_directory",
        "bootstrap_path",
    }
)
_SAFE_VALUE = re.compile(r"^[^\x00\r\n]{1,4096}$")


class AdapterError(ValueError):
    """An adapter record or expansion is malformed or unsafe."""


class CaptureKind(StrEnum):
    NONE = "none"
    GENERATED_UUID = "generated_uuid"
    COMMAND_JSON_LIST_DELTA = "command_json_list_delta"


@dataclass(frozen=True, slots=True)
class ExecutableSpec:
    candidates: tuple[str, ...]
    version_argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResumeSpec:
    supported: bool
    argv: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class SessionCaptureSpec:
    kind: CaptureKind
    argv: tuple[str, ...] | None = None
    id_pointer: str | None = None
    cwd_pointer: str | None = None


@dataclass(frozen=True, slots=True)
class StartupSpec:
    argv: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class PermissionsSpec:
    supervised: tuple[str, ...] = ()
    auto: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AdapterRecord:
    adapter_id: str
    observed_version: str
    models: tuple[str, ...]
    skill: str
    executable: ExecutableSpec
    launch: LaunchSpec
    resume: ResumeSpec
    session_capture: SessionCaptureSpec
    startup: StartupSpec = field(default_factory=StartupSpec)
    permissions: PermissionsSpec = field(default_factory=PermissionsSpec)
    schema_version: int = ADAPTER_SCHEMA_VERSION


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdapterError(f"{field} must be a table")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or not _SAFE_VALUE.fullmatch(value):
        raise AdapterError(f"{field} must be a bounded non-empty string")
    return value


def _flag_argv(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise AdapterError(f"{field} must be an array of strings")
    if not value:
        return ()
    result = tuple(_string(token, f"{field} token") for token in value)
    for token in result:
        if _PLACEHOLDER.search(token):
            raise AdapterError(f"{field} cannot contain placeholders")
        if "{" in token or "}" in token:
            raise AdapterError(f"{field} contains malformed placeholder")
    return result


def _argv(value: Any, field: str, *, required: bool = True) -> tuple[str, ...] | None:
    if value is None and not required:
        return None
    if not isinstance(value, list) or not value:
        raise AdapterError(f"{field} argv must be a non-empty array of strings")
    result = tuple(_string(token, f"{field} argv token") for token in value)
    for token in result:
        for placeholder in _PLACEHOLDER.findall(token):
            if placeholder not in _ALLOWED_PLACEHOLDERS:
                raise AdapterError(f"{field} argv contains unknown placeholder: {placeholder}")
        if "{" in _PLACEHOLDER.sub("", token) or "}" in _PLACEHOLDER.sub("", token):
            raise AdapterError(f"{field} argv contains malformed placeholder")
    return result


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AdapterError(f"{field} must be a non-empty array of strings")
    return tuple(_string(item, field) for item in value)


def _only(table: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(table) - allowed
    if unknown:
        raise AdapterError(f"{field} contains unknown fields")


def load_adapter(path: Path | str) -> AdapterRecord:
    """Load and validate one schema-v1 TOML adapter record."""

    adapter_path = Path(path)
    try:
        with adapter_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise AdapterError(f"cannot load adapter record: {adapter_path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError("adapter record must be an object")
    version = payload.get("schema_version")
    if type(version) is not int or version != ADAPTER_SCHEMA_VERSION:
        raise AdapterError(f"unsupported adapter schema version: {version}")
    _only(
        payload,
        {
            "schema_version",
            "id",
            "observed_version",
            "models",
            "skill",
            "executable",
            "launch",
            "resume",
            "session_capture",
            "startup",
            "permissions",
        },
        "adapter",
    )

    executable = _object(payload.get("executable"), "executable")
    _only(executable, {"candidates", "version_argv"}, "executable")
    launch = _object(payload.get("launch"), "launch")
    _only(launch, {"argv"}, "launch")
    resume = _object(payload.get("resume"), "resume")
    _only(resume, {"supported", "argv"}, "resume")
    capture = _object(payload.get("session_capture"), "session_capture")
    _only(capture, {"kind", "argv", "id_pointer", "cwd_pointer"}, "session_capture")

    supported = resume.get("supported")
    if not isinstance(supported, bool):
        raise AdapterError("resume.supported must be a boolean")
    resume_argv = _argv(resume.get("argv"), "resume", required=supported)
    if not supported and "argv" in resume:
        raise AdapterError("resume.argv is forbidden when resume is unsupported")

    try:
        capture_kind = CaptureKind(capture.get("kind"))
    except (TypeError, ValueError) as exc:
        raise AdapterError("session_capture.kind is unsupported") from exc
    capture_argv = _argv(
        capture.get("argv"),
        "session_capture",
        required=capture_kind is CaptureKind.COMMAND_JSON_LIST_DELTA,
    )
    id_pointer = capture.get("id_pointer")
    cwd_pointer = capture.get("cwd_pointer")
    if capture_kind is CaptureKind.COMMAND_JSON_LIST_DELTA:
        id_pointer = _string(id_pointer, "session_capture.id_pointer")
        if not id_pointer.startswith("/"):
            raise AdapterError("session_capture.id_pointer must be a JSON pointer")
        if cwd_pointer is not None:
            cwd_pointer = _string(cwd_pointer, "session_capture.cwd_pointer")
            if not cwd_pointer.startswith("/"):
                raise AdapterError("session_capture.cwd_pointer must be a JSON pointer")
    elif any(value is not None for value in (capture_argv, id_pointer, cwd_pointer)):
        raise AdapterError("capture command fields require command_json_list_delta")

    startup_argv = None
    if "startup" in payload:
        startup = _object(payload.get("startup"), "startup")
        _only(startup, {"argv"}, "startup")
        startup_argv = _argv(startup.get("argv"), "startup")

    permissions = PermissionsSpec()
    if "permissions" in payload:
        table = _object(payload.get("permissions"), "permissions")
        _only(table, {"supervised", "auto"}, "permissions")
        supervised = ()
        auto: tuple[str, ...] | None = None
        if "supervised" in table:
            supervised = _flag_argv(table.get("supervised"), "permissions.supervised")
        if "auto" in table:
            auto = _flag_argv(table.get("auto"), "permissions.auto")
            if not auto:
                raise AdapterError(
                    "permissions.auto must declare a non-empty argv"
                )
        permissions = PermissionsSpec(supervised=supervised, auto=auto)

    return AdapterRecord(
        adapter_id=_string(payload.get("id"), "id"),
        observed_version=_string(payload.get("observed_version"), "observed_version"),
        models=_strings(payload.get("models"), "models"),
        skill=_string(payload.get("skill"), "skill"),
        executable=ExecutableSpec(
            candidates=_strings(executable.get("candidates"), "executable.candidates"),
            version_argv=_argv(executable.get("version_argv"), "executable.version") or (),
        ),
        launch=LaunchSpec(argv=_argv(launch.get("argv"), "launch") or ()),
        resume=ResumeSpec(supported=supported, argv=resume_argv),
        session_capture=SessionCaptureSpec(
            kind=capture_kind,
            argv=capture_argv,
            id_pointer=id_pointer,
            cwd_pointer=cwd_pointer,
        ),
        startup=StartupSpec(argv=startup_argv),
        permissions=permissions,
    )


def load_builtin_adapter(adapter_id: str) -> AdapterRecord:
    if not isinstance(adapter_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", adapter_id
    ):
        raise AdapterError("adapter ID is unsafe")
    resource = (
        resources.files("foil")
        .joinpath("resources")
        .joinpath("adapters")
        .joinpath(f"{adapter_id}.toml")
    )
    try:
        with resources.as_file(resource) as path:
            record = load_adapter(path)
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise AdapterError(f"unknown built-in adapter: {adapter_id}") from exc
    if record.adapter_id != adapter_id:
        raise AdapterError("built-in adapter identity does not match its file name")
    return record


PERMISSION_SUPERVISED = "supervised"
PERMISSION_AUTO = "auto"
PERMISSION_PROFILES = (PERMISSION_SUPERVISED, PERMISSION_AUTO)


def permission_argv(adapter: AdapterRecord, profile: str) -> tuple[str, ...]:
    """Return extra launch/resume flags for an explicit permission profile."""

    if profile == PERMISSION_SUPERVISED:
        return adapter.permissions.supervised
    if profile == PERMISSION_AUTO:
        if adapter.permissions.auto is None:
            raise AdapterError(
                f"permission profile auto is unsupported for adapter {adapter.adapter_id}"
            )
        return adapter.permissions.auto
    raise AdapterError(f"unknown permission profile {profile}")


def expand_argv(template: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """Expand typed placeholders without parsing or constructing a shell command."""

    result: list[str] = []
    for token in template:
        names = _PLACEHOLDER.findall(token)
        for name in names:
            if name not in _ALLOWED_PLACEHOLDERS:
                raise AdapterError(f"unknown placeholder: {name}")
            if name not in values:
                raise AdapterError(f"missing placeholder: {name}")
            value = values[name]
            if not isinstance(value, str) or not _SAFE_VALUE.fullmatch(value):
                raise AdapterError(f"placeholder {name} has an unsafe value")
            token = token.replace(f"{{{name}}}", value)
        if "{" in _PLACEHOLDER.sub("", token) or "}" in _PLACEHOLDER.sub("", token):
            raise AdapterError("malformed placeholder")
        result.append(token)
    return result
