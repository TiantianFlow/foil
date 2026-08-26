"""Declarative per-seat profile contract tests (schema v1)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from foil.adapters import CaptureKind
from foil.profiles import ProfileError, load_profile
from foil.runtime import RuntimeController
from foil.runtime import RuntimeError as LifecycleError
from tests.test_runtime_cli import (
    FakeTmux,
    generated_argv,
    make_fixture_executable,
    make_project,
    write_live_fleet,
)

COMPLETE_PROFILE = """
schema_version = 1
id = "fixture-profile"
cli = "fixture-agent"

[executable]
candidates = ["fixture-agent", "fixture-agent-next"]
version_argv = ["fixture-agent", "--version"]

[launch]
argv = [
  "fixture-agent",
  "launch",
  "--seat",
  "{seat_id}",
  "--cwd",
  "{working_directory}",
  "--store",
  "{adapter_state_dir}/sessions.json",
  "--native-id",
  "{native_session_id}",
]

[startup]
argv = ["Read {bootstrap_path} and its sibling FOIL.md before beginning."]

[resume]
supported = true
argv = [
  "fixture-agent",
  "resume",
  "--seat",
  "{seat_id}",
  "--cwd",
  "{working_directory}",
  "--store",
  "{adapter_state_dir}/sessions.json",
  "--native-id",
  "{native_session_id}",
]

[session_capture]
kind = "generated_uuid"

[permissions]
supervised = ["--ask"]
auto = ["--yes"]

[environment]
forward = ["FOIL_TEST_FORWARDED", "OPTIONAL_PROVIDER_KEY"]

[working]
isolated = true
"""


def _write_profile(tmp_path: Path, text: str = COMPLETE_PROFILE) -> Path:
    path = tmp_path / "profile.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_schema_matches_the_loader_contract() -> None:
    schema_path = (
        Path(__file__).parents[1] / "schemas" / "profile-v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["required"] == ["schema_version", "id", "cli"]
    assert schema["properties"]["schema_version"] == {"const": 1}
    assert set(schema["properties"]) == {
        "schema_version",
        "id",
        "cli",
        "executable",
        "launch",
        "startup",
        "resume",
        "session_capture",
        "permissions",
        "environment",
        "working",
    }
    capture_kinds = schema["properties"]["session_capture"]["properties"]["kind"]["enum"]
    assert capture_kinds == ["none", "generated_uuid", "command_json_list_delta"]
    forward_items = schema["properties"]["environment"]["properties"]["forward"][
        "items"
    ]
    assert forward_items["pattern"] == "^[A-Za-z_][A-Za-z0-9_]{0,127}$"


def test_load_profile_accepts_the_complete_contract(tmp_path: Path) -> None:
    profile = load_profile(_write_profile(tmp_path))

    assert profile.profile_id == "fixture-profile"
    assert profile.cli == "fixture-agent"
    assert profile.executable_candidates == ("fixture-agent", "fixture-agent-next")
    assert profile.version_argv == ("fixture-agent", "--version")
    assert profile.launch_argv is not None and profile.launch_argv[0] == "fixture-agent"
    assert profile.startup_argv == (
        "Read {bootstrap_path} and its sibling FOIL.md before beginning.",
    )
    assert profile.resume_supported is True
    assert profile.resume_argv is not None and profile.resume_argv[1] == "resume"
    assert profile.session_capture == "generated_uuid"
    assert profile.permission_supervised == ("--ask",)
    assert profile.permission_auto == ("--yes",)
    assert profile.environment_forward == ("FOIL_TEST_FORWARDED", "OPTIONAL_PROVIDER_KEY")
    assert profile.isolated is True


def test_load_profile_accepts_a_minimal_profile(tmp_path: Path) -> None:
    path = tmp_path / "minimal.toml"
    path.write_text(
        'schema_version = 1\nid = "minimal"\ncli = "fixture-agent"\n',
        encoding="utf-8",
    )

    profile = load_profile(path)

    assert profile.executable_candidates == ("fixture-agent",)
    assert profile.launch_argv is None
    assert profile.startup_argv is None
    assert profile.resume_supported is False
    assert profile.resume_argv is None
    assert profile.session_capture is None
    assert profile.permission_supervised == ()
    assert profile.permission_auto is None
    assert profile.environment_forward == ()
    assert profile.isolated is None


def test_load_profile_requires_an_existing_regular_file(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="does not exist"):
        load_profile(tmp_path / "missing.toml")
    with pytest.raises(ProfileError, match="regular non-symlink"):
        load_profile(tmp_path)
    target = _write_profile(tmp_path)
    link = tmp_path / "linked.toml"
    link.symlink_to(target)
    with pytest.raises(ProfileError, match="regular non-symlink"):
        load_profile(link)


def test_load_profile_rejects_unknown_fields_and_versions(tmp_path: Path) -> None:
    bad_version = tmp_path / "version.toml"
    bad_version.write_text(
        'schema_version = 2\nid = "x"\ncli = "fixture-agent"\n', encoding="utf-8"
    )
    with pytest.raises(ProfileError, match="schema version"):
        load_profile(bad_version)

    unknown = tmp_path / "unknown.toml"
    unknown.write_text(
        'schema_version = 1\nid = "x"\ncli = "fixture-agent"\nprovider = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="unknown fields"):
        load_profile(unknown)


def test_load_profile_requires_candidates_to_include_the_cli(tmp_path: Path) -> None:
    path = tmp_path / "candidates.toml"
    path.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[executable]
candidates = ["other-agent"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="must include the profile cli"):
        load_profile(path)


def test_load_profile_validates_resume_and_capture_combinations(tmp_path: Path) -> None:
    unsupported_with_argv = tmp_path / "resume.toml"
    unsupported_with_argv.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[resume]
supported = false
argv = ["fixture-agent", "resume"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="forbidden when resume is unsupported"):
        load_profile(unsupported_with_argv)

    capture_without_pointer = tmp_path / "capture.toml"
    capture_without_pointer.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[session_capture]
kind = "command_json_list_delta"
argv = ["fixture-agent", "session", "list", "--format", "json"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="id_pointer"):
        load_profile(capture_without_pointer)

    misplaced_fields = tmp_path / "misplaced.toml"
    misplaced_fields.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[session_capture]
kind = "generated_uuid"
id_pointer = "/id"
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="command_json_list_delta"):
        load_profile(misplaced_fields)


def test_load_profile_rejects_empty_auto_permissions(tmp_path: Path) -> None:
    path = tmp_path / "auto.toml"
    path.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[permissions]
auto = []
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="permissions.auto"):
        load_profile(path)


def test_environment_forward_declares_names_only(tmp_path: Path) -> None:
    with_values = tmp_path / "values.toml"
    with_values.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[environment]
forward = ["FOIL_TEST_FORWARDED=sk-inline-value"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="variable names only"):
        load_profile(with_values)

    duplicates = tmp_path / "duplicates.toml"
    duplicates.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[environment]
forward = ["FOIL_TEST_FORWARDED", "FOIL_TEST_FORWARDED"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="duplicates"):
        load_profile(duplicates)


def test_load_profile_rejects_credential_shaped_argv(tmp_path: Path) -> None:
    path = tmp_path / "secret.toml"
    path.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[launch]
argv = ["fixture-agent", "--token", "sk-profiled-secret"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="secret"):
        load_profile(path)


def test_load_profile_rejects_unknown_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "placeholder.toml"
    path.write_text(
        """
schema_version = 1
id = "x"
cli = "fixture-agent"

[launch]
argv = ["fixture-agent", "--home", "{user_home}"]
""",
        encoding="utf-8",
    )
    with pytest.raises(ProfileError, match="unknown placeholder"):
        load_profile(path)


def _profile_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RuntimeController, FakeTmux, Path]:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    tmux = FakeTmux()
    monkeypatch.setenv(
        "PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}"
    )
    controller = RuntimeController(state, fleet_id, tmux=tmux)
    return controller, tmux, executable


def _fleet_root(controller: RuntimeController) -> Path:
    return controller.state_root / "v1" / "fleets" / controller.fleet.fleet_id


def test_declarative_profile_owns_the_spawn_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    monkeypatch.setenv("FOIL_TEST_FORWARDED", "forwarded-test-value")
    profile_path = _write_profile(tmp_path)

    spawned = controller.spawn(
        seat_id="lead",
        lead=True,
        profile_file=str(profile_path),
        permission="auto",
    )

    assert {seat["seat_id"] for seat in spawned} == {"lead"}
    record = controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    profile = record.extensions["profile"]
    assert record.agent_kind == "fixture-profile"
    assert profile["profile_id"] == "fixture-profile"
    assert profile["cli"] == "fixture-agent"
    assert profile["environment_forward"] == [
        "FOIL_TEST_FORWARDED",
        "OPTIONAL_PROVIDER_KEY",
    ]
    # Profile-owned permission flags reach the launch argv.
    plan_path = _fleet_root(controller) / "runner-plans" / "lead.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["argv"][1] == "launch"
    assert Path(plan["argv"][0]).name == "fixture-agent"
    assert "--yes" in plan["argv"]
    assert any("Read " in token and "bootstrap.json" in token for token in plan["argv"])
    # The plan declares forwarded names only; values stay out of state.
    assert plan["env_forward"] == ["FOIL_TEST_FORWARDED", "OPTIONAL_PROVIDER_KEY"]
    assert "forwarded-test-value" not in plan_path.read_text(encoding="utf-8")
    # Values travel through tmux injection: present vars only.
    assert tmux.environments[-1] == {"FOIL_TEST_FORWARDED": "forwarded-test-value"}
    record_text = (
        _fleet_root(controller) / "seats" / "lead.json"
    ).read_text(encoding="utf-8")
    assert "forwarded-test-value" not in record_text
    events = (_fleet_root(controller) / "events" / "events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "forwarded-test-value" not in events


def test_remainder_argv_wins_over_declarative_launch_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    profile_path = _write_profile(tmp_path)

    controller.spawn(
        seat_id="lead",
        lead=True,
        profile_file=str(profile_path),
        launch_argv=generated_argv(),
    )

    plan = json.loads(
        (_fleet_root(controller) / "runner-plans" / "lead.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["argv"][1] == "launch"
    assert "--native-id" in plan["argv"]


def test_spawn_rejects_a_conflicting_cli_and_profile_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _profile_controller(tmp_path, monkeypatch)
    profile_path = _write_profile(tmp_path)

    with pytest.raises(LifecycleError, match="conflicts with the profile cli"):
        controller.spawn(
            seat_id="lead",
            cli="other-agent",
            launch_argv=generated_argv(),
            lead=True,
            profile_file=str(profile_path),
        )
    assert tmux.launches == []
    assert executable.name == "fixture-agent"


def test_spawn_without_cli_or_profile_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, _executable = _profile_controller(tmp_path, monkeypatch)

    with pytest.raises(LifecycleError, match="--cli or --profile"):
        controller.spawn(seat_id="lead", launch_argv=generated_argv(), lead=True)
    assert tmux.launches == []


def test_credential_shaped_argv_is_rejected_before_any_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, _executable = _profile_controller(tmp_path, monkeypatch)

    with pytest.raises(LifecycleError, match="unsafe launch data"):
        controller.spawn(
            seat_id="lead",
            cli="fixture-agent",
            launch_argv=("launch", "--token", "sk-remainder-secret"),
            lead=True,
        )

    assert tmux.launches == []
    fleet_root = _fleet_root(controller)
    with pytest.raises(FileNotFoundError):
        controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    assert not (fleet_root / "runner-plans" / "lead.json").exists()
    assert not (fleet_root / "adapter-state" / "lead").exists()
    assert not (tmp_path / "project" / "worktrees" / "lead").exists()


def test_profile_owned_permission_auto_flags_and_isolated_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    controller.spawn(
        seat_id="lead",
        cli="fixture-agent",
        launch_argv=generated_argv(),
        lead=True,
    )
    profile_path = _write_profile(tmp_path)

    spawned = controller.spawn(
        seat_id="worker",
        profile_file=str(profile_path),
        permission="auto",
    )

    assert {seat["seat_id"] for seat in spawned} == {"worker"}
    record = controller.registry.read_seat(controller.fleet.fleet_id, "worker")
    # working.isolated = true in the profile owned the workdir behavior.
    assert record.worktree_path == str(tmp_path / "project" / "worktrees" / "worker")
    plan = json.loads(
        (_fleet_root(controller) / "runner-plans" / "worker.json").read_text(
            encoding="utf-8"
        )
    )
    assert "--yes" in plan["argv"]
    assert len(tmux.launches) == 2


def test_profile_owned_capture_command_uses_the_file_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    profile_path = tmp_path / "delta.toml"
    profile_path.write_text(
        """
schema_version = 1
id = "delta-profile"
cli = "fixture-agent"

[launch]
argv = [
  "fixture-agent",
  "launch",
  "--seat",
  "{seat_id}",
  "--cwd",
  "{working_directory}",
  "--store",
  "{adapter_state_dir}/sessions.json",
]

[session_capture]
kind = "command_json_list_delta"
argv = [
  "fixture-agent",
  "session",
  "list",
  "--format",
  "json",
  "--store",
  "{adapter_state_dir}/sessions.json",
]
id_pointer = "/id"
cwd_pointer = "/directory"
""",
        encoding="utf-8",
    )

    def capture_native(runtime, baseline, *, wait_seconds: float) -> str:
        del baseline, wait_seconds
        return f"native-{runtime.seat.seat_id}"

    monkeypatch.setattr(controller, "_capture_delta", capture_native)
    controller.spawn(seat_id="lead", lead=True, profile_file=str(profile_path))

    record = controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    assert record.native_session_id == "native-lead"
    runtime = next(
        item for item in controller._runtimes() if item.seat.seat_id == "lead"
    )
    assert runtime.adapter.session_capture.kind is CaptureKind.COMMAND_JSON_LIST_DELTA
    assert runtime.adapter.session_capture.argv is not None
    assert runtime.adapter.session_capture.argv[:4] == (
        "fixture-agent",
        "session",
        "list",
        "--format",
    )
