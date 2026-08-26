"""Full operator journey against the lead-owned live fleet.

These cases follow `docs/walking-skeleton.md` T2–T6 using the shipped
`grok` and `opencode` contracts. Real CLIs are replaced by PATH shims.
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
        "seat",
        "status",
        "resume",
        "poll-status",
        "send-message",
        "ack-message",
        "message-status",
        "notepad-write",
        "notepad-read",
        "notepad-ack",
        "memory-propose",
        "memory-accept",
        "memory-supersede",
        "memory-reject",
        "memory-status",
        "dispatch",
        "doctor",
        "set-state",
        "catalog-list",
        "catalog-map",
    ):
        assert command in result.stdout
    assert "launch" not in result.stdout


def test_init_emits_the_paths_an_operator_copies_into_later_commands(
    fleet: OperatorFleet,
) -> None:
    payload = fleet.foil("init", ".").json()
    assert payload["roles_path"] == str(fleet.roles_path)
    assert payload["state_root"] == str(fleet.state)
    assert payload["git_branch"] == "foil-demo"
    assert payload["lead_seat_id"] is None
    assert payload["fleet_id"].startswith("starter-")
    assert (fleet.project / ".foil" / "roles" / "implementer.toml").is_file()
    assert (fleet.project / ".foil" / "roles" / "reviewer-challenger.toml").is_file()
    assert not (fleet.project / ".foil" / "runtime.toml").exists()


def test_t2_through_t6_lead_owned_user_journey(initialized: OperatorFleet) -> None:
    fleet = initialized
    spawned = fleet.start_complementary_fleet()
    lead = _seats(spawned["lead"])["lead"]
    implementer = _seats(spawned["implementer"])["implementer"]
    reviewer = _seats(spawned["reviewer-challenger"])["reviewer-challenger"]
    assert lead["state"] == "working"
    assert implementer["state"] == "working"
    assert reviewer["state"] == "working"
    assert lead["registry"]["agent_kind"] == "grok_cli"
    assert implementer["registry"]["agent_kind"] == "grok_cli"
    assert reviewer["registry"]["agent_kind"] == "opencode"
    assert uuid.UUID(implementer["registry"]["native_session_id"])
    assert reviewer["registry"]["native_session_id"].startswith("oc-")
    session_names = {
        seat["registry"]["tmux"]["session_name"]
        for seat in (lead, implementer, reviewer)
    }
    assert session_names == {fleet.tmux_session()}
    assert fleet.tmux_alive()
    windows = fleet.tmux_windows()
    assert len(windows) == 3
    assert len(set(windows)) == 3

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
    assert "grok-4.6" in grok_launches[0]["argv"]
    assert "xai/grok-4.6" in opencode_launches[0]["argv"]
    assert implementer["registry"]["worktree_path"] == str(
        (fleet.project / "worktrees" / "implementer").resolve()
    )
    assert reviewer["registry"]["worktree_path"] == str(
        (fleet.project / "worktrees" / "reviewer-challenger").resolve()
    )

    status = fleet.lifecycle("status").json()
    assert all(seat["state"] == "working" for seat in status["seats"])
    assert status["lead_seat_id"] == "lead"

    uncommitted = "uncommitted-only-in-root.txt"
    (fleet.project / uncommitted).write_text("do-not-clone", encoding="utf-8")
    for seat_id in ("implementer", "reviewer-challenger"):
        worktree = fleet.project / "worktrees" / seat_id
        assert worktree.is_dir()
        assert not (worktree / uncommitted).exists()
        branch = subprocess.run(
            ["git", "-C", str(worktree), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert branch.stdout.strip() == "foil-demo"

    doctor = fleet.foil("doctor", *fleet.fleet_flags(), "--json").json()
    assert doctor["tmux"]["ok"] is True
    assert doctor["git"]["ok"] is True
    assert doctor["worktrees"]["ok"] is True
    assert {seat["seat_id"] for seat in doctor["worktrees"]["seats"]} == {
        "lead",
        "implementer",
        "reviewer-challenger",
    }

    brief = fleet.foil(
        "notepad-write",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "journey-brief",
        "--author",
        "implementer",
        "--body",
        "Implementer drafts; reviewer challenges the same brief.",
    ).json()
    assert brief["duplicate"] is False
    read_brief = fleet.foil(
        "notepad-read",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "journey-brief",
    ).json()
    assert "reviewer challenges" in read_brief["body"]
    ack = fleet.foil(
        "notepad-ack",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "journey-brief",
        "--actor",
        "reviewer-challenger",
    ).json()
    assert ack["state"] == "acknowledged"

    lesson = fleet.foil(
        "memory-propose",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "journey-ack-first",
        "--author",
        "memory-curator",
        "--task",
        "quickstart-review",
        "--body",
        "Acknowledge mailbox messages before claiming the task is done.",
    ).json()
    assert lesson["state"] == "proposed"
    accepted = fleet.foil(
        "memory-accept",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "journey-ack-first",
        "--actor",
        "manager",
    ).json()
    assert accepted["state"] == "accepted"

    (fleet.agent_home / "usage-grok.json").write_text(
        json.dumps({"availability": "ok", "active_load": 2}),
        encoding="utf-8",
    )
    (fleet.agent_home / "usage-opencode.json").write_text(
        json.dumps({"availability": "ok", "active_load": 0}),
        encoding="utf-8",
    )
    decision = fleet.foil(
        "dispatch",
        *fleet.fleet_flags(),
        "--json",
        "--capability",
        "review",
    ).json()
    assert decision["selected_seat_id"] == "reviewer-challenger"

    waiting = fleet.foil(
        "set-state",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "implementer",
        "--state",
        "waiting",
    ).json()
    assert _seats(waiting)["implementer"]["state"] == "waiting"
    fleet.foil(
        "set-state",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "implementer",
        "--state",
        "working",
    ).json()

    before_fresh = _seats(status)["implementer"]["registry"]
    freshened = fleet.foil(
        "resume",
        *fleet.fleet_flags(),
        "--json",
        "--fresh",
        "--seat",
        "implementer",
    ).json()
    after_fresh = _seats(freshened)["implementer"]
    assert after_fresh["action"] == "start_fresh"
    assert after_fresh["registry"]["incarnation_id"] != before_fresh["incarnation_id"]
    assert after_fresh["registry"]["previous_incarnation_id"] == before_fresh["incarnation_id"]
    assert len(fleet.tmux_windows()) == 3

    status = fleet.lifecycle("status").json()
    assert all(seat["state"] == "working" for seat in status["seats"])

    hostile_body = "Review $(touch /tmp/foil-e2e-should-not-run) and report risks."
    message_id = "quickstart-review-001"
    delivery = fleet.mailbox(
        "send-message",
        "reviewer-challenger",
        "--sender",
        "lead",
        "--message-id",
        message_id,
        "--task",
        "quickstart-review",
        "--body",
        hostile_body,
        "--wake",
    ).json()
    assert delivery["duplicate"] is False
    assert delivery["message"]["body"] == hostile_body
    assert delivery["delivery"]["wake"]["state"] == "sent"

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
    acked = fleet.wait_for_ack("reviewer-challenger", message_id)
    assert acked["state"] == "acknowledged"

    worker_spawn = fleet.spawn(
        "intruder",
        "grok",
        extra=("--actor", "implementer"),
    )
    assert worker_spawn.returncode != 0
    assert "not authorized" in worker_spawn.stderr.lower()

    before = _seats(status)
    fleet.kill_tmux()
    dead = fleet.lifecycle("status").json()
    assert all(seat["state"] == "exited" for seat in dead["seats"])
    assert not fleet.tmux_alive()

    resumed = fleet.lifecycle("resume").json()
    after = _seats(resumed)
    assert all(seat["action"] == "resume_native" for seat in after.values())
    assert all(seat["state"] == "working" for seat in after.values())
    for seat_id in ("lead", "implementer", "reviewer-challenger"):
        assert after[seat_id]["registry"]["native_session_id"] == (
            before[seat_id]["registry"]["native_session_id"]
        )
        assert after[seat_id]["registry"]["incarnation_id"] == (
            before[seat_id]["registry"]["incarnation_id"]
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
    assert {seat["seat_id"] for seat in polled["seats"]} == {
        "lead",
        "implementer",
        "reviewer-challenger",
    }

    removed = fleet.foil(
        "seat",
        "remove",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "reviewer-challenger",
    ).json()
    assert _seats(removed)["reviewer-challenger"]["state"] == "removed"
    remaining = fleet.lifecycle("status").json()
    assert set(_seats(remaining)) == {"lead", "implementer"}
    assert all(seat["state"] == "working" for seat in remaining["seats"])

    stopped = fleet.lifecycle("stop").json()
    assert all(seat["state"] == "exited" for seat in stopped["seats"])
    assert not fleet.tmux_alive()

    events_path = Path(fleet.state) / "v1" / "fleets" / fleet.fleet_id / "events" / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert {event["event_type"] for event in events} >= {
        "spawn_result",
        "stop_result",
        "resume_decision",
        "resume_result",
        "remove_result",
    }
    assert all("argv" not in event for event in events)


def test_resume_while_seats_are_alive_revives_tmux(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    revived = fleet.lifecycle("resume").json()
    assert {seat["action"] for seat in revived["seats"]} == {"revive_tmux"}
    assert all(seat["state"] == "working" for seat in revived["seats"])
    assert fleet.tmux_alive()


def test_resume_after_stop_reuses_native_session_ids(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    launched = _seats(fleet.lifecycle("status").json())
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


def test_poll_status_reads_files_after_tmux_is_gone(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
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


def test_both_workers_can_be_mailed_independently(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    for seat, message_id, body in (
        ("implementer", "brief-implementer-1", "Please implement the brief."),
        ("reviewer-challenger", "brief-reviewer-1", "Please challenge the brief."),
    ):
        delivery = fleet.mailbox(
            "send-message",
            seat,
            "--sender",
            "lead",
            "--message-id",
            message_id,
            "--body",
            body,
            "--wake",
        ).json()
        assert delivery["delivery"]["wake"]["state"] == "sent"
        on_disk = json.loads(fleet.inbox_path(seat, message_id).read_text(encoding="utf-8"))
        assert on_disk["body"] == body
        fleet.wait_for_ack(seat, message_id)
    fleet.wait_for_wakes(2)
    wakes = "".join(fleet.stdin_lines())
    assert "Please implement the brief." not in wakes
    assert "Please challenge the brief." not in wakes
