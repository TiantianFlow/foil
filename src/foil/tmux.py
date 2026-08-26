"""Verified tmux lifecycle boundary (CAP-017, CAP-019, CAP-029–CAP-031)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from foil.registry import TmuxTarget

_PROBE_FORMAT = (
    "#{session_id}\t#{window_id}\t"
    "#{@foil-fleet-id}\t#{@foil-seat-id}"
)
_IDENTITY_FORMAT = "#{session_id}\t#{window_id}\t#{window_name}"


class TmuxError(RuntimeError):
    """A tmux operation could not be completed safely."""


class ProbeState(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    state: ProbeState
    identity_matches: bool | None
    observed: TmuxTarget | None = None


class TmuxController:
    def __init__(self, executable: str = "tmux", timeout: float = 10.0):
        self.executable = executable
        self.timeout = timeout

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.executable, *argv],
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TmuxError("tmux command could not be executed") from exc

    def _required(self, argv: list[str], operation: str) -> str:
        result = self._run(argv)
        if result.returncode != 0:
            raise TmuxError(f"tmux {operation} failed")
        if len(result.stdout) > 64 * 1024 or len(result.stderr) > 64 * 1024:
            raise TmuxError(f"tmux {operation} output exceeded the limit")
        return result.stdout

    def _session_exists(self, session_name: str) -> bool:
        result = self._run(["has-session", "-t", session_name])
        return result.returncode == 0

    def _session_fleet_marker(self, session_name: str) -> str | None:
        result = self._run(
            ["show-options", "-qv", "-t", session_name, "@foil-fleet-id"]
        )
        return result.stdout.strip() if result.returncode == 0 else None

    def _exact_window_id(self, target: TmuxTarget) -> str | None:
        """Resolve the recorded window to its exact id, never a name fallback.

        A stored ``@N`` window id is already exact. A name-only target is
        resolved through tmux and kept only when the resolved window carries
        the exact recorded name (and session id, when one was recorded); a
        stale name that tmux would fall back onto another window yields None.
        """

        if target.window_id:
            return target.window_id
        result = self._run(
            [
                "display-message",
                "-p",
                "-t",
                f"{target.session_name}:{target.window_name}",
                _IDENTITY_FORMAT,
            ]
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("\t")
        if len(parts) != 3:
            return None
        session_id, window_id, window_name = parts
        if window_name != target.window_name:
            return None
        if target.session_id and session_id != target.session_id:
            return None
        if not window_id.startswith("@"):
            return None
        return window_id

    def launch(
        self,
        *,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_name: str,
        working_directory: Path,
        runner_argv: list[str],
        environment: dict[str, str] | None = None,
    ) -> TmuxTarget:
        """Create a detached verified window running a fixed Foil runner argv.

        ``environment`` entries are injected with tmux ``-e`` so selected host
        variables reach the runner process without being persisted in any
        Foil state file. The new window's exact id is captured at creation
        (``-P``/``-F``) so duplicate window names can never alias the seat.
        """

        env_args: list[str] = []
        for key, value in (environment or {}).items():
            env_args.extend(["-e", f"{key}={value}"])
        created_session = False
        if self._session_exists(session_name):
            if self._session_fleet_marker(session_name) != fleet_id:
                raise TmuxError("existing tmux session has a mismatched fleet marker")
            created = self._required(
                [
                    "new-window",
                    "-d",
                    "-P",
                    "-F",
                    "#{session_id}\t#{window_id}",
                    "-t",
                    session_name,
                    "-n",
                    window_name,
                    "-c",
                    str(working_directory),
                    *env_args,
                    *runner_argv,
                ],
                "window launch",
            )
        else:
            created = self._required(
                [
                    "new-session",
                    "-d",
                    "-P",
                    "-F",
                    "#{session_id}\t#{window_id}",
                    "-s",
                    session_name,
                    "-n",
                    window_name,
                    "-c",
                    str(working_directory),
                    *env_args,
                    *runner_argv,
                ],
                "session launch",
            )
            created_session = True
        parts = created.strip().split("\t")
        if len(parts) != 2 or not parts[1].startswith("@"):
            if created_session:
                self._run(["kill-session", "-t", session_name])
            raise TmuxError("tmux returned malformed identity data")
        session_id, window_id = parts
        try:
            self._required(
                ["set-option", "-t", session_name, "@foil-fleet-id", fleet_id],
                "session marker update",
            )
            self._required(
                ["set-option", "-w", "-t", window_id, "@foil-seat-id", seat_id],
                "window marker update",
            )
            output = self._required(
                ["display-message", "-p", "-t", window_id, _PROBE_FORMAT],
                "identity capture",
            ).strip()
            observed = output.split("\t")
            if len(observed) != 4:
                raise TmuxError("tmux returned malformed identity data")
            observed_session, observed_window, observed_fleet, observed_seat = observed
            if observed_session != session_id or observed_window != window_id:
                raise TmuxError("tmux identity capture drifted")
            if observed_fleet != fleet_id or observed_seat != seat_id:
                raise TmuxError("tmux markers did not persist")
            return TmuxTarget(
                session_name=session_name,
                window_name=window_name,
                session_id=session_id,
                window_id=window_id,
            )
        except Exception:
            if created_session:
                self._run(["kill-session", "-t", session_name])
            else:
                self._run(["kill-window", "-t", window_id])
            raise

    def probe(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> ProbeResult:
        # Exact window identity only: a stored @N id, or a name resolved with
        # an exact name match. tmux falls a stale session:name target back
        # onto the session's current window, which must report DEAD instead.
        exact = self._exact_window_id(target)
        if exact is None:
            return ProbeResult(ProbeState.DEAD, False)
        result = self._run(
            ["display-message", "-p", "-t", exact, _PROBE_FORMAT]
        )
        if result.returncode != 0:
            return ProbeResult(ProbeState.DEAD, False)
        parts = result.stdout.strip().split("\t")
        if not any(parts):
            return ProbeResult(ProbeState.DEAD, False)
        if len(parts) != 4:
            return ProbeResult(ProbeState.UNKNOWN, None)
        session_id, window_id, observed_fleet, observed_seat = parts
        observed = TmuxTarget(
            session_name=target.session_name,
            window_name=target.window_name,
            session_id=session_id,
            window_id=window_id,
        )
        matches = (
            observed_fleet == fleet_id
            and observed_seat == seat_id
            and session_id == target.session_id
            and window_id == target.window_id
        )
        return ProbeResult(ProbeState.ALIVE, matches, observed)

    def list_marked_windows(self, fleet_id: str) -> list[dict[str, str]]:
        """Return Foil-marked windows that claim this fleet."""

        result = self._run(
            [
                "list-windows",
                "-a",
                "-F",
                "#{session_name}\t#{window_name}\t#{@foil-fleet-id}\t"
                "#{@foil-seat-id}\t#{session_id}\t#{window_id}",
            ]
        )
        if result.returncode != 0:
            return []
        marked: list[dict[str, str]] = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 6:
                continue
            session_name, window_name, observed_fleet, seat_id, session_id, window_id = (
                parts
            )
            if observed_fleet != fleet_id or not seat_id:
                continue
            marked.append(
                {
                    "session_name": session_name,
                    "window_name": window_name,
                    "seat_id": seat_id,
                    "session_id": session_id,
                    "window_id": window_id,
                }
            )
        return marked

    def stop_verified(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        probe = self.probe(fleet_id, seat_id, target)
        if probe.state is ProbeState.DEAD:
            return False
        if probe.state is not ProbeState.ALIVE or not probe.identity_matches:
            raise TmuxError("refusing to stop an unverified tmux target")
        kill_target = target.window_id or (
            probe.observed.window_id if probe.observed is not None else None
        )
        if not kill_target:
            raise TmuxError("refusing to stop an unverified tmux target")
        self._required(
            ["kill-window", "-t", kill_target],
            "verified window stop",
        )
        return True

    def abandon_window(self, target: TmuxTarget) -> None:
        """Best-effort kill of a window created during a failed spawn.

        Targets the exact recorded window id only; a stale name must never
        fall back onto another live window.
        """

        exact = self._exact_window_id(target)
        if exact is not None:
            self._run(["kill-window", "-t", exact])
