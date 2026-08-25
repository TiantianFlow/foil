"""Fail-closed operator mistakes. Launch must not leave stray tmux sessions."""

from __future__ import annotations

import shutil

from tests.e2e.harness import OperatorFleet, require_tmux


def test_init_rejects_a_nonempty_directory(fleet: OperatorFleet) -> None:
    (fleet.project / "already-here.txt").write_text("nope", encoding="utf-8")
    result = fleet.foil("init", ".")
    assert result.returncode != 0
    assert "empty" in result.stderr.lower()
    assert not (fleet.project / ".foil").exists()


def test_launch_without_git_identity_does_not_start_tmux(fleet: OperatorFleet) -> None:
    payload = fleet.foil("init", ".").json()
    fleet.fleet_id = payload["fleet_id"]
    launched = fleet.lifecycle("launch")
    assert launched.returncode != 0
    assert "git" in launched.stderr.lower() or "worktree" in launched.stderr.lower()
    assert not fleet.tmux_alive()


def test_wrong_branch_does_not_start_tmux(initialized: OperatorFleet) -> None:
    initialized.git("checkout", "-B", "not-foil-demo")
    launched = initialized.lifecycle("launch")
    assert launched.returncode != 0
    assert "branch" in launched.stderr.lower()
    assert not initialized.tmux_alive()


def test_missing_agent_cli_fails_closed(initialized: OperatorFleet) -> None:
    (initialized.bin / "grok").unlink()
    launched = initialized.lifecycle("launch")
    assert launched.returncode != 0
    assert "unavailable" in launched.stderr.lower()
    assert not initialized.tmux_alive()


def test_missing_opencode_cli_fails_closed(initialized: OperatorFleet) -> None:
    (initialized.bin / "opencode").unlink()
    launched = initialized.lifecycle("launch")
    assert launched.returncode != 0
    assert "unavailable" in launched.stderr.lower()
    assert not initialized.tmux_alive()


def test_second_launch_is_rejected(initialized: OperatorFleet) -> None:
    first = initialized.lifecycle("launch")
    assert first.returncode == 0, first.stderr
    second = initialized.lifecycle("launch")
    assert second.returncode != 0
    assert "already" in second.stderr.lower()
    assert initialized.tmux_alive()


def test_corrupt_runtime_toml_does_not_launch(initialized: OperatorFleet) -> None:
    initialized.config.write_text("this is not toml {", encoding="utf-8")
    launched = initialized.lifecycle("launch")
    assert launched.returncode != 0
    assert launched.stdout == ""
    assert not initialized.tmux_alive()


def test_stop_without_launch_fails_without_a_seat_record(initialized: OperatorFleet) -> None:
    result = initialized.lifecycle("stop")
    assert result.returncode != 0
    assert "registered" in result.stderr.lower()
    assert not initialized.tmux_alive()


def test_send_message_before_launch_fails_without_a_seat_record(
    initialized: OperatorFleet,
) -> None:
    result = initialized.mailbox(
        "send-message",
        "reviewer-challenger",
        "--sender",
        "operator",
        "--body",
        "Should not queue against a missing seat.",
    )
    assert result.returncode != 0


def test_duplicate_message_does_not_wake_again(initialized: OperatorFleet) -> None:
    initialized.lifecycle("launch").json()
    args = (
        "send-message",
        "reviewer-challenger",
        "--sender",
        "operator",
        "--message-id",
        "dup-1",
        "--body",
        "Same body twice.",
    )
    first = initialized.mailbox(*args).json()
    initialized.wait_for_wake()
    second = initialized.mailbox(*args).json()
    assert first["duplicate"] is False
    assert first["delivery"]["wake"]["state"] == "sent"
    assert second["duplicate"] is True
    assert second["delivery"]["wake"]["state"] == "duplicate_skipped"
    assert initialized.wake_count() == 1


def test_ack_completes_delivery_on_a_live_seat(initialized: OperatorFleet) -> None:
    initialized.lifecycle("launch").json()
    initialized.mailbox(
        "send-message",
        "implementer",
        "--sender",
        "operator",
        "--message-id",
        "ack-path-1",
        "--body",
        "Please implement the brief.",
    ).json()
    queued = initialized.mailbox(
        "message-status",
        "implementer",
        "--message",
        "ack-path-1",
    ).json()
    ack = initialized.mailbox(
        "ack-message",
        "implementer",
        "--message",
        "ack-path-1",
        "--actor",
        "implementer",
    ).json()
    done = initialized.mailbox(
        "message-status",
        "implementer",
        "--message",
        "ack-path-1",
    ).json()
    assert queued["state"] == "queued"
    assert ack["state"] == "acknowledged"
    assert done["state"] == "acknowledged"


def test_missing_tmux_binary_is_a_nonzero_diagnostic(initialized: OperatorFleet) -> None:
    require_tmux()
    hidden = initialized.root / "no-tmux-path"
    hidden.mkdir()
    git = shutil.which("git")
    assert git is not None
    (hidden / "git").symlink_to(git)
    for name in ("grok", "opencode"):
        (hidden / name).symlink_to(initialized.bin / name)
    (hidden / "agent-home").write_text(str(initialized.agent_home), encoding="utf-8")
    env = initialized.env()
    env["PATH"] = str(hidden)
    launched = initialized.lifecycle("launch", env=env)
    assert launched.returncode != 0
    combined = launched.stdout + launched.stderr
    assert "tmux" in combined.lower()
    assert not initialized.tmux_alive()
