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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.adapters import (
    AdapterError,
    AdapterRecord,
    CaptureKind,
    expand_argv,
    load_adapter,
    load_builtin_adapter,
)
from foil.naming import tmux_session_name, tmux_window_name
from foil.registry import (
    RegistryStore,
    SeatRecord,
    TmuxTarget,
    _assert_no_secret,
    _atomic_write_json,
)
from foil.resume import ResumeAction, ResumeEvidence, TmuxProbeState, resolve_resume
from foil.runtime_config import FleetConfig, SeatConfig, UsagePoolConfig
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


def _safe_environment(*, executable: str | None = None) -> dict[str, str]:
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
        config: FleetConfig,
        state_root: Path | str,
        *,
        tmux: TmuxController | None = None,
    ):
        self.config = config
        self.state_root = Path(state_root)
        self.registry = RegistryStore(self.state_root)
        self.status_store = PollStatusReader(self.state_root)
        self.catalog = AdapterCatalog(config.adapter_paths)
        self.tmux = tmux or TmuxController()
        self.audit = AuditLog(self.state_root, config.fleet_id)

    def _runtimes(self) -> list[SeatRuntime]:
        runtimes: list[SeatRuntime] = []
        for seat in self.config.seats:
            pool = self.config.pool_for(seat)
            adapter = self.catalog.load(pool.adapter_id)
            if pool.model not in adapter.models:
                raise RuntimeError(
                    f"model {pool.model!r} is not declared by adapter {pool.adapter_id}"
                )
            try:
                executable = _resolve_executable(adapter)
            except RuntimeError:
                executable = None
            runtimes.append(
                SeatRuntime(
                    seat=seat,
                    pool=pool,
                    adapter=adapter,
                    executable=executable,
                )
            )
        return runtimes

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
            / self.config.fleet_id
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
            "model": runtime.pool.model,
            "seat_id": runtime.seat.seat_id,
            "working_directory": str(runtime.seat.working_directory),
        }
        if native_session_id is not None:
            values["native_session_id"] = native_session_id
        return values

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

    def _write_plan(self, runtime: SeatRuntime, argv: list[str]) -> Path:
        plan_dir = (
            self.state_root
            / "v1"
            / "fleets"
            / self.config.fleet_id
            / "runner-plans"
        )
        plan = plan_dir / f"{runtime.seat.seat_id}.json"
        return _atomic_write_json(
            plan,
            {
                "argv": argv,
                "cwd": str(runtime.seat.working_directory),
                "env": _safe_environment(executable=runtime.executable),
            },
        )

    def _spawn(
        self,
        runtime: SeatRuntime,
        argv: list[str],
    ) -> TmuxTarget:
        plan = self._write_plan(runtime, argv)
        return self.tmux.launch(
            fleet_id=self.config.fleet_id,
            seat_id=runtime.seat.seat_id,
            session_name=tmux_session_name(
                self.config.display_name,
                self.config.fleet_id,
            ),
            window_name=tmux_window_name(
                runtime.seat.display_name,
                runtime.seat.seat_id,
            ),
            working_directory=runtime.seat.working_directory,
            runner_argv=[sys.executable, "-m", "foil.runner", str(plan)],
        )

    def _status(
        self,
        runtime: SeatRuntime,
        record: SeatRecord,
        state: SeatState,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = StatusSnapshot(
            fleet_id=self.config.fleet_id,
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
    ) -> SeatRecord:
        return SeatRecord(
            fleet_id=self.config.fleet_id,
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
            extensions={
                "adapter_schema_version": runtime.adapter.schema_version,
                "capture_baseline": sorted(baseline),
                "model": runtime.pool.model,
                **(
                    {"resolved_executable": runtime.executable}
                    if runtime.executable is not None
                    else {}
                ),
            },
        )

    def _validate_record(self, runtime: SeatRuntime, record: SeatRecord) -> None:
        expected = (
            record.fleet_id == self.config.fleet_id
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

    def launch(self) -> list[dict[str, Any]]:
        runtimes = [self._with_executable(runtime) for runtime in self._validate_runtimes()]
        for runtime in runtimes:
            try:
                self.registry.read_seat(self.config.fleet_id, runtime.seat.seat_id)
            except FileNotFoundError:
                continue
            raise RuntimeError(f"seat {runtime.seat.seat_id} is already registered")

        results: list[dict[str, Any]] = []
        for runtime in runtimes:
            baseline: set[str] = set()
            native_session_id: str | None = None
            if runtime.adapter.session_capture.kind is CaptureKind.GENERATED_UUID:
                native_session_id = str(uuid.uuid4())
            elif (
                runtime.adapter.session_capture.kind
                is CaptureKind.COMMAND_JSON_LIST_DELTA
            ):
                baseline = self._session_ids(runtime)
            argv = self._expanded(runtime, runtime.adapter.launch.argv, native_session_id)
            self.audit.append(
                "launch_attempt",
                runtime.seat.seat_id,
                adapter_id=runtime.adapter.adapter_id,
                usage_pool_id=runtime.pool.pool_id,
                argv_shape=[f"arg-{index}" for index in range(len(argv))],
            )
            target = self._spawn(runtime, argv)
            if (
                runtime.adapter.session_capture.kind
                is CaptureKind.COMMAND_JSON_LIST_DELTA
            ):
                native_session_id = self._capture_delta(runtime, baseline, wait_seconds=3)
            record = self._new_record(
                runtime,
                native_session_id,
                target,
                baseline,
            )
            self.registry.write_seat(record)
            event_id = self.audit.append(
                "launch_result",
                runtime.seat.seat_id,
                result="started",
                native_session_captured=native_session_id is not None,
            )
            results.append(
                self._status(
                    runtime,
                    record,
                    SeatState.WORKING,
                    {"kind": "lifecycle_event", "event_id": event_id},
                )
            )
        return results

    def _records_and_probes(
        self,
    ) -> list[tuple[SeatRuntime, SeatRecord, ProbeResult]]:
        runtimes = self._validate_runtimes()
        records_and_probes: list[tuple[SeatRuntime, SeatRecord, ProbeResult]] = []
        for runtime in runtimes:
            try:
                record = self.registry.read_seat(
                    self.config.fleet_id, runtime.seat.seat_id
                )
            except FileNotFoundError as exc:
                raise RuntimeError(f"seat {runtime.seat.seat_id} is not registered") from exc
            self._validate_record(runtime, record)
            records_and_probes.append(
                (
                    runtime,
                    record,
                    self.tmux.probe(
                        self.config.fleet_id,
                        runtime.seat.seat_id,
                        record.tmux,
                    ),
                )
            )
        return records_and_probes

    def status(self) -> list[dict[str, Any]]:
        entries = self._records_and_probes()
        results: list[dict[str, Any]] = []
        for runtime, record, probe in entries:
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
            if probe.state is ProbeState.ALIVE and probe.identity_matches:
                state = SeatState.WORKING
            elif probe.state is ProbeState.DEAD:
                state = SeatState.EXITED
            else:
                state = SeatState.BLOCKED
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

    def stop(self) -> list[dict[str, Any]]:
        entries = self._records_and_probes()
        for _runtime, _record, probe in entries:
            if probe.state is ProbeState.UNKNOWN or (
                probe.state is ProbeState.ALIVE and not probe.identity_matches
            ):
                raise RuntimeError("refusing stop because tmux identity is unverified")
        results: list[dict[str, Any]] = []
        for runtime, record, _probe in entries:
            stopped = self.tmux.stop_verified(
                self.config.fleet_id,
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

    def resume(self) -> list[dict[str, Any]]:
        entries = self._records_and_probes()
        results: list[dict[str, Any]] = []
        for runtime, record, probe in entries:
            tmux_state = {
                ProbeState.ALIVE: TmuxProbeState.ALIVE,
                ProbeState.DEAD: TmuxProbeState.DEAD,
                ProbeState.UNKNOWN: TmuxProbeState.UNKNOWN,
            }[probe.state]
            decision = resolve_resume(
                ResumeEvidence(
                    tmux_state=tmux_state,
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
                runtime = self._with_executable(runtime, record)
                baseline: set[str] = set()
                native_session_id = record.native_session_id
                if decision.action is ResumeAction.RESUME_NATIVE:
                    assert runtime.adapter.resume.argv is not None
                    argv = self._expanded(
                        runtime,
                        runtime.adapter.resume.argv,
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
                    argv = self._expanded(
                        runtime,
                        runtime.adapter.launch.argv,
                        native_session_id,
                    )
                    incarnation_id = str(uuid.uuid4())
                target = self._spawn(runtime, argv)
                if (
                    decision.action is ResumeAction.START_FRESH
                    and runtime.adapter.session_capture.kind
                    is CaptureKind.COMMAND_JSON_LIST_DELTA
                ):
                    native_session_id = self._capture_delta(
                        runtime, baseline, wait_seconds=3
                    )
                record = self._new_record(
                    runtime,
                    native_session_id,
                    target,
                    baseline,
                    incarnation_id=incarnation_id,
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
        return results
