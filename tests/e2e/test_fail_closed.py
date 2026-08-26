"""Fail-closed operator mistakes. Spawn must not leave stray tmux sessions."""

from __future__ import annotations

import shutil

from tests.e2e.harness import OperatorFleet, require_tmux


def test_init_rejects_a_nonempty_directory(fleet: OperatorFleet) -> None:
    (fleet.project / "already-here.txt").write_text("nope", encoding="utf-8")
    result = fleet.foil("init", ".")
    assert result.returncode != 0
    assert "empty" in result.stderr.lower()
    assert not (fleet.project / ".foil").exists()


def test_spawn_without_git_identity_does_not_start_tmux(fleet: OperatorFleet) -> None:
    payload = fleet.foil("init", ".").json()
    fleet.fleet_id = payload["fleet_id"]
    spawned = fleet.spawn("lead", "grok", lead=True, role="manager")
    assert spawned.returncode != 0
    assert "git" in spawned.stderr.lower() or "worktree" in spawned.stderr.lower()
    assert not fleet.tmux_alive()


def test_worker_before_lead_does_not_start_tmux(initialized: OperatorFleet) -> None:
    spawned = initialized.spawn(
        "implementer",
        "grok",
        isolated=True,
        role="implementer",
    )
    assert spawned.returncode != 0
    assert "lead" in spawned.stderr.lower()
    assert not initialized.tmux_alive()


def test_missing_agent_cli_fails_closed(initialized: OperatorFleet) -> None:
    (initialized.bin / "grok").unlink()
    spawned = initialized.spawn("lead", "grok", lead=True, role="manager")
    assert spawned.returncode != 0
    assert "unavailable" in spawned.stderr.lower()
    assert not initialized.tmux_alive()


def test_missing_opencode_cli_fails_closed(initialized: OperatorFleet) -> None:
    initialized.spawn("lead", "grok", lead=True, role="manager").json()
    (initialized.bin / "opencode").unlink()
    spawned = initialized.spawn(
        "reviewer-challenger",
        "opencode",
        isolated=True,
        role="reviewer-challenger",
    )
    assert spawned.returncode != 0
    assert "unavailable" in spawned.stderr.lower()


def test_second_spawn_of_the_same_seat_is_rejected(initialized: OperatorFleet) -> None:
    first = initialized.spawn("lead", "grok", lead=True, role="manager")
    assert first.returncode == 0, first.stderr
    second = initialized.spawn("lead", "grok", lead=True, role="manager")
    assert second.returncode != 0
    assert "already" in second.stderr.lower()
    assert initialized.tmux_alive()


def test_unknown_fleet_does_not_spawn(initialized: OperatorFleet) -> None:
    spawned = initialized.foil(
        "seat",
        "spawn",
        "--state-dir",
        str(initialized.state),
        "--fleet",
        "missing-fleet",
        "--json",
        "--lead",
        "--seat",
        "lead",
        "--cli",
        "grok",
    )
    assert spawned.returncode != 0
    assert spawned.stdout == ""
    assert not initialized.tmux_alive()


def test_stop_without_seats_fails_without_a_seat_record(initialized: OperatorFleet) -> None:
    result = initialized.lifecycle("stop")
    assert result.returncode != 0
    assert "registered" in result.stderr.lower()
    assert not initialized.tmux_alive()


def test_send_message_before_spawn_fails_without_a_seat_record(
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
    initialized.start_complementary_fleet()
    args = (
        "send-message",
        "reviewer-challenger",
        "--sender",
        "lead",
        "--message-id",
        "dup-1",
        "--body",
        "Same body twice.",
        "--wake",
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
    initialized.start_complementary_fleet()
    initialized.mailbox(
        "send-message",
        "implementer",
        "--sender",
        "lead",
        "--message-id",
        "ack-path-1",
        "--body",
        "Please implement the brief.",
    ).json()
    done = initialized.wait_for_ack("implementer", "ack-path-1")
    assert done["state"] == "acknowledged"


def test_in_seat_worker_cannot_change_membership(initialized: OperatorFleet) -> None:
    initialized.start_complementary_fleet()
    env = initialized.env()
    env["FOIL_SEAT_ID"] = "implementer"
    for extra in ((), ("--actor", "operator"), ("--actor", "lead")):
        spawned = initialized.spawn(
            "intruder",
            "grok",
            extra=extra,
            env=env,
        )
        assert spawned.returncode != 0
        assert "authorized" in spawned.stderr.lower() or "override" in spawned.stderr.lower()


def test_send_message_without_wake_does_not_type_into_tmux(
    initialized: OperatorFleet,
) -> None:
    initialized.start_complementary_fleet()
    before = initialized.wake_count()
    delivery = initialized.mailbox(
        "send-message",
        "implementer",
        "--sender",
        "lead",
        "--message-id",
        "no-wake-1",
        "--body",
        "Queued only.",
    ).json()
    assert delivery["delivery"]["wake"]["state"] == "not_requested"
    assert initialized.wake_count() == before
    woken = initialized.foil(
        "seat",
        "wake",
        *initialized.fleet_flags(),
        "--json",
        "--seat",
        "implementer",
    ).json()
    # The shim may acknowledge from its poll loop before this explicit wake.
    assert woken["wake"]["state"] in {"sent", "skipped_empty"}
    if woken["wake"]["state"] == "sent":
        assert initialized.wake_count() == before + 1
    else:
        assert initialized.wake_count() == before
    initialized.wait_for_ack("implementer", "no-wake-1")


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
    spawned = initialized.spawn("lead", "grok", lead=True, role="manager", extra=(), env=env)
    assert spawned.returncode != 0
    combined = spawned.stdout + spawned.stderr
    assert "tmux" in combined.lower()
    assert not initialized.tmux_alive()
