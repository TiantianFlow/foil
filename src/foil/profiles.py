"""Declarative per-seat launch profiles (schema v1).

A profile file lets a seat own its executable candidates, launch and resume
argv, startup/bootstrap delivery, session capture, permission-mode flags,
working-directory behavior, and safe environment forwarding — without a
provider-named branch in the core runtime. Shipped adapter records stay the
convenient presets; a profile is the reusable CLI-agnostic contract.

Environment forwarding declares variable *names* only. Values are resolved
from the host environment at launch and are never persisted or printed.
"""

from __future__ import annotations

import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foil.adapters import (
    AdapterError,
    CaptureKind,
    _argv,
    _flag_argv,
    _object,
    _string,
    _strings,
)
from foil.registry import RegistryError, _assert_no_secret

PROFILE_SCHEMA_VERSION = 1
MAX_FORWARDED_ENVIRONMENT = 32
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


class ProfileError(ValueError):
    """A declarative seat profile is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class SeatProfile:
    profile_id: str
    cli: str
    executable_candidates: tuple[str, ...]
    version_argv: tuple[str, ...] | None
    launch_argv: tuple[str, ...] | None
    startup_argv: tuple[str, ...] | None
    resume_supported: bool
    resume_argv: tuple[str, ...] | None
    session_capture: str | None
    session_list_argv: tuple[str, ...] | None
    session_id_pointer: str | None
    session_cwd_pointer: str | None
    permission_supervised: tuple[str, ...]
    permission_auto: tuple[str, ...] | None
    environment_forward: tuple[str, ...]
    isolated: bool | None


def _environment_forward(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ProfileError("environment.forward must be an array of variable names")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _ENVIRONMENT_NAME.fullmatch(item):
            raise ProfileError(
                "environment.forward entries must be variable names only; "
                "values are resolved from the host environment at launch"
            )
        if item in names:
            raise ProfileError(f"environment.forward duplicates variable: {item}")
        names.append(item)
    if len(names) > MAX_FORWARDED_ENVIRONMENT:
        raise ProfileError(
            f"environment.forward is limited to {MAX_FORWARDED_ENVIRONMENT} variables"
        )
    return tuple(names)


def load_profile(path: Path | str) -> SeatProfile:
    """Load and validate one schema-v1 declarative seat profile."""

    profile_path = Path(path)
    try:
        file_stat = profile_path.lstat()
    except FileNotFoundError as exc:
        raise ProfileError(f"profile file does not exist: {profile_path}") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ProfileError(
            f"profile file must be a regular non-symlink file: {profile_path}"
        )
    try:
        with profile_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"cannot load seat profile: {profile_path}") from exc
    try:
        return _validate_profile(payload)
    except AdapterError as exc:
        raise ProfileError(str(exc)) from exc


def _validate_profile(payload: Any) -> SeatProfile:
    if not isinstance(payload, dict):
        raise ProfileError("seat profile must be an object")
    version = payload.get("schema_version")
    if type(version) is not int or version != PROFILE_SCHEMA_VERSION:
        raise ProfileError(f"unsupported profile schema version: {version}")
    unknown = set(payload) - {
        "schema_version",
        "id",
        "cli",
        "executable",
        "launch",
        "startup",
        "resume",
        "session_capture",
        "permissions",
        "environment",
        "working",
    }
    if unknown:
        raise ProfileError(f"seat profile contains unknown fields: {sorted(unknown)}")

    profile_id = _string(payload.get("id"), "id")
    if not _PROFILE_ID.fullmatch(profile_id):
        raise ProfileError("profile id is unsafe")
    cli = _string(payload.get("cli"), "cli")

    candidates = (cli,)
    version_argv: tuple[str, ...] | None = None
    if "executable" in payload:
        executable = _object(payload.get("executable"), "executable")
        unknown = set(executable) - {"candidates", "version_argv"}
        if unknown:
            raise ProfileError(f"executable contains unknown fields: {sorted(unknown)}")
        if "candidates" in executable:
            candidates = _strings(executable.get("candidates"), "executable.candidates")
        if "version_argv" in executable:
            version_argv = _argv(executable.get("version_argv"), "executable.version")
    if cli not in candidates:
        raise ProfileError("executable.candidates must include the profile cli")

    launch_argv: tuple[str, ...] | None = None
    if "launch" in payload:
        launch = _object(payload.get("launch"), "launch")
        unknown = set(launch) - {"argv"}
        if unknown:
            raise ProfileError(f"launch contains unknown fields: {sorted(unknown)}")
        launch_argv = _argv(launch.get("argv"), "launch")

    startup_argv: tuple[str, ...] | None = None
    if "startup" in payload:
        startup = _object(payload.get("startup"), "startup")
        unknown = set(startup) - {"argv"}
        if unknown:
            raise ProfileError(f"startup contains unknown fields: {sorted(unknown)}")
        startup_argv = _argv(startup.get("argv"), "startup")

    resume_supported = False
    resume_argv: tuple[str, ...] | None = None
    if "resume" in payload:
        resume = _object(payload.get("resume"), "resume")
        unknown = set(resume) - {"supported", "argv"}
        if unknown:
            raise ProfileError(f"resume contains unknown fields: {sorted(unknown)}")
        supported = resume.get("supported", False)
        if not isinstance(supported, bool):
            raise ProfileError("resume.supported must be a boolean")
        resume_supported = supported
        resume_argv = _argv(resume.get("argv"), "resume", required=supported)
        if not supported and "argv" in resume:
            raise ProfileError("resume.argv is forbidden when resume is unsupported")

    session_capture: str | None = None
    session_list_argv: tuple[str, ...] | None = None
    session_id_pointer: str | None = None
    session_cwd_pointer: str | None = None
    if "session_capture" in payload:
        capture = _object(payload.get("session_capture"), "session_capture")
        unknown = set(capture) - {"kind", "argv", "id_pointer", "cwd_pointer"}
        if unknown:
            raise ProfileError(
                f"session_capture contains unknown fields: {sorted(unknown)}"
            )
        try:
            capture_kind = CaptureKind(capture.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ProfileError("session_capture.kind is unsupported") from exc
        session_capture = capture_kind.value
        session_list_argv = _argv(
            capture.get("argv"),
            "session_capture",
            required=capture_kind is CaptureKind.COMMAND_JSON_LIST_DELTA,
        )
        session_id_pointer = capture.get("id_pointer")
        session_cwd_pointer = capture.get("cwd_pointer")
        if capture_kind is CaptureKind.COMMAND_JSON_LIST_DELTA:
            session_id_pointer = _string(
                session_id_pointer, "session_capture.id_pointer"
            )
            if not session_id_pointer.startswith("/"):
                raise ProfileError("session_capture.id_pointer must be a JSON pointer")
            if session_cwd_pointer is not None:
                session_cwd_pointer = _string(
                    session_cwd_pointer, "session_capture.cwd_pointer"
                )
                if not session_cwd_pointer.startswith("/"):
                    raise ProfileError(
                        "session_capture.cwd_pointer must be a JSON pointer"
                    )
        elif any(
            value is not None
            for value in (session_list_argv, session_id_pointer, session_cwd_pointer)
        ):
            raise ProfileError("capture command fields require command_json_list_delta")

    permission_supervised: tuple[str, ...] = ()
    permission_auto: tuple[str, ...] | None = None
    if "permissions" in payload:
        permissions = _object(payload.get("permissions"), "permissions")
        unknown = set(permissions) - {"supervised", "auto"}
        if unknown:
            raise ProfileError(f"permissions contains unknown fields: {sorted(unknown)}")
        if "supervised" in permissions:
            permission_supervised = _flag_argv(
                permissions.get("supervised"), "permissions.supervised"
            )
        if "auto" in permissions:
            permission_auto = _flag_argv(permissions.get("auto"), "permissions.auto")
            if not permission_auto:
                raise ProfileError("permissions.auto must declare a non-empty argv")

    environment_forward: tuple[str, ...] = ()
    if "environment" in payload:
        environment = _object(payload.get("environment"), "environment")
        unknown = set(environment) - {"forward"}
        if unknown:
            raise ProfileError(f"environment contains unknown fields: {sorted(unknown)}")
        environment_forward = _environment_forward(environment.get("forward"))

    isolated: bool | None = None
    if "working" in payload:
        working = _object(payload.get("working"), "working")
        unknown = set(working) - {"isolated"}
        if unknown:
            raise ProfileError(f"working contains unknown fields: {sorted(unknown)}")
        if "isolated" in working:
            value = working.get("isolated")
            if not isinstance(value, bool):
                raise ProfileError("working.isolated must be a boolean")
            isolated = value

    try:
        _assert_no_secret(payload, path="profile")
    except RegistryError as exc:
        raise ProfileError(str(exc)) from exc

    return SeatProfile(
        profile_id=profile_id,
        cli=cli,
        executable_candidates=candidates,
        version_argv=version_argv,
        launch_argv=launch_argv,
        startup_argv=startup_argv,
        resume_supported=resume_supported,
        resume_argv=resume_argv,
        session_capture=session_capture,
        session_list_argv=session_list_argv,
        session_id_pointer=session_id_pointer,
        session_cwd_pointer=session_cwd_pointer,
        permission_supervised=permission_supervised,
        permission_auto=permission_auto,
        environment_forward=environment_forward,
        isolated=isolated,
    )
