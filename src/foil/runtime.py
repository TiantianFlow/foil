"""Generic tmux-backed lifecycle orchestration (CAP-008–CAP-019, CAP-024, CAP-029)."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.adapters import (
    _SAFE_VALUE,
    PERMISSION_PROFILES,
    PERMISSION_SUPERVISED,
    AdapterError,
    AdapterRecord,
    CaptureKind,
    ExecutableSpec,
    LaunchSpec,
    PermissionsSpec,
    ResumeSpec,
    SessionCaptureSpec,
    StartupSpec,
    expand_argv,
    load_adapter,
    load_builtin_adapter,
    permission_argv,
)
from foil.doctor import DoctorError, ensure_isolated_worktree
from foil.fleet import (
    LEAD_CAPABILITIES,
    WORKER_CAPABILITIES,
    FleetRecord,
    FleetStore,
)
from foil.known_clis import adapter_id_for_cli
from foil.naming import tmux_session_name, tmux_window_name
from foil.profiles import ProfileError, SeatProfile, load_profile
from foil.registry import (
    RegistryError,
    RegistryStore,
    SeatRecord,
    TmuxTarget,
    _assert_no_secret,
    _atomic_write_json,
    _validate_id,
)
from foil.resume import ResumeAction, ResumeEvidence, TmuxProbeState, resolve_resume
from foil.runtime_config import SeatConfig, UsagePoolConfig
from foil.status import PollStatusReader, SeatState, StatusSnapshot
from foil.tmux import ProbeResult, ProbeState, TmuxController


class RuntimeError(ValueError):
    """A lifecycle operation failed validation or safe execution."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AdapterCatalog:
    def __init__(self, search_paths: tuple[Path, ...]):
        self.search_paths = search_paths
        self._cache: dict[str, AdapterRecord] = {}

    def load(self, adapter_id: str) -> AdapterRecord:
        if adapter_id in self._cache:
            return self._cache[adapter_id]
        for directory in self.search_paths:
            try:
                directory_stat = directory.lstat()
            except OSError as exc:
                raise RuntimeError(f"cannot inspect adapter path: {directory}") from exc
            if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
                raise RuntimeError("adapter path must be a regular non-symlink directory")
            matches: list[AdapterRecord] = []
            for candidate in sorted(directory.glob("*.toml")):
                candidate_stat = candidate.lstat()
                if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISREG(
                    candidate_stat.st_mode
                ):
                    raise RuntimeError("adapter record must be a regular non-symlink file")
                record = load_adapter(candidate)
                if record.adapter_id == adapter_id:
                    matches.append(record)
            if len(matches) > 1:
                raise RuntimeError(f"adapter identity is duplicated: {adapter_id}")
            if matches:
                self._cache[adapter_id] = matches[0]
                return matches[0]
        try:
            record = load_builtin_adapter(adapter_id)
        except AdapterError as exc:
            raise RuntimeError(f"adapter is unavailable: {adapter_id}") from exc
        self._cache[adapter_id] = record
        return record


@dataclass(frozen=True, slots=True)
class SeatRuntime:
    seat: SeatConfig
    pool: UsagePoolConfig
    adapter: AdapterRecord
    executable: str | None


def _usable_resolved_executable(adapter: AdapterRecord, resolved: str) -> bool:
    path = Path(resolved)
    if not path.is_absolute():
        return False
    try:
        file_stat = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(file_stat.st_mode) or not os.access(resolved, os.X_OK):
        return False
    return (
        resolved in adapter.executable.candidates
        or path.name in adapter.executable.candidates
    )


def _resolve_executable(
    adapter: AdapterRecord,
    *,
    fallback: str | None = None,
) -> str:
    for candidate in adapter.executable.candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    if fallback is not None and _usable_resolved_executable(adapter, fallback):
        return fallback
    raise RuntimeError(f"adapter executable is unavailable: {adapter.adapter_id}")


def _safe_environment(
    *,
    executable: str | None = None,
    foil: dict[str, str] | None = None,
) -> dict[str, str]:
    exact = {"COLORTERM", "HOME", "LANG", "PATH", "SHELL", "TERM", "TMPDIR"}
    prefixes = ("LC_", "XDG_")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in exact or key.startswith(prefixes)
    }
    if executable:
        parent = str(Path(executable).parent)
        current = environment.get("PATH", "")
        parts = [part for part in current.split(os.pathsep) if part]
        if parent not in parts:
            environment["PATH"] = parent if not current else f"{parent}{os.pathsep}{current}"
    if foil:
        for key, value in foil.items():
            if key.startswith("FOIL_") and value:
                environment[key] = value
    return environment


def _git_output(cwd: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("git identity probe failed") from exc
    if result.returncode != 0:
        raise RuntimeError("working directory is not a valid Git worktree")
    if len(result.stdout) > 64 * 1024:
        raise RuntimeError("git identity probe output exceeded the limit")
    return result.stdout.strip()


def validate_worktree(seat: SeatConfig) -> None:
    """Validate configured cwd, worktree, and branch before lifecycle mutation."""

    try:
        cwd = seat.working_directory.resolve(strict=True)
        expected_worktree = seat.worktree_path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"seat {seat.seat_id} path does not exist") from exc
    if not cwd.is_dir() or not expected_worktree.is_dir():
        raise RuntimeError(f"seat {seat.seat_id} paths must be directories")
    observed_worktree = Path(_git_output(cwd, "rev-parse", "--show-toplevel")).resolve()
    observed_branch = _git_output(cwd, "branch", "--show-current")
    if observed_worktree != expected_worktree:
        raise RuntimeError(f"seat {seat.seat_id} worktree mismatch")
    if observed_branch != seat.git_branch:
        raise RuntimeError(f"seat {seat.seat_id} branch mismatch")
    try:
        cwd.relative_to(expected_worktree)
    except ValueError as exc:
        raise RuntimeError(f"seat {seat.seat_id} cwd is outside its worktree") from exc


def _json_pointer(value: Any, pointer: str) -> Any:
    current = value
    if pointer == "":
        return current
    for raw in pointer.removeprefix("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise RuntimeError("structured session discovery returned an invalid shape")
    return current


class AuditLog:
    def __init__(self, state_root: Path, fleet_id: str):
        self.path = (
            state_root / "v1" / "fleets" / fleet_id / "events" / "events.jsonl"
        )
        self.lock_path = self.path.parent.parent / "locks" / "events.lock"
        self.fleet_id = fleet_id

    def append(self, event_type: str, seat_id: str, **details: Any) -> str:
        event_id = str(uuid.uuid4())
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": event_type,
            "fleet_id": self.fleet_id,
            "seat_id": seat_id,
            "recorded_at": _now(),
            "details": details,
        }
        _assert_no_secret(event)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            event_descriptor = os.open(self.path, flags, 0o600)
            try:
                os.fchmod(event_descriptor, 0o600)
                encoded = (
                    json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode()
                os.write(event_descriptor, encoded)
                os.fsync(event_descriptor)
            finally:
                os.close(event_descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        return event_id


class RuntimeController:
    def __init__(
        self,
        state_root: Path | str,
        fleet_id: str,
        *,
        tmux: TmuxController | None = None,
    ):
        self.state_root = Path(state_root)
        self.fleets = FleetStore(self.state_root)
        self.fleet = self.fleets.read(fleet_id)
        self.registry = RegistryStore(self.state_root)
        self.status_store = PollStatusReader(self.state_root)
        self.catalog = AdapterCatalog(())
        self.tmux = tmux or TmuxController()
        self.audit = AuditLog(self.state_root, self.fleet.fleet_id)

    def _runtimes(self) -> list[SeatRuntime]:
        return [
            self._runtime_from_record(record)
            for record in self.registry.list_seats(self.fleet.fleet_id)
        ]

    def _runtime_from_record(self, record: SeatRecord) -> SeatRuntime:
        profile = record.extensions.get("profile")
        if not isinstance(profile, dict) or not profile.get("cli"):
            raise RuntimeError(f"seat {record.seat_id} is missing a spawn profile")
        seat = SeatConfig(
            seat_id=record.seat_id,
            display_name=str(profile.get("display_name") or record.seat_id),
            usage_pool_id=record.usage_pool_id,
            working_directory=Path(record.working_directory),
            worktree_path=Path(record.worktree_path),
            git_branch=record.git_branch,
            cli=str(profile["cli"]),
            launch_argv=tuple(profile.get("launch_argv") or ()),
            resume_argv=tuple(profile["resume_argv"]) if profile.get("resume_argv") else None,
            session_capture=profile.get("session_capture"),
            environment_forward=tuple(profile.get("environment_forward") or ()),
        )
        pool = UsagePoolConfig(
            pool_id=record.usage_pool_id,
            adapter_id=profile.get("adapter_id")
            if isinstance(profile.get("adapter_id"), str)
            else None,
            model=profile.get("model") if isinstance(profile.get("model"), str) else None,
        )
        adapter = _adapter_from_profile(seat, pool, profile)
        try:
            executable = _resolve_executable(adapter)
        except RuntimeError:
            executable = None
        return SeatRuntime(seat=seat, pool=pool, adapter=adapter, executable=executable)

    def _validate_runtimes(self) -> list[SeatRuntime]:
        runtimes = self._runtimes()
        for runtime in runtimes:
            validate_worktree(runtime.seat)
        return runtimes

    def _with_executable(
        self,
        runtime: SeatRuntime,
        record: SeatRecord | None = None,
    ) -> SeatRuntime:
        if runtime.executable:
            return runtime
        fallback = None
        if record is not None:
            raw = record.extensions.get("resolved_executable")
            if isinstance(raw, str):
                fallback = raw
        return replace(runtime, executable=_resolve_executable(runtime.adapter, fallback=fallback))

    def _adapter_state_dir(self, seat_id: str) -> Path:
        return (
            self.state_root
            / "v1"
            / "fleets"
            / self.fleet.fleet_id
            / "adapter-state"
            / seat_id
        )

    def _values(
        self,
        runtime: SeatRuntime,
        native_session_id: str | None,
    ) -> dict[str, str]:
        values = {
            "adapter_state_dir": str(self._adapter_state_dir(runtime.seat.seat_id)),
            "bootstrap_path": str(
                self._adapter_state_dir(runtime.seat.seat_id) / "bootstrap.json"
            ),
            "seat_id": runtime.seat.seat_id,
            "working_directory": str(runtime.seat.working_directory),
        }
        if runtime.pool.model is not None:
            values["model"] = runtime.pool.model
        if native_session_id is not None:
            values["native_session_id"] = native_session_id
        return values

    def _launch_argv(
        self,
        runtime: SeatRuntime,
        native_session_id: str | None,
        *,
        permission: str,
        include_startup: bool = True,
    ) -> list[str]:
        try:
            extra = permission_argv(runtime.adapter, permission)
        except AdapterError as exc:
            raise RuntimeError(str(exc)) from exc
        template = runtime.adapter.launch.argv + extra
        if include_startup and runtime.adapter.startup.argv:
            template = template + runtime.adapter.startup.argv
        return self._expanded(runtime, template, native_session_id)

    def _expanded(
        self,
        runtime: SeatRuntime,
        template: tuple[str, ...],
        native_session_id: str | None,
    ) -> list[str]:
        if runtime.executable is None:
            raise RuntimeError(
                f"adapter executable is unavailable: {runtime.adapter.adapter_id}"
            )
        expanded = expand_argv(template, self._values(runtime, native_session_id))
        if expanded[0] in runtime.adapter.executable.candidates:
            expanded[0] = runtime.executable
        elif Path(expanded[0]).resolve() != Path(runtime.executable).resolve():
            raise RuntimeError("adapter argv executable is not a declared candidate")
        return expanded

    def _list_sessions(self, runtime: SeatRuntime) -> list[dict[str, Any]]:
        capture = runtime.adapter.session_capture
        if capture.kind is not CaptureKind.COMMAND_JSON_LIST_DELTA or capture.argv is None:
            return []
        argv = self._expanded(runtime, capture.argv, None)
        try:
            result = subprocess.run(
                argv,
                cwd=runtime.seat.working_directory,
                env=_safe_environment(executable=runtime.executable),
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("structured session discovery failed") from exc
        if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
            raise RuntimeError("structured session discovery failed")
        if not result.stdout.strip():
            return []
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("structured session discovery returned invalid JSON") from exc
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RuntimeError("structured session discovery must return a JSON array")
        return payload

    def _session_ids(self, runtime: SeatRuntime) -> set[str]:
        if runtime.adapter.session_capture.id_pointer is None:
            return set()
        ids: set[str] = set()
        for item in self._list_sessions(runtime):
            session_id = self._selected_session_id(runtime, item)
            if session_id is not None:
                ids.add(session_id)
        return ids

    def _selected_session_id(
        self,
        runtime: SeatRuntime,
        item: dict[str, Any],
    ) -> str | None:
        capture = runtime.adapter.session_capture
        if capture.cwd_pointer is not None:
            observed_cwd = _json_pointer(item, capture.cwd_pointer)
            if not isinstance(observed_cwd, str) or not observed_cwd:
                raise RuntimeError("structured session discovery returned an invalid cwd")
            observed_path = Path(observed_cwd)
            if not observed_path.is_absolute():
                raise RuntimeError("structured session discovery returned an invalid cwd")
            try:
                matches_working_directory = (
                    observed_path.resolve() == runtime.seat.working_directory.resolve()
                )
            except (OSError, ValueError) as exc:
                raise RuntimeError(
                    "structured session discovery returned an invalid cwd"
                ) from exc
            if not matches_working_directory:
                return None

        session_id = _json_pointer(item, capture.id_pointer or "")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            raise RuntimeError("structured session discovery returned an invalid ID")
        return session_id

    def _capture_delta(
        self,
        runtime: SeatRuntime,
        baseline: set[str],
        *,
        wait_seconds: float,
    ) -> str | None:
        deadline = time.monotonic() + wait_seconds
        while True:
            candidates: list[str] = []
            for item in self._list_sessions(runtime):
                candidate = self._selected_session_id(runtime, item)
                if candidate is None or candidate in baseline:
                    continue
                candidates.append(candidate)
            candidates = sorted(set(candidates))
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise RuntimeError("structured session discovery is ambiguous")
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def _bootstrap_env(
        self,
        runtime: SeatRuntime,
        *,
        incarnation_id: str,
        profile: dict[str, Any],
        lead_seat_id: str | None = None,
    ) -> dict[str, str]:
        path = self._adapter_state_dir(runtime.seat.seat_id) / "bootstrap.json"
        capabilities = [str(item) for item in profile.get("capabilities") or WORKER_CAPABILITIES]
        resolved_lead = lead_seat_id if lead_seat_id is not None else self.fleet.lead_seat_id
        payload = {
            "fleet_id": self.fleet.fleet_id,
            "seat_id": runtime.seat.seat_id,
            "lead_seat_id": resolved_lead,
            "state_dir": str(self.state_root.resolve()),
            "incarnation_id": incarnation_id,
            "capabilities": capabilities,
            "role_id": profile.get("role_id"),
            "role_path": profile.get("role_path"),
            "is_lead": bool(profile.get("is_lead")),
        }
        _atomic_write_json(path, payload)
        instructions_text = _worker_instructions(payload, runtime.seat.seat_id)
        instructions = path.with_name("FOIL.md")
        instructions.write_text(instructions_text, encoding="utf-8")
        if profile.get("isolated") and not _git_tracks(
            runtime.seat.working_directory, "FOIL.md"
        ):
            (runtime.seat.working_directory / "FOIL.md").write_text(
                instructions_text,
                encoding="utf-8",
            )
        return {
            "FOIL_STATE_DIR": str(self.state_root.resolve()),
            "FOIL_FLEET_ID": self.fleet.fleet_id,
            "FOIL_SEAT_ID": runtime.seat.seat_id,
            "FOIL_INCARNATION_ID": incarnation_id,
            "FOIL_LEAD_SEAT_ID": resolved_lead or "",
            "FOIL_CAPABILITIES": ",".join(capabilities),
            "FOIL_ROLE_FILE": str(profile.get("role_path") or ""),
            "FOIL_BOOTSTRAP": str(path),
            "FOIL_INSTRUCTIONS": str(instructions),
        }

    def _write_plan(
        self,
        runtime: SeatRuntime,
        argv: list[str],
        *,
        foil: dict[str, str] | None = None,
    ) -> Path:
        plan_dir = (
            self.state_root
            / "v1"
            / "fleets"
            / self.fleet.fleet_id
            / "runner-plans"
        )
        plan = plan_dir / f"{runtime.seat.seat_id}.json"
        payload: dict[str, Any] = {
            "argv": argv,
            "cwd": str(runtime.seat.working_directory),
            "env": _safe_environment(executable=runtime.executable, foil=foil),
        }
        if runtime.seat.environment_forward:
            # Variable names only; values are resolved at exec time by the
            # runner and are never persisted.
            payload["env_forward"] = list(runtime.seat.environment_forward)
        return _atomic_write_json(plan, payload)

    def _spawn(
        self,
        runtime: SeatRuntime,
        argv: list[str],
        *,
        foil: dict[str, str] | None = None,
        environment: dict[str, str] | None = None,
    ) -> TmuxTarget:
        plan = self._write_plan(runtime, argv, foil=foil)
        return self.tmux.launch(
            fleet_id=self.fleet.fleet_id,
            seat_id=runtime.seat.seat_id,
            session_name=tmux_session_name(
                self.fleet.display_name,
                self.fleet.fleet_id,
            ),
            window_name=tmux_window_name(
                runtime.seat.display_name,
                runtime.seat.seat_id,
            ),
            working_directory=runtime.seat.working_directory,
            runner_argv=[sys.executable, "-m", "foil.runner", str(plan)],
            environment=environment,
        )

    def _status(
        self,
        runtime: SeatRuntime,
        record: SeatRecord,
        state: SeatState,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = StatusSnapshot(
            fleet_id=self.fleet.fleet_id,
            seat_id=runtime.seat.seat_id,
            state=state,
            updated_at=_now(),
            evidence=evidence,
            extensions={
                "observed_git_branch": runtime.seat.git_branch,
                "observed_worktree_path": str(runtime.seat.worktree_path.resolve()),
            },
        )
        self.status_store.write_fixture(snapshot)
        return {
            "seat_id": runtime.seat.seat_id,
            "usage_pool_id": runtime.pool.pool_id,
            "state": state.value,
            "registry": record.to_dict(),
        }

    def _new_record(
        self,
        runtime: SeatRuntime,
        native_session_id: str | None,
        target: TmuxTarget,
        baseline: set[str],
        *,
        incarnation_id: str | None = None,
        previous_incarnation_id: str | None = None,
        extra_extensions: dict[str, Any] | None = None,
    ) -> SeatRecord:
        extensions: dict[str, Any] = {
            "adapter_schema_version": runtime.adapter.schema_version,
            "capture_baseline": sorted(baseline),
        }
        if extra_extensions:
            extensions.update(extra_extensions)
        if runtime.pool.model is not None:
            extensions["model"] = runtime.pool.model
        if runtime.executable is not None:
            extensions["resolved_executable"] = runtime.executable
        return SeatRecord(
            fleet_id=self.fleet.fleet_id,
            seat_id=runtime.seat.seat_id,
            working_directory=str(runtime.seat.working_directory.resolve()),
            agent_kind=runtime.adapter.adapter_id,
            native_session_id=native_session_id,
            tmux=target,
            git_branch=runtime.seat.git_branch,
            worktree_path=str(runtime.seat.worktree_path.resolve()),
            usage_pool_id=runtime.pool.pool_id,
            incarnation_id=incarnation_id or str(uuid.uuid4()),
            updated_at=_now(),
            previous_incarnation_id=previous_incarnation_id,
            extensions=extensions,
        )

    def _validate_record(self, runtime: SeatRuntime, record: SeatRecord) -> None:
        expected = (
            record.fleet_id == self.fleet.fleet_id
            and record.seat_id == runtime.seat.seat_id
            and Path(record.working_directory).resolve()
            == runtime.seat.working_directory.resolve()
            and Path(record.worktree_path).resolve() == runtime.seat.worktree_path.resolve()
            and record.git_branch == runtime.seat.git_branch
            and record.agent_kind == runtime.adapter.adapter_id
            and record.usage_pool_id == runtime.pool.pool_id
            and record.extensions.get("model") == runtime.pool.model
        )
        if not expected:
            raise RuntimeError(f"seat {runtime.seat.seat_id} registry/config mismatch")

    def _read_registered(self, runtime: SeatRuntime) -> SeatRecord | None:
        try:
            return self.registry.read_seat(self.fleet.fleet_id, runtime.seat.seat_id)
        except FileNotFoundError:
            return None

    def spawn(
        self,
        *,
        seat_id: str,
        cli: str | None = None,
        launch_argv: tuple[str, ...] = (),
        actor: str | None = None,
        display_name: str | None = None,
        resume_argv: tuple[str, ...] | None = None,
        session_capture: str | None = None,
        session_list_argv: tuple[str, ...] | None = None,
        session_id_pointer: str | None = None,
        session_cwd_pointer: str | None = None,
        role_id: str | None = None,
        role_path: str | None = None,
        isolated: bool | None = None,
        shared_cwd: bool = False,
        working_directory: Path | None = None,
        model: str | None = None,
        lead: bool = False,
        permission: str = PERMISSION_SUPERVISED,
        profile_file: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.fleets.operation_lock(self.fleet.fleet_id):
            return self._spawn_locked(
                seat_id=seat_id,
                cli=cli,
                launch_argv=launch_argv,
                actor=actor,
                display_name=display_name,
                resume_argv=resume_argv,
                session_capture=session_capture,
                session_list_argv=session_list_argv,
                session_id_pointer=session_id_pointer,
                session_cwd_pointer=session_cwd_pointer,
                role_id=role_id,
                role_path=role_path,
                isolated=isolated,
                shared_cwd=shared_cwd,
                working_directory=working_directory,
                model=model,
                lead=lead,
                permission=permission,
                profile_file=profile_file,
            )

    def _spawn_locked(
        self,
        *,
        seat_id: str,
        cli: str | None,
        launch_argv: tuple[str, ...],
        actor: str | None,
        display_name: str | None,
        resume_argv: tuple[str, ...] | None,
        session_capture: str | None,
        session_list_argv: tuple[str, ...] | None,
        session_id_pointer: str | None,
        session_cwd_pointer: str | None,
        role_id: str | None,
        role_path: str | None,
        isolated: bool | None,
        shared_cwd: bool,
        working_directory: Path | None,
        model: str | None,
        lead: bool,
        permission: str,
        profile_file: str | None,
    ) -> list[dict[str, Any]]:
        self.fleets.require_lifecycle_actor(self.fleet.fleet_id, actor)
        if permission not in PERMISSION_PROFILES:
            raise RuntimeError(f"unknown permission profile {permission}")
        if shutil.which("tmux") is None:
            raise RuntimeError("tmux is unavailable")
        try:
            self.registry.read_seat(self.fleet.fleet_id, seat_id)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError(f"seat {seat_id} is already registered")
        fleet = self.fleets.read(self.fleet.fleet_id)
        self.fleet = fleet
        if lead and fleet.lead_seat_id is not None:
            raise RuntimeError("fleet already has a lead seat")
        if not lead and fleet.lead_seat_id is None:
            raise RuntimeError("fleet must start with a lead seat")
        if shared_cwd and isolated is True:
            raise RuntimeError("shared-cwd cannot be combined with isolated")
        # Load and merge the declarative seat profile before any side effect.
        seat_profile: SeatProfile | None = None
        if profile_file is not None:
            try:
                seat_profile = load_profile(profile_file)
            except ProfileError as exc:
                raise RuntimeError(str(exc)) from exc
            if cli is not None and cli != seat_profile.cli:
                raise RuntimeError(
                    f"--cli {cli} conflicts with the profile cli {seat_profile.cli}"
                )
            cli = seat_profile.cli
            if isolated is None and seat_profile.isolated is not None:
                isolated = seat_profile.isolated
        if not cli:
            raise RuntimeError("seat spawn requires --cli or --profile")
        launch_argv = _normalize_remainder(launch_argv)
        declarative = seat_profile is not None
        if declarative:
            assert seat_profile is not None
            if launch_argv:
                # Profile-owned remainder argv wins over the file's launch argv.
                full_launch_argv: tuple[str, ...] = (cli, *launch_argv)
            elif seat_profile.launch_argv:
                # Declarative launch argv includes argv[0], like adapter records.
                full_launch_argv = seat_profile.launch_argv
            else:
                full_launch_argv = ()
            if resume_argv is None:
                resume_argv = seat_profile.resume_argv
            if session_capture is None:
                session_capture = seat_profile.session_capture
            if session_list_argv is None:
                session_list_argv = seat_profile.session_list_argv
            if session_id_pointer is None:
                session_id_pointer = seat_profile.session_id_pointer
            if session_cwd_pointer is None:
                session_cwd_pointer = seat_profile.session_cwd_pointer
        else:
            full_launch_argv = ()
        session_capture = session_capture or "generated_uuid"
        environment_forward = (
            seat_profile.environment_forward if seat_profile is not None else ()
        )
        if isolated is None:
            isolated = not (lead or shared_cwd)
        if shared_cwd:
            isolated = False
        project = Path(fleet.project_root)
        if _git_toplevel(project) is None:
            raise RuntimeError("working directory is not a valid Git worktree")
        # Validate and normalize the role file before any side effect: no new
        # worktree, runner plan, registry record, or tmux window may be
        # created for a missing or invalid role file.
        role_path = _resolve_role_file(project, role_id=role_id, role_path=role_path)
        # Reject credential-shaped argv and other unsafe launch data before
        # any runner-plan, bootstrap, or registry artifact is written.
        launch_data: dict[str, Any] = {
            "cli": cli,
            "launch_argv": list(full_launch_argv) if declarative else list(launch_argv),
            "resume_argv": list(resume_argv) if resume_argv else [],
            "session_capture": session_capture,
            "session_list_argv": list(session_list_argv) if session_list_argv else [],
            "session_id_pointer": session_id_pointer,
            "session_cwd_pointer": session_cwd_pointer,
            "display_name": display_name or seat_id,
            "model": model,
            "permission": permission,
            "environment_forward": list(environment_forward),
            "startup_argv": (
                list(seat_profile.startup_argv)
                if seat_profile is not None and seat_profile.startup_argv
                else []
            ),
            "permission_flags": (
                [
                    *seat_profile.permission_supervised,
                    *(seat_profile.permission_auto or ()),
                ]
                if seat_profile is not None
                else []
            ),
        }
        try:
            _assert_no_secret(launch_data)
        except RegistryError as exc:
            raise RuntimeError(
                f"unsafe launch data rejected before any artifact: {exc}"
            ) from exc
        # Values are resolved from the host environment now; they travel
        # through tmux injection and the runner's exec-time merge only.
        forwarded_environment = _resolve_forwarded_environment(environment_forward)
        worktree = working_directory or (
            project / "worktrees" / seat_id if isolated else project
        )
        self._reject_shared_cwd(worktree, shared_cwd=shared_cwd)
        worktree_preexisting = worktree.exists()
        if isolated:
            try:
                ensure_isolated_worktree(project, worktree)
            except DoctorError as exc:
                raise RuntimeError(str(exc)) from exc
        worktree.mkdir(parents=True, exist_ok=True)
        # Rollback scope: only a worktree this attempt created may be
        # removed on abort — a pre-existing path is user-owned.
        created_worktree = None if worktree_preexisting else worktree
        git_branch = _observed_branch(worktree) or "foil-demo"
        capabilities = LEAD_CAPABILITIES if lead else WORKER_CAPABILITIES
        adapter_id = (
            seat_profile.profile_id
            if seat_profile is not None
            else adapter_id_for_cli(cli)
        )
        profile = {
            "cli": cli,
            "adapter_id": adapter_id,
            "profile_id": seat_profile.profile_id if seat_profile is not None else None,
            "launch_argv": (
                list(full_launch_argv) if declarative else list(launch_argv)
            ),
            "resume_argv": list(resume_argv) if resume_argv else None,
            "session_capture": session_capture,
            "session_list_argv": list(session_list_argv) if session_list_argv else None,
            "session_id_pointer": session_id_pointer,
            "session_cwd_pointer": session_cwd_pointer,
            "display_name": display_name or seat_id,
            "capabilities": list(capabilities),
            "role_id": role_id,
            "role_path": role_path,
            "is_lead": lead,
            "model": model,
            "isolated": isolated,
            "shared_cwd": shared_cwd,
            "permission": permission,
            "executable_candidates": (
                list(seat_profile.executable_candidates)
                if seat_profile is not None
                else None
            ),
            "version_argv": (
                list(seat_profile.version_argv)
                if seat_profile is not None and seat_profile.version_argv
                else None
            ),
            "startup_argv": (
                list(seat_profile.startup_argv)
                if seat_profile is not None and seat_profile.startup_argv
                else None
            ),
            "permission_supervised": (
                list(seat_profile.permission_supervised)
                if seat_profile is not None
                else None
            ),
            "permission_auto": (
                list(seat_profile.permission_auto)
                if seat_profile is not None and seat_profile.permission_auto
                else None
            ),
            "environment_forward": list(environment_forward),
        }
        seat = SeatConfig(
            seat_id=seat_id,
            display_name=display_name or seat_id,
            usage_pool_id="default",
            working_directory=worktree,
            worktree_path=worktree,
            git_branch=git_branch,
            cli=cli,
            launch_argv=(full_launch_argv if declarative else launch_argv) or None,
            resume_argv=resume_argv,
            session_capture=session_capture,
            environment_forward=environment_forward,
        )
        pool = UsagePoolConfig("default", adapter_id, model)
        adapter = _adapter_from_profile(seat, pool, profile)
        try:
            permission_argv(adapter, permission)
        except AdapterError as exc:
            raise RuntimeError(str(exc)) from exc
        if model is None and adapter.models:
            model = adapter.models[0]
            pool = UsagePoolConfig("default", adapter_id, model)
            profile["model"] = model
        adapter_state = self._adapter_state_dir(seat_id)
        adapter_state_preexisting = adapter_state.exists()
        incarnation_id = str(uuid.uuid4())
        target = None
        try:
            # Everything from executable resolution through the registry
            # write sits inside the abort scope: a failure after worktree
            # creation and before a tmux/registry row exists must not orphan
            # the isolated worktree or adapter-state cards.
            runtime = self._with_executable(
                SeatRuntime(seat=seat, pool=pool, adapter=adapter, executable=None)
            )
            if isolated or worktree.resolve() != project.resolve():
                validate_worktree(runtime.seat)
            foil = self._bootstrap_env(
                runtime,
                incarnation_id=incarnation_id,
                profile=profile,
                lead_seat_id=seat_id if lead else fleet.lead_seat_id,
            )
            baseline: set[str] = set()
            native_session_id: str | None = None
            if runtime.adapter.session_capture.kind is CaptureKind.GENERATED_UUID:
                native_session_id = str(uuid.uuid4())
            elif (
                runtime.adapter.session_capture.kind
                is CaptureKind.COMMAND_JSON_LIST_DELTA
            ):
                baseline = self._session_ids(runtime)
            argv = self._launch_argv(runtime, native_session_id, permission=permission)
            self.audit.append(
                "spawn_attempt",
                seat_id,
                adapter_id=runtime.adapter.adapter_id,
                lead=lead,
                argv_shape=[f"arg-{index}" for index in range(len(argv))],
                environment_forwarded=len(runtime.seat.environment_forward),
            )
            target = self._spawn(
                runtime,
                argv,
                foil=foil,
                environment=forwarded_environment,
            )
            if runtime.adapter.session_capture.kind is CaptureKind.COMMAND_JSON_LIST_DELTA:
                native_session_id = self._capture_delta(runtime, baseline, wait_seconds=3)
            record = self._new_record(
                runtime,
                native_session_id,
                target,
                baseline,
                incarnation_id=incarnation_id,
                extra_extensions={"profile": profile, "model": model},
            )
            self.registry.write_seat(record)
            if lead:
                self.fleet = FleetRecord(
                    fleet_id=fleet.fleet_id,
                    project_root=fleet.project_root,
                    updated_at=_now(),
                    display_name=fleet.display_name,
                    lead_seat_id=seat_id,
                    state="running",
                    extensions=fleet.extensions,
                )
                self.fleets._write_unlocked(self.fleet)
        except Exception as exc:
            self._abort_spawn(
                seat_id,
                target,
                reason=str(exc),
                worktree=created_worktree,
                adapter_state=None if adapter_state_preexisting else adapter_state,
            )
            raise
        event_id = self.audit.append(
            "spawn_result",
            seat_id,
            result="started",
            native_session_captured=native_session_id is not None,
            lead=lead,
        )
        return [
            self._status(
                runtime,
                record,
                SeatState.WORKING,
                {"kind": "lifecycle_event", "event_id": event_id},
            )
        ]

    def _reject_shared_cwd(self, worktree: Path, *, shared_cwd: bool) -> None:
        if shared_cwd:
            return
        resolved = worktree.resolve()
        for record in self.registry.list_seats(self.fleet.fleet_id):
            existing = Path(record.working_directory).resolve()
            if existing == resolved:
                raise RuntimeError(
                    f"working directory {resolved} is already used by seat "
                    f"{record.seat_id}; pass --shared-cwd to override"
                )

    def _abort_spawn(
        self,
        seat_id: str,
        target: TmuxTarget | None,
        *,
        reason: str,
        worktree: Path | None = None,
        adapter_state: Path | None = None,
    ) -> None:
        if target is not None:
            try:
                self.tmux.stop_verified(self.fleet.fleet_id, seat_id, target)
            except Exception:
                self.tmux.abandon_window(target)
        with suppress(Exception):
            self.registry.delete_seat(self.fleet.fleet_id, seat_id)
        self.status_store.delete_snapshot(self.fleet.fleet_id, seat_id)
        removed: list[str] = []
        # Remove only artifacts this attempt created; pre-existing paths are
        # user-owned and are left for `foil doctor` to report instead.
        if adapter_state is not None and adapter_state.exists():
            with suppress(OSError):
                shutil.rmtree(adapter_state)
                removed.append("adapter-state")
        if worktree is not None and worktree.exists():
            with suppress(OSError):
                shutil.rmtree(worktree)
                removed.append("worktree")
        fleet = self.fleets.read(self.fleet.fleet_id)
        if fleet.lead_seat_id == seat_id:
            self.fleet = FleetRecord(
                fleet_id=fleet.fleet_id,
                project_root=fleet.project_root,
                updated_at=_now(),
                display_name=fleet.display_name,
                lead_seat_id=None,
                state="initialized",
                extensions=fleet.extensions,
            )
            self.fleets._write_unlocked(self.fleet)
        details: dict[str, Any] = {"result": "aborted", "reason": reason[:512]}
        if removed:
            details["cleaned"] = removed
        self.audit.append("spawn_failed", seat_id, **details)

    def _records_and_probes(
        self,
        *,
        require_any: bool = True,
    ) -> list[tuple[SeatRuntime, SeatRecord, ProbeResult]]:
        records_and_probes: list[tuple[SeatRuntime, SeatRecord, ProbeResult]] = []
        for runtime in self._runtimes():
            record = self._read_registered(runtime)
            if record is None:
                continue
            self._validate_record(runtime, record)
            records_and_probes.append(
                (
                    runtime,
                    record,
                    self.tmux.probe(
                        self.fleet.fleet_id,
                        runtime.seat.seat_id,
                        record.tmux,
                    ),
                )
            )
        if require_any and not records_and_probes:
            raise RuntimeError("no seats are registered")
        return records_and_probes

    def list_seats(self) -> dict[str, Any]:
        self.fleet = self.fleets.read(self.fleet.fleet_id)
        return {
            "fleet": self.fleet.to_dict(),
            "seats": [
                {
                    "seat_id": record.seat_id,
                    "is_lead": record.seat_id == self.fleet.lead_seat_id,
                    "cli": (record.extensions.get("profile") or {}).get("cli"),
                    "role_id": (record.extensions.get("profile") or {}).get("role_id"),
                    "isolated": bool(
                        (record.extensions.get("profile") or {}).get("isolated")
                    ),
                    "working_directory": record.working_directory,
                    "worktree_path": record.worktree_path,
                    "permission": (record.extensions.get("profile") or {}).get(
                        "permission", PERMISSION_SUPERVISED
                    ),
                }
                for record in self.registry.list_seats(self.fleet.fleet_id)
            ],
        }

    def inspect(self, seat_id: str) -> dict[str, Any]:
        record = self.registry.read_seat(self.fleet.fleet_id, seat_id)
        runtime = self._runtime_from_record(record)
        probe = self.tmux.probe(self.fleet.fleet_id, seat_id, record.tmux)
        status = self._status(
            runtime,
            record,
            self._live_state(record, probe),
            {
                "kind": "tmux_probe",
                "state": probe.state.value,
                "identity_matches": probe.identity_matches,
            },
        )
        bootstrap_path = self._adapter_state_dir(seat_id) / "bootstrap.json"
        bootstrap = None
        if bootstrap_path.is_file():
            bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        return {
            **status,
            "is_lead": seat_id == self.fleet.lead_seat_id,
            "profile": record.extensions.get("profile") or {},
            "bootstrap": bootstrap,
            "fleet": self.fleet.to_dict(),
        }

    def status(self) -> list[dict[str, Any]]:
        present = {
            runtime.seat.seat_id: (runtime, record, probe)
            for runtime, record, probe in self._records_and_probes(require_any=False)
        }
        results: list[dict[str, Any]] = []
        for configured in self._runtimes():
            entry = present.get(configured.seat.seat_id)
            if entry is None:
                results.append(
                    {
                        "seat_id": configured.seat.seat_id,
                        "usage_pool_id": configured.pool.pool_id,
                        "state": SeatState.UNKNOWN.value,
                        "registry": None,
                    }
                )
                continue
            runtime, record, probe = entry
            if (
                record.native_session_id is None
                and runtime.adapter.session_capture.kind
                is CaptureKind.COMMAND_JSON_LIST_DELTA
            ):
                runtime = self._with_executable(runtime, record)
                baseline = set(record.extensions.get("capture_baseline", []))
                captured = self._capture_delta(runtime, baseline, wait_seconds=0)
                if captured is not None:
                    record = replace(
                        record,
                        native_session_id=captured,
                        updated_at=_now(),
                    )
                    self.registry.write_seat(record)
                    self.audit.append(
                        "session_capture",
                        runtime.seat.seat_id,
                        result="captured",
                    )
            state = self._live_state(record, probe)
            results.append(
                self._status(
                    runtime,
                    record,
                    state,
                    {
                        "kind": "tmux_probe",
                        "state": probe.state.value,
                        "identity_matches": probe.identity_matches,
                    },
                )
            )
        return results

    def stop(
        self,
        *,
        seat_id: str | None = None,
        all_seats: bool = False,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.fleets.operation_lock(self.fleet.fleet_id):
            return self._stop_locked(seat_id=seat_id, all_seats=all_seats, actor=actor)

    def _stop_locked(
        self,
        *,
        seat_id: str | None,
        all_seats: bool,
        actor: str | None,
    ) -> list[dict[str, Any]]:
        self.fleets.require_lifecycle_actor(self.fleet.fleet_id, actor)
        if seat_id is None and not all_seats:
            raise RuntimeError("stop requires --seat or --all")
        entries = self._records_and_probes()
        if seat_id is not None:
            entries = [
                entry for entry in entries if entry[0].seat.seat_id == seat_id
            ]
            if not entries:
                raise RuntimeError(f"seat {seat_id} is not registered")
        for _runtime, _record, probe in entries:
            if probe.state is ProbeState.UNKNOWN or (
                probe.state is ProbeState.ALIVE and not probe.identity_matches
            ):
                raise RuntimeError("refusing stop because tmux identity is unverified")
        results: list[dict[str, Any]] = []
        for runtime, record, _probe in entries:
            stopped = self.tmux.stop_verified(
                self.fleet.fleet_id,
                runtime.seat.seat_id,
                record.tmux,
            )
            event_id = self.audit.append(
                "stop_result",
                runtime.seat.seat_id,
                result="stopped" if stopped else "already_dead",
            )
            results.append(
                self._status(
                    runtime,
                    record,
                    SeatState.EXITED,
                    {"kind": "lifecycle_event", "event_id": event_id},
                )
            )
        return results

    def remove(self, *, seat_id: str, actor: str | None = None) -> list[dict[str, Any]]:
        with self.fleets.operation_lock(self.fleet.fleet_id):
            self.fleets.require_lifecycle_actor(self.fleet.fleet_id, actor)
            stopped = self._stop_locked(seat_id=seat_id, all_seats=False, actor=actor)
            fleet = self.fleets.read(self.fleet.fleet_id)
            if fleet.lead_seat_id == seat_id:
                self.fleet = FleetRecord(
                    fleet_id=fleet.fleet_id,
                    project_root=fleet.project_root,
                    updated_at=_now(),
                    display_name=fleet.display_name,
                    lead_seat_id=None,
                    state="initialized",
                    extensions=fleet.extensions,
                )
                self.fleets._write_unlocked(self.fleet)
            self.registry.delete_seat(self.fleet.fleet_id, seat_id)
            self.status_store.delete_snapshot(self.fleet.fleet_id, seat_id)
            self.audit.append("remove_result", seat_id, result="removed")
            for item in stopped:
                item["state"] = "removed"
            return stopped

    def resume(
        self,
        *,
        seat_id: str | None = None,
        force_fresh: bool = False,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.fleets.operation_lock(self.fleet.fleet_id):
            return self._resume_locked(
                seat_id=seat_id, force_fresh=force_fresh, actor=actor
            )

    def _resume_locked(
        self,
        *,
        seat_id: str | None,
        force_fresh: bool,
        actor: str | None,
    ) -> list[dict[str, Any]]:
        self.fleets.require_lifecycle_actor(self.fleet.fleet_id, actor)
        entries = self._records_and_probes()
        results: list[dict[str, Any]] = []
        for runtime, record, probe in entries:
            if seat_id is not None and runtime.seat.seat_id != seat_id:
                continue
            tmux_state = {
                ProbeState.ALIVE: TmuxProbeState.ALIVE,
                ProbeState.DEAD: TmuxProbeState.DEAD,
                ProbeState.UNKNOWN: TmuxProbeState.UNKNOWN,
            }[probe.state]
            decision = resolve_resume(
                ResumeEvidence(
                    tmux_state=tmux_state,
                    force_fresh=force_fresh,
                    tmux_identity_matches=(
                        probe.identity_matches
                        if probe.state is ProbeState.ALIVE
                        else None
                    ),
                    native_session_id=record.native_session_id,
                    native_resume_supported=runtime.adapter.resume.supported,
                )
            )
            self.audit.append(
                "resume_decision",
                runtime.seat.seat_id,
                action=decision.action.value,
                reason=decision.reason,
            )
            if decision.action is ResumeAction.BLOCKED:
                raise RuntimeError(f"seat {runtime.seat.seat_id} resume is blocked")
            if decision.action is ResumeAction.REVIVE_TMUX:
                extensions = dict(record.extensions)
                extensions.pop("operator_state", None)
                record = replace(record, extensions=extensions, updated_at=_now())
                self.registry.write_seat(record)
                event_id = self.audit.append(
                    "resume_result",
                    runtime.seat.seat_id,
                    result="revived",
                )
                result = self._status(
                    runtime,
                    record,
                    SeatState.WORKING,
                    {"kind": "lifecycle_event", "event_id": event_id},
                )
            else:
                if (
                    decision.action is ResumeAction.START_FRESH
                    and probe.state is ProbeState.ALIVE
                ):
                    if not probe.identity_matches:
                        raise RuntimeError(
                            f"seat {runtime.seat.seat_id} resume --fresh refused: "
                            "tmux identity is unverified"
                        )
                    self.tmux.stop_verified(
                        self.fleet.fleet_id,
                        runtime.seat.seat_id,
                        record.tmux,
                    )
                runtime = self._with_executable(runtime, record)
                baseline: set[str] = set()
                native_session_id = record.native_session_id
                previous_incarnation_id = record.previous_incarnation_id
                profile = dict(record.extensions.get("profile") or {})
                permission = str(profile.get("permission") or PERMISSION_SUPERVISED)
                if decision.action is ResumeAction.RESUME_NATIVE:
                    assert runtime.adapter.resume.argv is not None
                    try:
                        extra = permission_argv(runtime.adapter, permission)
                    except AdapterError as exc:
                        raise RuntimeError(str(exc)) from exc
                    argv = self._expanded(
                        runtime,
                        runtime.adapter.resume.argv + extra,
                        native_session_id,
                    )
                    incarnation_id = record.incarnation_id
                else:
                    if (
                        runtime.adapter.session_capture.kind
                        is CaptureKind.GENERATED_UUID
                    ):
                        native_session_id = str(uuid.uuid4())
                    elif (
                        runtime.adapter.session_capture.kind
                        is CaptureKind.COMMAND_JSON_LIST_DELTA
                    ):
                        baseline = self._session_ids(runtime)
                        native_session_id = None
                    argv = self._launch_argv(
                        runtime,
                        native_session_id,
                        permission=permission,
                    )
                    incarnation_id = str(uuid.uuid4())
                    previous_incarnation_id = record.incarnation_id
                forwarded = _resolve_forwarded_environment(
                    runtime.seat.environment_forward
                )
                foil = self._bootstrap_env(
                    runtime, incarnation_id=incarnation_id, profile=profile
                )
                target = self._spawn(runtime, argv, foil=foil, environment=forwarded)
                if (
                    decision.action is ResumeAction.START_FRESH
                    and runtime.adapter.session_capture.kind
                    is CaptureKind.COMMAND_JSON_LIST_DELTA
                ):
                    native_session_id = self._capture_delta(
                        runtime, baseline, wait_seconds=3
                    )
                extra = {
                    key: value
                    for key, value in record.extensions.items()
                    if key in {"profile", "model"}
                }
                record = self._new_record(
                    runtime,
                    native_session_id,
                    target,
                    baseline,
                    extra_extensions=extra,
                    incarnation_id=incarnation_id,
                    previous_incarnation_id=previous_incarnation_id,
                )
                self.registry.write_seat(record)
                event_id = self.audit.append(
                    "resume_result",
                    runtime.seat.seat_id,
                    result=decision.action.value,
                )
                result = self._status(
                    runtime,
                    record,
                    SeatState.WORKING,
                    {"kind": "lifecycle_event", "event_id": event_id},
                )
            result["action"] = decision.action.value
            result["reason"] = decision.reason
            results.append(result)
        if seat_id is not None and not results:
            raise RuntimeError(f"seat {seat_id} is not registered")
        return results

    def set_state(self, seat_id: str, state: str) -> list[dict[str, Any]]:
        allowed = {SeatState.WAITING, SeatState.IDLE, SeatState.WORKING}
        try:
            desired = SeatState(state)
        except ValueError as exc:
            raise RuntimeError(f"unknown state {state!r}") from exc
        if desired not in allowed:
            raise RuntimeError(f"unknown state {state!r}")
        found = False
        for runtime, record, _probe in self._records_and_probes():
            if runtime.seat.seat_id != seat_id:
                continue
            found = True
            extensions = dict(record.extensions)
            if desired is SeatState.WORKING:
                extensions.pop("operator_state", None)
            else:
                extensions["operator_state"] = desired.value
            self.registry.write_seat(
                replace(record, extensions=extensions, updated_at=_now())
            )
        if not found:
            raise RuntimeError(f"seat {seat_id} is not registered")
        return self.status()

    def _live_state(self, record: SeatRecord, probe: ProbeResult) -> SeatState:
        if probe.state is ProbeState.DEAD:
            return SeatState.EXITED
        if not (probe.state is ProbeState.ALIVE and probe.identity_matches):
            return SeatState.BLOCKED
        operator_state = record.extensions.get("operator_state")
        if operator_state in {
            SeatState.WAITING.value,
            SeatState.IDLE.value,
            SeatState.WORKING.value,
        }:
            return SeatState(operator_state)
        return SeatState.WORKING


def _normalize_remainder(argv: tuple[str, ...] | None) -> tuple[str, ...]:
    items = list(argv or ())
    if items[:1] == ["--"]:
        items = items[1:]
    return tuple(items)


def _resolve_role_file(
    project: Path,
    *,
    role_id: str | None,
    role_path: str | None,
) -> str | None:
    """Resolve the role file to an absolute existing regular file.

    Fails closed: a requested role that is missing or not a regular file is
    an error, never a silent spawn without a role.
    """

    if role_path is None and role_id:
        try:
            _validate_id(role_id, "role_id")
        except RegistryError as exc:
            raise RuntimeError(f"role_id is not a safe stable ID: {role_id}") from exc
        candidate = project / ".foil" / "roles" / f"{role_id}.toml"
        if not candidate.is_file():
            raise RuntimeError(
                f"role library has no file for role {role_id}: expected {candidate}"
            )
        role_path = str(candidate)
    if role_path is None:
        return None
    path = Path(role_path).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"role file does not exist: {role_path}") from exc
    if not resolved.is_file():
        raise RuntimeError(f"role file must be a regular file: {role_path}")
    return str(resolved)


def _adapter_from_profile(
    seat: SeatConfig,
    pool: UsagePoolConfig,
    profile: dict[str, Any] | None = None,
) -> AdapterRecord:
    profile = profile or {}
    custom_argv = tuple(profile.get("launch_argv") or seat.launch_argv or ())
    adapter_id = (
        profile.get("adapter_id") or pool.adapter_id or adapter_id_for_cli(seat.cli)
    )
    # Shipped adapters stay the convenient preset; a declarative profile file
    # always builds its own record from the same framework.
    if adapter_id and not custom_argv and not profile.get("profile_id"):
        try:
            return load_builtin_adapter(str(adapter_id))
        except AdapterError as exc:
            raise RuntimeError(f"adapter is unavailable: {adapter_id}") from exc
    return _profile_adapter(seat, pool, profile)


def _profile_adapter(
    seat: SeatConfig,
    pool: UsagePoolConfig,
    profile: dict[str, Any] | None = None,
) -> AdapterRecord:
    profile = profile or {}
    declarative = bool(profile.get("profile_id"))
    stored_launch = tuple(profile.get("launch_argv") or seat.launch_argv or ())
    if declarative:
        # Declarative argv includes argv[0], like shipped adapter records.
        launch = stored_launch
        candidates = tuple(profile.get("executable_candidates") or ())
    else:
        launch = (seat.cli, *stored_launch) if seat.cli and stored_launch else ()
        candidates = (seat.cli,) if seat.cli else ()
    if not seat.cli or not launch:
        raise RuntimeError(f"seat {seat.seat_id} has no profile-owned launch argv")
    kind = CaptureKind(
        profile.get("session_capture")
        or seat.session_capture
        or CaptureKind.GENERATED_UUID.value
    )
    list_argv = profile.get("session_list_argv")
    models = (pool.model,) if pool.model else ("unspecified",)
    stored_resume = tuple(profile.get("resume_argv") or seat.resume_argv or ())
    resume = stored_resume if declarative or not stored_resume else (seat.cli, *stored_resume)
    version_argv = tuple(profile.get("version_argv") or (seat.cli, "--version"))
    startup_argv = tuple(profile["startup_argv"]) if profile.get("startup_argv") else None
    supervised = tuple(profile.get("permission_supervised") or ())
    auto = tuple(profile["permission_auto"]) if profile.get("permission_auto") else None
    return AdapterRecord(
        adapter_id=str(profile.get("adapter_id") or seat.cli),
        observed_version="profile",
        models=models,
        skill="skills/controller/SKILL.md",
        executable=ExecutableSpec(
            candidates=candidates,
            version_argv=version_argv,
        ),
        launch=LaunchSpec(argv=launch),
        resume=ResumeSpec(
            supported=bool(resume),
            argv=resume or None,
        ),
        session_capture=SessionCaptureSpec(
            kind=kind,
            argv=tuple(list_argv) if list_argv else None,
            id_pointer=profile.get("session_id_pointer"),
            cwd_pointer=profile.get("session_cwd_pointer"),
        ),
        startup=StartupSpec(argv=startup_argv),
        permissions=PermissionsSpec(supervised=supervised, auto=auto),
    )


def _resolve_forwarded_environment(
    names: tuple[str, ...],
) -> dict[str, str] | None:
    """Resolve declared variable names from the host environment at launch.

    Values travel through tmux injection and the runner's exec-time merge;
    they are never written to Foil state. Host variables that are absent are
    skipped so optional credentials stay opt-in.
    """

    if not names:
        return None
    resolved: dict[str, str] = {}
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        if not _SAFE_VALUE.fullmatch(value):
            raise RuntimeError(f"forwarded environment value is unsafe: {name}")
        resolved[name] = value
    return resolved


def _git_toplevel(start: Path) -> Path | None:
    try:
        return Path(_git_output(start, "rev-parse", "--show-toplevel")).resolve()
    except RuntimeError:
        return None


def _observed_branch(cwd: Path) -> str | None:
    try:
        return _git_output(cwd, "branch", "--show-current")
    except RuntimeError:
        return None


def _git_tracks(cwd: Path, name: str) -> bool:
    """True when Git tracks name under cwd; an unknown answer refuses overwrite."""

    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "ls-files", "--error-unmatch", "--", name],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode == 0


def _worker_instructions(payload: dict[str, Any], seat_id: str) -> str:
    state = payload["state_dir"]
    fleet = payload["fleet_id"]
    capabilities = ", ".join(payload.get("capabilities") or [])
    role_path = payload.get("role_path")
    if role_path:
        role_block = (
            f"Role file: `{role_path}`.\n"
            f"Before beginning any work, read the role file at `{role_path}`: "
            "it is the validated `role_path` recorded in `bootstrap.json`.\n"
        )
    else:
        role_block = (
            "Role file: (none); no validated `role_path` was recorded in "
            "`bootstrap.json`.\n"
        )
    if payload.get("is_lead"):
        team_block = (
            "Team responsibilities: you are the fleet lead, so you own fleet "
            "membership — spawn, stop, and remove seats — along with "
            "decomposition, progress intervention, independent review, and "
            "final synthesis. Workers must not spawn, stop, or remove seats; "
            "they return evidence and results to you.\n"
        )
    else:
        team_block = (
            "Team responsibilities: you are a worker seat. Do not spawn, "
            "stop, or remove seats; only the lead manages fleet membership. "
            "Return your assigned work and its evidence to the lead.\n"
        )
    return (
        "# Foil seat\n\n"
        f"You are seat `{seat_id}` in fleet `{fleet}`.\n"
        f"Lead seat: `{payload.get('lead_seat_id') or 'operator'}`.\n"
        f"{role_block}"
        f"Capabilities: {capabilities}.\n\n"
        "On start and whenever you are woken, poll your mailbox and "
        "acknowledge only after you have read the message. Do not treat "
        "acknowledgement as task completion. Reply with `foil send-message` "
        "or a shared notepad/file.\n\n"
        "```sh\n"
        f"foil message-status --state-dir {state} --fleet {fleet} "
        f"--seat {seat_id} --message MESSAGE_ID\n"
        f"foil ack-message --state-dir {state} --fleet {fleet} "
        f"--seat {seat_id} --message MESSAGE_ID --actor {seat_id}\n"
        "```\n"
        "A role or persona file is a specialist lens scoped to this seat's "
        "assigned work: apply its identity, methods, quality bar, and "
        "deliverable formats only to the assignment; translate \"you must "
        "deliver X\" into \"produce X for this assignment and return it to "
        "the lead\"; do not assume ownership of the whole project. User, "
        "project, and task instructions override persona-specific stacks, "
        "paths, tools, examples, quotas, and workflows unless explicitly "
        "selected.\n"
        f"{team_block}"
    )
