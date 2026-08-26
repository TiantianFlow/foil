"""Startup instruction and explicit permission profiles on shipped adapters."""

from __future__ import annotations

from tests.e2e.harness import OperatorFleet


def test_supervised_launch_includes_startup_without_yolo(
    initialized: OperatorFleet,
) -> None:
    spawned = initialized.spawn("lead", "grok", lead=True, role="manager")
    assert spawned.returncode == 0, spawned.stderr
    listed = initialized.foil("seat", "list", *initialized.fleet_flags(), "--json").json()
    assert listed["seats"][0]["permission"] == "supervised"
    argv = initialized.runner_plan("lead")["argv"]
    assert any("bootstrap.json" in str(token) for token in argv)
    assert "--always-approve" not in argv
    inspect = initialized.foil(
        "seat",
        "inspect",
        *initialized.fleet_flags(),
        "--seat",
        "lead",
    ).json()
    assert inspect["profile"]["permission"] == "supervised"


def test_auto_permission_is_recorded_and_applied(initialized: OperatorFleet) -> None:
    lead = initialized.spawn(
        "lead",
        "grok",
        lead=True,
        role="manager",
        permission="auto",
    )
    assert lead.returncode == 0, lead.stderr
    worker = initialized.spawn(
        "reviewer-challenger",
        "opencode",
        role="reviewer-challenger",
        permission="auto",
    )
    assert worker.returncode == 0, worker.stderr
    grok_argv = initialized.runner_plan("lead")["argv"]
    open_argv = initialized.runner_plan("reviewer-challenger")["argv"]
    assert "--always-approve" in grok_argv
    assert any("bootstrap.json" in str(token) for token in grok_argv)
    assert "--auto" in open_argv
    assert any("bootstrap.json" in str(token) for token in open_argv)
    listed = initialized.foil("seat", "list", *initialized.fleet_flags(), "--json").json()
    by_id = {seat["seat_id"]: seat for seat in listed["seats"]}
    assert by_id["lead"]["permission"] == "auto"
    assert by_id["reviewer-challenger"]["permission"] == "auto"

    initialized.lifecycle("stop").json()
    resumed = initialized.lifecycle("resume").json()
    assert {seat["action"] for seat in resumed["seats"]} == {"resume_native"}
    grok_resume = initialized.runner_plan("lead")["argv"]
    assert "--always-approve" in grok_resume
    assert "--resume" in grok_resume
    assert not any("bootstrap.json" in str(token) for token in grok_resume)
    open_resume = initialized.runner_plan("reviewer-challenger")["argv"]
    assert "--auto" in open_resume
    assert "--session" in open_resume
