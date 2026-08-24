"""CLI contract for durable mailbox delivery and wake state (CAP-001, CAP-020, CAP-036)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from foil.registry import RegistryStore, SeatRecord, TmuxTarget


def write_seat(state_dir: Path) -> None:
    RegistryStore(state_dir).write_seat(
        SeatRecord(
            fleet_id="fleet-1",
            seat_id="seat-1",
            working_directory="/tmp/project",
            agent_kind="fixture-cli",
            native_session_id=None,
            tmux=TmuxTarget(
                session_name="foil-fixture-12345678",
                window_name="seat-12345678",
                session_id="$42",
                window_id="@73",
            ),
            git_branch="feature/mail",
            worktree_path="/tmp/project",
            usage_pool_id="pool-1",
            incarnation_id="incarnation-1",
            updated_at="2026-08-23T20:00:00Z",
            extensions={},
        )
    )


def fake_tmux(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "tmux"
    log = tmp_path / "tmux.jsonl"
    executable.write_text(
        f"""#!{sys.executable}
import json
import os
import sys

with open(os.environ["FOIL_TMUX_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
raise SystemExit(0)
"""
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable, log


def run_foil(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "foil", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_send_message_commits_mail_then_wakes_with_fixed_text(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    write_seat(state_dir)
    executable, log = fake_tmux(tmp_path)
    hostile_body = "$(touch should-not-run); read the durable mailbox"
    env = os.environ.copy()
    env["PATH"] = f"{executable.parent}{os.pathsep}{env['PATH']}"
    env["FOIL_TMUX_LOG"] = str(log)

    result = run_foil(
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
        "--task",
        "task-1",
        "--body",
        hostile_body,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["message"]["message_id"] == "message-1"
    assert payload["message"]["body"] == hostile_body
    assert payload["duplicate"] is False
    assert payload["delivery"] == {"state": "queued", "wake": {"state": "sent"}}
    assert result.stderr == ""
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ["has-session", "-t", "$42"]
    assert calls[1][:5] == ["send-keys", "-t", "@73", "-l", calls[1][4]]
    assert "poll" in calls[1][4].lower()
    assert hostile_body not in json.dumps(calls)


def test_duplicate_send_is_detected_and_does_not_repeat_wake(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    write_seat(state_dir)
    executable, log = fake_tmux(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{executable.parent}{os.pathsep}{env['PATH']}"
    env["FOIL_TMUX_LOG"] = str(log)
    args = (
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
        "Inspect queued work.",
    )

    first = run_foil(*args, env=env)
    second = run_foil(*args, env=env)

    assert first.returncode == second.returncode == 0
    assert json.loads(second.stdout)["duplicate"] is True
    assert json.loads(second.stdout)["delivery"]["wake"]["state"] == "duplicate_skipped"
    assert len(log.read_text().splitlines()) == 3


def test_ack_message_and_message_status_are_pollable(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    write_seat(state_dir)
    executable, _ = fake_tmux(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{executable.parent}{os.pathsep}{env['PATH']}"
    env["FOIL_TMUX_LOG"] = str(tmp_path / "tmux.jsonl")
    send = run_foil(
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
        "Inspect queued work.",
        env=env,
    )
    assert send.returncode == 0, send.stderr

    queued = run_foil(
        "message-status",
        "--state-dir",
        str(state_dir),
        "--fleet",
        "fleet-1",
        "--seat",
        "seat-1",
        "--message",
        "message-1",
    )
    ack = run_foil(
        "ack-message",
        "--state-dir",
        str(state_dir),
        "--fleet",
        "fleet-1",
        "--seat",
        "seat-1",
        "--message",
        "message-1",
        "--actor",
        "seat-1",
        "--ack-id",
        "ack-1",
    )
    delivered = run_foil(
        "message-status",
        "--state-dir",
        str(state_dir),
        "--fleet",
        "fleet-1",
        "--seat",
        "seat-1",
        "--message",
        "message-1",
    )

    assert json.loads(queued.stdout)["state"] == "queued"
    assert ack.returncode == 0, ack.stderr
    assert json.loads(ack.stdout)["duplicate"] is False
    delivered_payload = json.loads(delivered.stdout)
    assert delivered_payload["state"] == "acknowledged"
    assert delivered_payload["acknowledgement"]["acknowledgement_id"] == "ack-1"


def test_help_lists_narrow_composable_message_commands() -> None:
    result = run_foil("--help")

    assert result.returncode == 0
    assert "send-message" in result.stdout
    assert "ack-message" in result.stdout
    assert "message-status" in result.stdout
