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

    def launch(
        self,
        *,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_name: str,
        working_directory: Path,
        runner_argv: list[str],
    ) -> TmuxTarget:
        """Create a detached verified window running a fixed Foil runner argv."""

        target = f"{session_name}:{window_name}"
        created_session = False
        if self._session_exists(session_name):
            if self._session_fleet_marker(session_name) != fleet_id:
                raise TmuxError("existing tmux session has a mismatched fleet marker")
            self._required(
                [
                    "new-window",
                    "-d",
                    "-t",
                    session_name,
                    "-n",
                    window_name,
                    "-c",
                    str(working_directory),
                    *runner_argv,
                ],
                "window launch",
            )
        else:
            self._required(
                [
                    "new-session",
                    "-d",
                    "-s",
                    session_name,
                    "-n",
                    window_name,
                    "-c",
                    str(working_directory),
                    *runner_argv,
                ],
                "session launch",
            )
            created_session = True
        try:
            self._required(
                ["set-option", "-t", session_name, "@foil-fleet-id", fleet_id],
                "session marker update",
            )
            self._required(
                ["set-option", "-w", "-t", target, "@foil-seat-id", seat_id],
                "window marker update",
            )
            output = self._required(
                ["display-message", "-p", "-t", target, _PROBE_FORMAT],
                "identity capture",
            ).strip()
            parts = output.split("\t")
            if len(parts) != 4:
                raise TmuxError("tmux returned malformed identity data")
            session_id, window_id, observed_fleet, observed_seat = parts
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
                self._run(["kill-window", "-t", target])
            raise

    def probe(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> ProbeResult:
        result = self._run(
            [
                "display-message",
                "-p",
                "-t",
                f"{target.session_name}:{target.window_name}",
                _PROBE_FORMAT,
            ]
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
        self._required(
            ["kill-window", "-t", f"{target.session_name}:{target.window_name}"],
            "verified window stop",
        )
        return True

    def abandon_window(self, target: TmuxTarget) -> None:
        """Best-effort kill of a window created during a failed spawn."""

        self._run(["kill-window", "-t", f"{target.session_name}:{target.window_name}"])
