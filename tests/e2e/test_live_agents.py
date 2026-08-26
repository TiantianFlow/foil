"""Optional live T3/T6 against real grok and opencode CLIs.

Default CI cannot authenticate those tools. Set FOIL_E2E_LIVE=1 on a machine
that already has `grok` and `opencode` on PATH to exercise the same operator
commands without PATH shims.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.harness import OperatorFleet

pytestmark = pytest.mark.e2e_live


def _live_clis() -> list[str]:
    return [name for name in ("grok", "opencode") if shutil.which(name) is None]


@pytest.fixture
def live_fleet(tmp_path: Path) -> Iterator[OperatorFleet]:
    if os.environ.get("FOIL_E2E_LIVE") != "1":
        pytest.skip("set FOIL_E2E_LIVE=1 to run against real grok and opencode")
    missing = _live_clis()
    if missing:
        pytest.fail(f"FOIL_E2E_LIVE=1 but missing CLIs: {', '.join(missing)}")
    session = OperatorFleet(tmp_path, live=True)
    session.bootstrap()
    try:
        session.init_project()
        yield session
    finally:
        session.cleanup()


def test_live_lead_and_workers_spawn_status_and_stop(live_fleet: OperatorFleet) -> None:
    fleet = live_fleet
    fleet.start_complementary_fleet()
    status = fleet.lifecycle("status").json()
    seats = {seat["seat_id"]: seat for seat in status["seats"]}
    assert set(seats) == {"lead", "implementer", "reviewer-challenger"}
    assert all(seat["state"] == "working" for seat in seats.values())
    assert fleet.tmux_alive()
    stopped = fleet.lifecycle("stop").json()
    assert all(seat["state"] == "exited" for seat in stopped["seats"])
    assert not fleet.tmux_alive()


EXAM_LEAD_ROLE = """
schema_version = 1
id = "exam-lead"
display_name = "Exam lead"
primary_specialization = "coordination-and-synthesis"
description = '''
Read FOIL_BOOTSTRAP and FOIL.md. You are the lead.
Immediately spawn one OpenCode worker with foil seat spawn,
--state-dir $FOIL_STATE_DIR, --fleet $FOIL_FLEET_ID, --json,
--seat reviewer-challenger, --cli opencode,
--role reviewer-challenger, and --permission auto.
Then send that seat a mailbox message whose body is exactly
REAL_FOIL_ROUNDTRIP_PLEASE.
After it replies REAL_FOIL_ROUNDTRIP_OK, acknowledge the reply.
Do not wait for a human.
'''
required_output = "Spawned OpenCode worker and completed the mailbox round trip."
challenge_focus = "None."
"""

EXAM_WORKER_ROLE = """
schema_version = 1
id = "exam-worker"
display_name = "Exam worker"
primary_specialization = "defect-first-independent-review"
description = '''
Read FOIL_BOOTSTRAP and FOIL.md. Poll your mailbox.
When you read REAL_FOIL_ROUNDTRIP_PLEASE, acknowledge it.
Then reply to the lead with foil send-message body REAL_FOIL_ROUNDTRIP_OK.
'''
required_output = "REAL_FOIL_ROUNDTRIP_OK mailbox reply."
challenge_focus = "None."
"""


def test_live_authenticated_lead_to_worker_roundtrip(live_fleet: OperatorFleet) -> None:
    fleet = live_fleet
    lead_role = fleet.project / "exam-lead.toml"
    lead_role.write_text(EXAM_LEAD_ROLE.strip() + "\n", encoding="utf-8")
    (fleet.roles_path / "reviewer-challenger.toml").write_text(
        EXAM_WORKER_ROLE.strip() + "\n",
        encoding="utf-8",
    )
    spawned = fleet.foil(
        "seat",
        "spawn",
        *fleet.fleet_flags(),
        "--json",
        "--lead",
        "--seat",
        "lead",
        "--cli",
        "grok",
        "--permission",
        "auto",
        "--role-file",
        str(lead_role),
    )
    assert spawned.returncode == 0, spawned.stderr
    argv = fleet.runner_plan("lead")["argv"]
    assert "--always-approve" in argv
    assert any("bootstrap.json" in str(token) for token in argv)

    deadline = time.monotonic() + 240
    worker = None
    while time.monotonic() < deadline:
        listed = fleet.foil("seat", "list", *fleet.fleet_flags(), "--json").json()
        by_id = {seat["seat_id"]: seat for seat in listed["seats"]}
        if "reviewer-challenger" in by_id:
            worker = by_id["reviewer-challenger"]
            break
        time.sleep(2)
    assert worker is not None, "lead did not spawn the OpenCode worker"
    assert worker["permission"] == "auto"

    inbox = fleet.state / "v1" / "fleets" / fleet.fleet_id / "mailboxes"
    please = None
    while time.monotonic() < deadline:
        for path in (inbox / "reviewer-challenger" / "inbox").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("body") == "REAL_FOIL_ROUNDTRIP_PLEASE":
                please = payload["message_id"]
                break
        if please:
            break
        time.sleep(2)
    assert please is not None, "lead did not send the exam mailbox message"
    fleet.wait_for_ack("reviewer-challenger", please, timeout=120)

    reply = None
    while time.monotonic() < deadline:
        for path in (inbox / "lead" / "inbox").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("body") == "REAL_FOIL_ROUNDTRIP_OK":
                reply = payload["message_id"]
                break
        if reply:
            break
        time.sleep(2)
    assert reply is not None, "worker did not reply REAL_FOIL_ROUNDTRIP_OK"
    fleet.wait_for_ack("lead", reply, timeout=120)

    fleet.foil("seat", "stop", *fleet.fleet_flags(), "--json", "--all").json()
    for seat_id in ("reviewer-challenger", "lead"):
        removed = fleet.foil(
            "seat",
            "remove",
            *fleet.fleet_flags(),
            "--json",
            "--seat",
            seat_id,
        )
        assert removed.returncode == 0, removed.stderr
    assert not fleet.tmux_alive()
    assert fleet.tmux_windows() == []

