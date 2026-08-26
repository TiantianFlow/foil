"""Real-tmux mailbox wake integration (CAP-020, CAP-031, CAP-036)."""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from foil.registry import RegistryStore, SeatRecord, TmuxTarget

TMUX = shutil.which("tmux")


@pytest.mark.skipif(TMUX is None, reason="tmux is unavailable")
def test_idle_tmux_seat_receives_bounded_mailbox_wake(tmp_path: Path) -> None:
    session_name = f"foil-mailtest-{uuid.uuid4().hex[:12]}"
    window_name = "idle-seat"
    received = tmp_path / "received.txt"
    receiver = tmp_path / "receiver.py"
    receiver.write_text(
        f"""#!{sys.executable}
import pathlib
import sys

line = sys.stdin.readline()
pathlib.Path(sys.argv[1]).write_text(line, encoding="utf-8")
"""
    )
    receiver.chmod(receiver.stat().st_mode | stat.S_IXUSR)
    created = subprocess.run(
        [
            TMUX,
            "new-session",
            "-d",
            "-s",
            session_name,
            "-n",
            window_name,
            str(receiver),
            str(received),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.fail(f"tmux is installed but the test session could not start: {created.stderr}")

    try:
        identifiers = subprocess.run(
            [
                TMUX,
                "display-message",
                "-p",
                "-t",
                f"{session_name}:{window_name}",
                "-F",
                "#{session_id}\t#{window_id}",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        session_id, window_id = identifiers.split("\t")
        state_dir = tmp_path / "state"
        RegistryStore(state_dir).write_seat(
            SeatRecord(
                fleet_id="fleet-1",
                seat_id="seat-1",
                working_directory=str(tmp_path),
                agent_kind="fixture-cli",
                native_session_id=None,
                tmux=TmuxTarget(
                    session_name=session_name,
                    window_name=window_name,
                    session_id=session_id,
                    window_id=window_id,
                ),
                git_branch="feature/mail",
                worktree_path=str(tmp_path),
                usage_pool_id="pool-1",
                incarnation_id="incarnation-1",
                updated_at="2026-08-23T20:00:00Z",
                extensions={},
            )
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "foil",
                "send-message",
                "--state-dir",
                str(state_dir),
                "--fleet",
                "fleet-1",
                "--seat",
                "seat-1",
                "--sender",
                "controller-1",
                "--message-id",
                "message-1",
                "--body",
                "This body stays on disk and is not injected into tmux.",
                "--wake",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["delivery"]["wake"]["state"] == "sent"
        deadline = time.monotonic() + 3
        while not received.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert received.exists(), "idle tmux process did not receive the wake"
        wake = received.read_text()
        assert "poll" in wake.lower()
        assert "This body stays on disk" not in wake
    finally:
        subprocess.run(
            [TMUX, "kill-session", "-t", session_name],
            capture_output=True,
            check=False,
        )
