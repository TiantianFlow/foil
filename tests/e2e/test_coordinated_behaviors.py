"""End-to-end coverage for coordinated fleet behaviors."""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

from tests.e2e.harness import OperatorFleet


def _seat_map(payload: dict) -> dict:
    return {seat["seat_id"]: seat for seat in payload["seats"]}


def test_two_seats_share_a_notepad_and_ack_it(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    written = fleet.foil(
        "notepad-write",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "sprint-brief",
        "--author",
        "implementer",
        "--body",
        "Build the mailbox ack path; reviewer challenges the plan.",
    ).json()
    assert written["notepad_id"] == "sprint-brief"
    assert written["duplicate"] is False
    read = fleet.foil(
        "notepad-read",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "sprint-brief",
    ).json()
    assert "mailbox ack path" in read["body"]
    ack = fleet.foil(
        "notepad-ack",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "sprint-brief",
        "--actor",
        "reviewer-challenger",
    ).json()
    assert ack["state"] == "acknowledged"
    path = (
        fleet.state
        / "v1"
        / "fleets"
        / fleet.fleet_id
        / "notepads"
        / "sprint-brief.json"
    )
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["body"] == read["body"]


def test_notepad_rejects_secrets_and_detects_duplicate_writes(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    secret = fleet.foil(
        "notepad-write",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "leaky",
        "--author",
        "operator",
        "--body",
        "sk-secret-should-not-persist",
    )
    assert secret.returncode != 0
    first = fleet.foil(
        "notepad-write",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "brief",
        "--author",
        "operator",
        "--body",
        "Shared brief for both seats.",
    ).json()
    second = fleet.foil(
        "notepad-write",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--notepad",
        "brief",
        "--author",
        "operator",
        "--body",
        "Shared brief for both seats.",
    ).json()
    assert first["duplicate"] is False
    assert second["duplicate"] is True


def test_memory_lesson_can_be_proposed_accepted_superseded_and_rejected(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    proposed = fleet.foil(
        "memory-propose",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-ack-first",
        "--author",
        "memory-curator",
        "--task",
        "quickstart-review",
        "--body",
        "Acknowledge mailbox messages before claiming the task is done.",
    ).json()
    assert proposed["state"] == "proposed"
    accepted = fleet.foil(
        "memory-accept",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-ack-first",
        "--actor",
        "manager",
    ).json()
    assert accepted["state"] == "accepted"
    superseded = fleet.foil(
        "memory-supersede",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-ack-first",
        "--author",
        "memory-curator",
        "--replacement",
        "lesson-ack-and-status",
        "--body",
        "Ack mail, then poll-status before stopping the seat.",
        "--task",
        "quickstart-review",
    ).json()
    assert superseded["state"] == "superseded"
    replacement = fleet.foil(
        "memory-status",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-ack-and-status",
    ).json()
    assert replacement["state"] == "proposed"
    rejected = fleet.foil(
        "memory-reject",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-ack-and-status",
        "--actor",
        "manager",
    ).json()
    assert rejected["state"] == "rejected"
    secret = fleet.foil(
        "memory-propose",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
        "--lesson",
        "lesson-secret",
        "--author",
        "memory-curator",
        "--task",
        "quickstart-review",
        "--body",
        "sk-this-must-not-become-a-lesson",
    )
    assert secret.returncode != 0


def test_operator_can_request_fresh_respawn_with_incarnation_lineage(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    launched = _seat_map(fleet.lifecycle("status").json())
    before = launched["implementer"]["registry"]
    resumed = fleet.foil(
        "resume",
        *fleet.fleet_flags(),
        "--json",
        "--fresh",
        "--seat",
        "implementer",
    ).json()
    after = _seat_map(resumed)["implementer"]
    assert after["action"] == "start_fresh"
    assert after["registry"]["incarnation_id"] != before["incarnation_id"]
    assert after["registry"]["previous_incarnation_id"] == before["incarnation_id"]
    assert after["registry"]["native_session_id"] != before["native_session_id"]
    assert after["state"] == "working"


def test_seat_can_spawn_from_profile_owned_cli_args(initialized: OperatorFleet) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    spawned = fleet.spawn(
        "implementer",
        "grok",
        isolated=True,
        role="implementer",
        extra=(
            "--session-capture",
            "generated_uuid",
            "--",
            "--model",
            "grok-4.6",
            "--session-id",
            "{native_session_id}",
        ),
    ).json()
    implementer = _seat_map(spawned)["implementer"]
    assert implementer["state"] == "working"
    grok = fleet.wait_for_invocations(
        lambda row: row["cli"] == "grok" and "--session-id" in row["argv"],
        description="profile-owned grok launch",
    )
    assert "grok-4.6" in grok[0]["argv"]


def test_seat_can_spawn_directly_from_a_markdown_persona(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    catalog = fleet.project / "personas"
    catalog.mkdir()
    persona = catalog / "reviewer.md"
    persona.write_text(
        "---\nname: Reviewer\ndescription: Challenges plans.\n---\n# Reviewer\n",
        encoding="utf-8",
    )

    listed = fleet.foil("catalog-list", "--path", str(catalog), "--json").json()
    assert [entry["name"] for entry in listed["personas"]] == ["Reviewer"]
    mapped = fleet.foil(
        "catalog-map", "--path", str(catalog), "--persona", "Reviewer", "--json"
    ).json()
    assert Path(mapped["path"]).samefile(persona)

    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    spawned = fleet.spawn(
        "reviewer",
        "grok",
        role=None,
        extra=("--role-file", mapped["path"]),
    ).json()
    assert _seat_map(spawned)["reviewer"]["state"] == "working"
    instructions = (
        fleet.state
        / "v1"
        / "fleets"
        / fleet.fleet_id
        / "adapter-state"
        / "reviewer"
        / "FOIL.md"
    ).read_text(encoding="utf-8")
    assert f"read the role file at `{persona.resolve()}`" in instructions
    assert "you are a worker seat" in instructions


def test_spawn_with_a_missing_role_file_creates_no_worktree(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    spawned = fleet.spawn(
        "implementer",
        "grok",
        role=None,
        extra=("--role-file", str(fleet.project / "missing.md")),
    )
    assert spawned.returncode != 0
    assert "role file" in spawned.stderr.lower()
    assert not (fleet.project / "worktrees" / "implementer").exists()
    assert not (fleet.project / "worktrees").exists()
    listed = fleet.foil("seat", "list", *fleet.fleet_flags(), "--json").json()
    assert [seat["seat_id"] for seat in listed["seats"]] == ["lead"]


def test_dispatch_ranks_live_seats_from_structured_usage_probes(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
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
    assert "active_load" in json.dumps(decision["evidence"])


def test_doctor_reports_prerequisites_and_a_live_plan(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    before = fleet.foil("doctor", *fleet.fleet_flags(), "--json").json()
    assert before["tmux"]["ok"] is True
    assert before["git"]["ok"] is True
    clis = {item["name"]: item for item in before["clis"]}
    assert clis["grok"]["ok"] is True
    assert clis["opencode"]["ok"] is True
    assert before["credentials_inspected"] is False
    assert before["plan"]["seats"] == []

    fleet.start_complementary_fleet()
    after = fleet.foil("doctor", *fleet.fleet_flags(), "--json").json()
    assert {seat["seat_id"] for seat in after["plan"]["seats"]} == {
        "lead",
        "implementer",
        "reviewer-challenger",
    }
    assert after["worktrees"]["ok"] is True


def test_isolated_workers_use_distinct_builder_worktrees(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    status = _seat_map(fleet.lifecycle("status").json())
    implementer = Path(status["implementer"]["registry"]["worktree_path"]).resolve()
    reviewer = Path(status["reviewer-challenger"]["registry"]["worktree_path"]).resolve()
    assert implementer != reviewer
    assert implementer.is_dir()
    assert reviewer.is_dir()


def test_operator_can_mark_a_live_seat_waiting_or_idle(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    waiting = fleet.foil(
        "set-state",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "implementer",
        "--state",
        "waiting",
    ).json()
    assert _seat_map(waiting)["implementer"]["state"] == "waiting"
    status = fleet.lifecycle("status").json()
    assert _seat_map(status)["implementer"]["state"] == "waiting"
    idle = fleet.foil(
        "set-state",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "implementer",
        "--state",
        "idle",
    ).json()
    assert _seat_map(idle)["implementer"]["state"] == "idle"
    polled = fleet.foil(
        "poll-status",
        "--state-dir",
        str(fleet.state),
        "--fleet",
        fleet.fleet_id,
    ).json()
    assert _seat_map(polled)["implementer"]["state"] == "idle"


def test_catalog_lists_local_markdown_personas_without_downloading(
    fleet: OperatorFleet, tmp_path: Path
) -> None:
    catalog = tmp_path / "personas"
    catalog.mkdir()
    (catalog / "reviewer.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: Engineering reviewer
            description: Challenges implementation from an independent context.
            ---
            # Engineering reviewer
            """
        ),
        encoding="utf-8",
    )
    listed = fleet.foil("catalog-list", "--path", str(catalog), "--json").json()
    assert listed["personas"][0]["name"] == "Engineering reviewer"
    mapped = fleet.foil(
        "catalog-map",
        "--path",
        str(catalog),
        "--persona",
        "Engineering reviewer",
        "--json",
    ).json()
    assert mapped["display_name"] == "Engineering reviewer"
    assert mapped["primary_specialization"]
    assert "cli" in mapped or "preset" in mapped
    assert "github.com" not in json.dumps(mapped)


def test_opencode_native_id_can_arrive_on_later_status(initialized: OperatorFleet) -> None:
    fleet = initialized
    (fleet.agent_home / "opencode-capture-delay-seconds").write_text("4", encoding="utf-8")
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    launched = _seat_map(
        fleet.spawn(
            "reviewer-challenger",
            "opencode",
            isolated=True,
            role="reviewer-challenger",
        ).json()
    )
    assert launched["reviewer-challenger"]["registry"]["native_session_id"] in (None, "")
    native = None
    for _ in range(40):
        status = _seat_map(fleet.lifecycle("status").json())
        native = status["reviewer-challenger"]["registry"]["native_session_id"]
        if native:
            break
        time.sleep(0.25)
    assert isinstance(native, str) and native.startswith("oc-")


def test_fleet_journey_succeeds_with_mcp_unavailable(initialized: OperatorFleet) -> None:
    fleet = initialized
    env = fleet.env()
    env.pop("MCP_SERVER", None)
    fleet.spawn("lead", "grok", lead=True, role="manager", env=env).json()
    fleet.spawn(
        "implementer",
        "grok",
        isolated=True,
        role="implementer",
        env=env,
    ).json()
    status = fleet.foil("status", *fleet.fleet_flags(), "--json", env=env).json()
    assert {seat["seat_id"] for seat in status["seats"]} == {"lead", "implementer"}


def test_repository_has_no_mcp_runtime_dependency() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "mcp" not in pyproject.lower() or "no MCP" in pyproject
    src = root / "src" / "foil"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "mcp.server" in text or "from mcp" in text or "import mcp" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
