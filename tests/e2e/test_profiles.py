"""Hermetic e2e for the declarative per-seat profile contract."""

from __future__ import annotations

import json
import uuid

from tests.e2e.harness import OperatorFleet

PROFILE = """
schema_version = 1
id = "hermetic-grok"
cli = "grok"

[executable]
candidates = ["grok"]

[launch]
argv = ["grok", "--session-id", "{native_session_id}"]

[startup]
argv = ["--startup-note", "read-{bootstrap_path}"]

[resume]
supported = true
argv = ["grok", "--resume", "{native_session_id}"]

[session_capture]
kind = "generated_uuid"

[permissions]
auto = ["--auto-yes"]

[environment]
forward = ["FOIL_E2E_FORWARD_MARK", "FOIL_E2E_FORWARD_ABSENT"]

[working]
isolated = true
"""


def _seat_map(payload: dict) -> dict:
    return {seat["seat_id"]: seat for seat in payload["seats"]}


def _state_texts(fleet: OperatorFleet) -> list[tuple[str, str]]:
    root = fleet.state / "v1" / "fleets" / fleet.fleet_id
    return [
        (str(path), path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in {".json", ".jsonl"}
    ]


def test_declarative_profile_owns_launch_resume_permissions_and_environment(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    profile_path = fleet.project / "seat-profile.toml"
    profile_path.write_text(PROFILE, encoding="utf-8")
    marker = f"mark-{uuid.uuid4().hex[:12]}"
    env = fleet.env()
    env["FOIL_E2E_FORWARD_MARK"] = marker

    # --profile alone staffs the seat; --cli is declared by the profile.
    spawned = fleet.spawn(
        "worker",
        permission="auto",
        profile=str(profile_path),
        env=env,
    ).json()
    worker = _seat_map(spawned)["worker"]
    assert worker["state"] == "working"
    assert worker["registry"]["agent_kind"] == "hermetic-grok"
    assert worker["registry"]["worktree_path"] == str(
        fleet.project / "worktrees" / "worker"
    )

    launched = fleet.wait_for_invocations(
        lambda row: (
            row["cli"] == "grok"
            and "--session-id" in row["argv"]
            and row["cwd"].endswith("worktrees/worker")
        ),
        description="profile-owned grok launch",
    )
    argv = launched[-1]["argv"]
    assert "--auto-yes" in argv
    assert any(token == "--startup-note" for token in argv)
    assert any(
        token.startswith("read-") and token.endswith("bootstrap.json") for token in argv
    )
    assert launched[-1]["forwarded"] == {"FOIL_E2E_FORWARD_MARK": marker}

    # Values are never persisted: names only in durable state.
    for name, text in _state_texts(fleet):
        assert marker not in text, f"forwarded value persisted in {name}"
    plan = fleet.runner_plan("worker")
    assert plan["env_forward"] == ["FOIL_E2E_FORWARD_MARK", "FOIL_E2E_FORWARD_ABSENT"]

    # Resume re-resolves forwarded values at launch and uses profile resume argv.
    fleet.lifecycle("stop", "--seat", "worker").json()
    env["FOIL_E2E_FORWARD_MARK"] = f"{marker}-resumed"
    resumed = fleet.foil(
        "resume", *fleet.fleet_flags(), "--json", "--seat", "worker", env=env
    ).json()
    assert _seat_map(resumed)["worker"]["action"] == "resume_native"
    relaunched = fleet.wait_for_invocations(
        lambda row: (
            row["cli"] == "grok"
            and "--resume" in row["argv"]
            and row["cwd"].endswith("worktrees/worker")
        ),
        description="profile-owned grok resume",
    )
    assert relaunched[-1]["forwarded"] == {"FOIL_E2E_FORWARD_MARK": f"{marker}-resumed"}
    for name, text in _state_texts(fleet):
        assert f"{marker}-resumed" not in text, f"forwarded value persisted in {name}"


def test_builtin_adapters_remain_the_preset_without_extra_argv(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    record = json.loads(fleet.seat_record_path("lead").read_text(encoding="utf-8"))
    assert record["agent_kind"] == "grok_cli"
    assert record["extensions"]["profile"].get("profile_id") is None
    plan = fleet.runner_plan("lead")
    assert "env_forward" not in plan
    assert plan["argv"][1:3] == ["--model", "grok-4.6"]


def test_spawn_rejects_a_profile_with_credential_shaped_argv(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    profile_path = fleet.project / "bad-profile.toml"
    profile_path.write_text(
        """
schema_version = 1
id = "leaky"
cli = "grok"

[launch]
argv = ["grok", "--api-key", "sk-embedded-credential"]
""",
        encoding="utf-8",
    )

    spawned = fleet.spawn("worker", profile=str(profile_path))
    assert spawned.returncode != 0
    assert "secret" in spawned.stderr.lower()
    assert not (fleet.project / "worktrees" / "worker").exists()
    assert not fleet.seat_record_path("worker").exists()
    assert not (fleet.project / "worktrees").exists()
