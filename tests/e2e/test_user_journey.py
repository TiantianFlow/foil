"""Full operator journey against the generated two-seat runtime.

These cases follow `docs/walking-skeleton.md` T2–T6 using the shipped
`grok_cli` and `opencode` adapters. Real CLIs are replaced by PATH shims that
keep the same argv and session-capture contracts so CI can prove the app works.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

from foil.delivery import TmuxWakeService
from tests.e2e.harness import OperatorFleet, OperatorResult


def _seats(payload: dict) -> dict:
    return {seat["seat_id"]: seat for seat in payload["seats"]}


def test_operator_help_lists_the_commands_a_controller_uses() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "foil", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    result = OperatorResult(completed)
    assert result.returncode == 0
    for command in (
        "init",
        "launch",
        "status",
        "stop",
        "resume",
        "poll-status",
        "send-message",
        "ack-message",
        "message-status",
    ):
        assert command in result.stdout


def test_init_emits_the_paths_an_operator_copies_into_later_commands(
    fleet: OperatorFleet,
) -> None:
    payload = fleet.foil("init", ".").json()
    assert payload["runtime_config_path"] == str(fleet.config)
    assert payload["state_root"] == str(fleet.state)
    assert payload["git_branch"] == "foil-demo"
    assert payload["fleet_id"].startswith("starter-")
    assert (fleet.project / ".foil" / "fleet.toml").is_file()
    assert (fleet.project / ".foil" / "roles" / "implementer.toml").is_file()
    assert (fleet.project / ".foil" / "roles" / "reviewer-challenger.toml").is_file()


def test_t2_through_t6_two_seat_user_journey(initialized: OperatorFleet) -> None:
    fleet = initialized
    assert fleet.config.is_file()

    launched = fleet.lifecycle("launch").json()
    seats = _seats(launched)
    assert set(seats) == {"implementer", "reviewer-challenger"}
    assert all(seat["state"] == "working" for seat in seats.values())
    assert seats["implementer"]["registry"]["agent_kind"] == "grok_cli"
    assert seats["reviewer-challenger"]["registry"]["agent_kind"] == "opencode"
    assert seats["implementer"]["usage_pool_id"] == "primary"
    assert seats["reviewer-challenger"]["usage_pool_id"] == "independent-review"
    assert uuid.UUID(seats["implementer"]["registry"]["native_session_id"])
    assert seats["reviewer-challenger"]["registry"]["native_session_id"].startswith("oc-")
    assert all(
        seat["registry"]["tmux"]["session_name"].startswith("foil-")
        for seat in seats.values()
    )
    session_names = {seat["registry"]["tmux"]["session_name"] for seat in seats.values()}
    assert session_names == {fleet.tmux_session()}
    assert fleet.tmux_alive()
    windows = fleet.tmux_windows()
    assert len(windows) == 2
    assert len(set(windows)) == 2

    grok_launches = [
        row for row in fleet.invocations() if row["cli"] == "grok" and "--session-id" in row["argv"]
    ]
    opencode_launches = [
        row
        for row in fleet.invocations()
        if row["cli"] == "opencode" and row["argv"][:1] == ["."] and "--session" not in row["argv"]
    ]
    assert grok_launches
    assert opencode_launches
    assert "--model" in grok_launches[0]["argv"]
    assert "grok-4.6" in grok_launches[0]["argv"]
    assert "xai/grok-4.6" in opencode_launches[0]["argv"]
    assert grok_launches[0]["cwd"] == str(fleet.project.resolve())
    assert opencode_launches[0]["cwd"] == str(fleet.project.resolve())

    status = fleet.lifecycle("status").json()
    assert all(seat["state"] == "working" for seat in status["seats"])

    hostile_body = "Review $(touch /tmp/foil-e2e-should-not-run) and report risks."
    message_id = "quickstart-review-001"
    delivery = fleet.mailbox(
        "send-message",
        "reviewer-challenger",
        "--sender",
        "quickstart-controller",
        "--message-id",
        message_id,
        "--task",
        "quickstart-review",
        "--body",
        hostile_body,
    ).json()
    assert delivery["duplicate"] is False
    assert delivery["message"]["body"] == hostile_body
    assert delivery["delivery"]["state"] == "queued"
    assert delivery["delivery"]["wake"]["state"] == "sent"

    queued = fleet.mailbox(
        "message-status",
        "reviewer-challenger",
        "--message",
        message_id,
    ).json()
    assert queued["state"] == "queued"

    inbox = json.loads(
        fleet.inbox_path("reviewer-challenger", message_id).read_text(encoding="utf-8")
    )
    assert inbox["body"] == hostile_body

    wake = fleet.wait_for_wake()
    assert "poll" in wake.lower()
    assert TmuxWakeService.WAKE_TEXT in wake
    assert hostile_body not in wake
    assert "$(touch" not in wake
    assert not Path("/tmp/foil-e2e-should-not-run").exists()

    before = _seats(status)
    fleet.kill_tmux()
    dead = fleet.lifecycle("status").json()
    assert all(seat["state"] == "exited" for seat in dead["seats"])
    assert not fleet.tmux_alive()

    resumed = fleet.lifecycle("resume").json()
    after = _seats(resumed)
    assert all(seat["action"] == "resume_native" for seat in after.values())
    assert all(seat["state"] == "working" for seat in after.values())
    for seat_id in ("implementer", "reviewer-challenger"):
        assert after[seat_id]["registry"]["native_session_id"] == (
            before[seat_id]["registry"]["native_session_id"]
        )
        assert after[seat_id]["registry"]["incarnation_id"] == (
            before[seat_id]["registry"]["incarnation_id"]
        )
        assert after[seat_id]["registry"]["tmux"]["session_name"] == (
            before[seat_id]["registry"]["tmux"]["session_name"]
        )
        assert after[seat_id]["registry"]["tmux"]["session_id"] != (
            before[seat_id]["registry"]["tmux"]["session_id"]
        )
        assert after[seat_id]["registry"]["tmux"]["window_id"] != (
            before[seat_id]["registry"]["tmux"]["window_id"]
        )

    grok_resumes = fleet.wait_for_invocations(
        lambda row: row["cli"] == "grok" and "--resume" in row["argv"],
        description="grok --resume",
    )
    opencode_resumes = fleet.wait_for_invocations(
        lambda row: row["cli"] == "opencode" and "--session" in row["argv"],
        description="opencode --session resume",
    )
    assert before["implementer"]["registry"]["native_session_id"] in grok_resumes[0]["argv"]
    assert before["reviewer-challenger"]["registry"]["native_session_id"] in opencode_resumes[0][
        "argv"
    ]

    polled = fleet.foil(
        "poll-status",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
    ).json()
    assert [seat["seat_id"] for seat in polled["seats"]] == [
        "implementer",
        "reviewer-challenger",
    ]
    assert all(seat["state"] == "working" for seat in polled["seats"])

    stopped = fleet.lifecycle("stop").json()
    assert all(seat["state"] == "exited" for seat in stopped["seats"])
    assert not fleet.tmux_alive()

    events_path = Path(fleet.state) / "v1" / "fleets" / fleet.fleet_id / "events" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert {event["event_type"] for event in events} >= {
        "launch_result",
        "stop_result",
        "resume_decision",
        "resume_result",
    }
    assert all("argv" not in event for event in events)


def test_resume_while_seats_are_alive_revives_tmux(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.lifecycle("launch").json()
    revived = fleet.lifecycle("resume").json()
    assert {seat["action"] for seat in revived["seats"]} == {"revive_tmux"}
    assert all(seat["state"] == "working" for seat in revived["seats"])
    assert fleet.tmux_alive()


def test_resume_after_stop_reuses_native_session_ids(initialized: OperatorFleet) -> None:
    fleet = initialized
    launched = _seats(fleet.lifecycle("launch").json())
    fleet.lifecycle("stop").json()
    assert not fleet.tmux_alive()
    resumed = _seats(fleet.lifecycle("resume").json())
    assert all(seat["action"] == "resume_native" for seat in resumed.values())
    assert all(seat["state"] == "working" for seat in resumed.values())
    for seat_id in launched:
        assert (
            resumed[seat_id]["registry"]["native_session_id"]
            == launched[seat_id]["registry"]["native_session_id"]
        )
        assert (
            resumed[seat_id]["registry"]["incarnation_id"]
            == launched[seat_id]["registry"]["incarnation_id"]
        )


def test_poll_status_reads_files_after_tmux_is_gone(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.lifecycle("launch").json()
    fleet.lifecycle("stop").json()
    polled = fleet.foil(
        "poll-status",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
    ).json()
    assert all(seat["state"] == "exited" for seat in polled["seats"])
    assert not fleet.tmux_alive()


def test_both_seats_can_be_mailed_independently(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.lifecycle("launch").json()
    for seat, message_id, body in (
        ("implementer", "brief-implementer-1", "Please implement the brief."),
        ("reviewer-challenger", "brief-reviewer-1", "Please challenge the brief."),
    ):
        delivery = fleet.mailbox(
            "send-message",
            seat,
            "--sender",
            "operator",
            "--message-id",
            message_id,
            "--body",
            body,
        ).json()
        assert delivery["delivery"]["wake"]["state"] == "sent"
        on_disk = json.loads(fleet.inbox_path(seat, message_id).read_text(encoding="utf-8"))
        assert on_disk["body"] == body
        assert on_disk["recipient_seat_id"] == seat
    fleet.wait_for_wakes(2)
    assert fleet.wake_count() >= 2
    wakes = "".join(fleet.stdin_lines())
    assert "Please implement the brief." not in wakes
    assert "Please challenge the brief." not in wakes
