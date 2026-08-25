"""End-to-end operator harness for Foil user journeys.

CI does not have authenticated Grok or OpenCode. The harness puts PATH shims
that honor the shipped adapter argv contracts in front of `foil`, then drives
the same commands an operator or controller skill would: init, git, spawn,
status, mailbox, resume, stop.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from foil.delivery import TmuxWakeService
from foil.naming import tmux_session_name

E2E_DIR = Path(__file__).resolve().parent
FAKE_CLI = E2E_DIR / "fake_cli.py"
FLEET_DISPLAY_NAME = "Foil fleet"


def require_tmux() -> str:
    executable = shutil.which("tmux")
    if executable is None:
        raise RuntimeError("tmux is required for Foil end-to-end tests")
    return executable


@dataclass(frozen=True, slots=True)
class OperatorResult:
    completed: subprocess.CompletedProcess[str]

    @property
    def returncode(self) -> int:
        return self.completed.returncode

    @property
    def stdout(self) -> str:
        return self.completed.stdout

    @property
    def stderr(self) -> str:
        return self.completed.stderr

    def json(self) -> Any:
        if self.returncode != 0:
            raise AssertionError(
                f"foil exited {self.returncode}\nstdout: {self.stdout}\nstderr: {self.stderr}"
            )
        return json.loads(self.stdout)


class OperatorFleet:
    """One isolated project, state root, and agent PATH — the operator machine."""

    def __init__(self, tmp_path: Path, *, live: bool = False):
        self.root = tmp_path / "operator"
        self.project = self.root / "project"
        self.state = self.root / "state"
        self.bin = self.root / "bin"
        self.agent_home = self.root / "agent-home"
        self.live = live
        self.fleet_id = ""
        self.roles_path = self.project / ".foil" / "roles"
        self._tmux = require_tmux()
        self._keeper = ""

    def bootstrap(self) -> None:
        self.project.mkdir(parents=True)
        self.state.mkdir()
        self.agent_home.mkdir()
        if not self.live:
            self._install_shims()
        self._start_keeper()

    def _install_shims(self) -> None:
        self.bin.mkdir()
        source = FAKE_CLI.read_bytes()
        for name in ("grok", "opencode"):
            target = self.bin / name
            target.write_bytes(source)
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
        (self.bin / "agent-home").write_text(str(self.agent_home), encoding="utf-8")

    def _start_keeper(self) -> None:
        """Keep the tmux server occupied so Foil session IDs do not reset to $0.

        GitHub Actions starts a virgin tmux server. Killing the only session
        makes the next `new-session` reuse `$0`, which is not how a machine
        that already has tmux running behaves.
        """
        self._keeper = f"foil-e2e-keeper-{uuid.uuid4().hex[:12]}"
        created = subprocess.run(
            [self._tmux, "new-session", "-d", "-s", self._keeper, "sleep", "3600"],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            raise RuntimeError(f"could not start tmux keeper: {created.stderr}")

    def env(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment["FOIL_STATE_DIR"] = str(self.state)
        environment["FOIL_E2E_AGENT_HOME"] = str(self.agent_home)
        if not self.live:
            environment["PATH"] = f"{self.bin}{os.pathsep}{environment.get('PATH', '')}"
        return environment

    def foil(
        self,
        *args: str,
        timeout: float = 60.0,
        env: dict[str, str] | None = None,
    ) -> OperatorResult:
        completed = subprocess.run(
            [sys.executable, "-m", "foil", *args],
            cwd=self.project,
            env=self.env() if env is None else env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return OperatorResult(completed)

    def fleet_flags(self) -> list[str]:
        return ["--state-dir", str(self.state), "--fleet", self.fleet_id]

    def lifecycle(self, command: str, *extra: str, **kwargs: Any) -> OperatorResult:
        if command == "stop":
            return self.foil(
                "seat",
                "stop",
                *self.fleet_flags(),
                "--json",
                "--all",
                *extra,
                **kwargs,
            )
        return self.foil(command, *self.fleet_flags(), "--json", *extra, **kwargs)

    def spawn(
        self,
        seat: str,
        cli: str,
        *,
        lead: bool = False,
        isolated: bool = False,
        role: str | None = None,
        extra: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> OperatorResult:
        args = [
            "seat",
            "spawn",
            *self.fleet_flags(),
            "--json",
            "--seat",
            seat,
            "--cli",
            cli,
        ]
        if lead:
            args.append("--lead")
        if isolated:
            args.append("--isolated")
        if role:
            args.extend(["--role", role])
        args.extend(extra)
        return self.foil(*args, **kwargs)

    def start_complementary_fleet(self) -> dict[str, Any]:
        lead = self.spawn("lead", "grok", lead=True, role="manager").json()
        implementer = self.spawn(
            "implementer",
            "grok",
            isolated=True,
            role="implementer",
        ).json()
        reviewer = self.spawn(
            "reviewer-challenger",
            "opencode",
            isolated=True,
            role="reviewer-challenger",
        ).json()
        return {
            "lead": lead,
            "implementer": implementer,
            "reviewer-challenger": reviewer,
        }

    def mailbox(self, command: str, seat: str, *args: str, **kwargs: Any) -> OperatorResult:
        return self.foil(
            command,
            "--state-dir",
            str(self.state),
            "--fleet",
            self.fleet_id,
            "--seat",
            seat,
            *args,
            **kwargs,
        )

    def git(self, *args: str) -> None:
        environment = os.environ.copy()
        environment.setdefault("GIT_AUTHOR_NAME", "Foil E2E")
        environment.setdefault("GIT_AUTHOR_EMAIL", "foil-e2e@localhost")
        environment.setdefault("GIT_COMMITTER_NAME", "Foil E2E")
        environment.setdefault("GIT_COMMITTER_EMAIL", "foil-e2e@localhost")
        subprocess.run(
            ["git", *args],
            cwd=self.project,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

    def init_project(self) -> dict[str, Any]:
        payload = self.foil("init", ".").json()
        self.fleet_id = payload["fleet_id"]
        self.git("init", "--quiet", "-b", payload["git_branch"])
        return payload

    def tmux_session(self) -> str:
        if not self.fleet_id:
            raise RuntimeError("init_project must run before tmux_session")
        return tmux_session_name(FLEET_DISPLAY_NAME, self.fleet_id)

    def kill_tmux(self) -> None:
        subprocess.run(
            [self._tmux, "kill-session", "-t", self.tmux_session()],
            capture_output=True,
            check=False,
        )

    def tmux_alive(self) -> bool:
        probe = subprocess.run(
            [self._tmux, "has-session", "-t", self.tmux_session()],
            capture_output=True,
            check=False,
        )
        return probe.returncode == 0

    def tmux_windows(self) -> list[str]:
        listed = subprocess.run(
            [
                self._tmux,
                "list-windows",
                "-t",
                self.tmux_session(),
                "-F",
                "#{window_name}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode != 0:
            return []
        return [line for line in listed.stdout.splitlines() if line]

    def seat_record_path(self, seat_id: str) -> Path:
        return (
            self.state / "v1" / "fleets" / self.fleet_id / "seats" / f"{seat_id}.json"
        )

    def kill_tmux_window(self, window_name: str) -> None:
        subprocess.run(
            [
                self._tmux,
                "kill-window",
                "-t",
                f"{self.tmux_session()}:{window_name}",
            ],
            capture_output=True,
            check=False,
        )

    def inbox_path(self, seat: str, message_id: str) -> Path:
        return (
            self.state
            / "v1"
            / "fleets"
            / self.fleet_id
            / "mailboxes"
            / seat
            / "inbox"
            / f"{message_id}.json"
        )

    def invocations(self) -> list[dict[str, Any]]:
        path = self.agent_home / "invocations.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def wait_for_invocations(
        self,
        predicate,
        *,
        timeout: float = 5.0,
        description: str = "matching agent invocation",
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [row for row in self.invocations() if predicate(row)]
            if matches:
                return matches
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {description}")

    def stdin_lines(self) -> list[str]:
        path = self.agent_home / "stdin.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line)["line"] for line in path.read_text(encoding="utf-8").splitlines()]

    def wait_for_wake(self, timeout: float = 5.0) -> str:
        return self.wait_for_wakes(1, timeout=timeout)

    def wait_for_wakes(self, count: int, timeout: float = 5.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.wake_count() >= count:
                return "".join(
                    line
                    for line in self.stdin_lines()
                    if TmuxWakeService.WAKE_TEXT in line
                )
            time.sleep(0.05)
        raise AssertionError(
            f"idle agents received {self.wake_count()} wake(s); expected at least {count}"
        )

    def wake_count(self) -> int:
        return sum(1 for line in self.stdin_lines() if TmuxWakeService.WAKE_TEXT in line)

    def wait_for_ack(
        self,
        seat: str,
        message_id: str,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            result = self.mailbox("message-status", seat, "--message", message_id)
            if result.returncode == 0:
                last = result.json()
                if last.get("state") == "acknowledged":
                    return last
            time.sleep(0.05)
        raise AssertionError(
            f"timed out waiting for {seat}/{message_id} acknowledgement: {last}"
        )

    def cleanup(self) -> None:
        if self.fleet_id:
            self.lifecycle("stop")
            self.kill_tmux()
        if self._keeper:
            subprocess.run(
                [self._tmux, "kill-session", "-t", self._keeper],
                capture_output=True,
                check=False,
            )
            self._keeper = ""
