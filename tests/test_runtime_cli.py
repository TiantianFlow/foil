"""Runtime continuity CLI integration tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil.fleet import FleetError, FleetRecord, FleetStore
from foil.registry import TmuxTarget
from foil.runtime import RuntimeController, SeatRuntime
from foil.runtime import RuntimeError as LifecycleError
from foil.tmux import ProbeResult, ProbeState


def run_foil(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "foil", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def with_adapter_path(executable: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{executable.parent}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def without_adapter_path(executable: Path) -> dict[str, str]:
    environment = os.environ.copy()
    parent = executable.parent.resolve()
    parts = [
        part
        for part in environment.get("PATH", "").split(os.pathsep)
        if part and Path(part).resolve() != parent
    ]
    environment["PATH"] = os.pathsep.join(parts)
    return environment


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def make_fixture_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "fixture-agent"
    executable.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            import time
            from pathlib import Path

            args = sys.argv[1:]
            if args == ["--version"]:
                print("fixture 1.0")
                raise SystemExit
            store = Path(args[args.index("--store") + 1])
            store.parent.mkdir(parents=True, exist_ok=True)
            if args[:3] == ["session", "list", "--format"]:
                if store.exists():
                    print(store.read_text())
                raise SystemExit
            seat = args[args.index("--seat") + 1]
            cwd = args[args.index("--cwd") + 1]
            if args[0] == "launch" and "--native-id" not in args:
                store.write_text(json.dumps([{"id": f"native-{seat}", "directory": cwd}]))
            Path(store.parent / f"{seat}-{args[0]}.started").write_text(str(os.getpid()))
            time.sleep(300)
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    git("init", "-b", "runtime-test", cwd=project)
    return project


def write_live_fleet(tmp_path: Path, project: Path) -> tuple[Path, str]:
    fleet_id = f"fleet-{uuid.uuid4().hex}"
    state = tmp_path / "state"
    FleetStore(state).write(
        FleetRecord(
            fleet_id=fleet_id,
            project_root=str(project.resolve()),
            updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            display_name="Runtime fixtures",
        )
    )
    return state, fleet_id


def generated_argv() -> tuple[str, ...]:
    return (
        "launch",
        "--seat",
        "{seat_id}",
        "--cwd",
        "{working_directory}",
        "--store",
        "{adapter_state_dir}/sessions.json",
        "--native-id",
        "{native_session_id}",
    )


def generated_resume() -> tuple[str, ...]:
    return (
        "resume",
        "--seat",
        "{seat_id}",
        "--cwd",
        "{working_directory}",
        "--store",
        "{adapter_state_dir}/sessions.json",
        "--native-id",
        "{native_session_id}",
    )


def discovery_argv() -> tuple[str, ...]:
    return (
        "launch",
        "--seat",
        "{seat_id}",
        "--cwd",
        "{working_directory}",
        "--store",
        "{adapter_state_dir}/sessions.json",
    )


def discovery_list_argv(command: str) -> tuple[str, ...]:
    return (
        command,
        "session",
        "list",
        "--format",
        "json",
        "--store",
        "{adapter_state_dir}/sessions.json",
    )


def spawn_fixture_seats(
    controller: RuntimeController,
    executable: Path,
    *,
    command: str | None = None,
) -> None:
    cli = command or str(executable)
    controller.spawn(
        seat_id="generated-seat",
        cli=cli,
        launch_argv=generated_argv(),
        resume_argv=generated_resume(),
        session_capture="generated_uuid",
        lead=True,
        model="fixture/model",
    )
    controller.spawn(
        seat_id="discovery-seat",
        cli=cli,
        launch_argv=discovery_argv(),
        resume_argv=generated_resume(),
        session_capture="command_json_list_delta",
        session_list_argv=discovery_list_argv(cli),
        session_id_pointer="/id",
        session_cwd_pointer="/directory",
        model="fixture/model",
    )


def discovery_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RuntimeController, SeatRuntime, Path, Path]:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    tmux = FakeTmux()
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    controller = RuntimeController(state, fleet_id, tmux=tmux)
    controller.spawn(
        seat_id="discovery-seat",
        cli=str(executable),
        launch_argv=discovery_argv(),
        session_capture="command_json_list_delta",
        session_list_argv=discovery_list_argv(str(executable)),
        session_id_pointer="/id",
        session_cwd_pointer="/directory",
        lead=True,
        model="fixture/model",
    )
    runtime = next(
        item for item in controller._runtimes() if item.seat.seat_id == "discovery-seat"
    )
    session_store = (
        state
        / "v1"
        / "fleets"
        / fleet_id
        / "adapter-state"
        / "discovery-seat"
        / "sessions.json"
    )
    session_store.parent.mkdir(parents=True, exist_ok=True)
    return controller, runtime, session_store, project


def test_discovery_baseline_only_retains_sessions_for_seat_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, runtime, session_store, project = discovery_runtime(tmp_path, monkeypatch)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    session_store.write_text(
        json.dumps(
            [
                {"id": "native-project", "directory": str(project)},
                {"id": "native-unrelated", "directory": str(unrelated)},
                {"id": 42, "directory": str(unrelated)},
            ]
        ),
        encoding="utf-8",
    )

    assert controller._session_ids(runtime) == {"native-project"}


@pytest.mark.parametrize(
    "record",
    [
        {"id": 42, "directory": "{project}"},
        {"id": "", "directory": "{project}"},
        {"id": "native-project", "directory": 42},
    ],
)
def test_discovery_baseline_rejects_malformed_selected_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, object],
) -> None:
    controller, runtime, session_store, project = discovery_runtime(tmp_path, monkeypatch)
    selected = {
        key: str(project) if value == "{project}" else value
        for key, value in record.items()
    }
    session_store.write_text(json.dumps([selected]), encoding="utf-8")

    with pytest.raises(LifecycleError, match="session discovery"):
        controller._session_ids(runtime)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_spawn_status_stop_and_native_resume_two_fixture_seats(tmp_path: Path) -> None:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    env = with_adapter_path(executable)
    flags = ("--state-dir", str(state), "--fleet", fleet_id, "--json")

    try:
        controller = RuntimeController(state, fleet_id)
        spawn_fixture_seats(controller, executable)

        status = run_foil("status", *flags, env=env)
        assert status.returncode == 0, status.stderr
        records = {seat["seat_id"]: seat for seat in json.loads(status.stdout)["seats"]}
        assert records["discovery-seat"]["registry"]["native_session_id"] == (
            "native-discovery-seat"
        )
        assert uuid.UUID(records["generated-seat"]["registry"]["native_session_id"])
        assert all(seat["state"] == "working" for seat in records.values())

        revived = run_foil("resume", *flags, env=env)
        assert revived.returncode == 0, revived.stderr
        assert {seat["action"] for seat in json.loads(revived.stdout)["seats"]} == {
            "revive_tmux"
        }

        stopped = run_foil("seat", "stop", *flags, "--all", env=env)
        assert stopped.returncode == 0, stopped.stderr
        assert all(seat["state"] == "exited" for seat in json.loads(stopped.stdout)["seats"])

        resumed = run_foil("resume", *flags, env=env)
        assert resumed.returncode == 0, resumed.stderr
        assert {seat["action"] for seat in json.loads(resumed.stdout)["seats"]} == {
            "resume_native"
        }

        events_path = state / "v1" / "fleets" / fleet_id / "events" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        assert {event["event_type"] for event in events} >= {
            "spawn_result",
            "stop_result",
            "resume_decision",
            "resume_result",
        }
        assert all("argv" not in event for event in events)
    finally:
        run_foil("seat", "stop", *flags, "--all", env=env)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_spawn_without_git_identity_does_not_start_tmux(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state, fleet_id = write_live_fleet(tmp_path, project)
    executable = make_fixture_executable(tmp_path)
    launched = run_foil(
        "seat",
        "spawn",
        "--state-dir",
        str(state),
        "--fleet",
        fleet_id,
        "--json",
        "--lead",
        "--seat",
        "lead",
        "--cli",
        str(executable),
        "--",
        *generated_argv(),
        env=with_adapter_path(executable),
    )
    assert launched.returncode != 0
    assert "git" in launched.stderr.lower() or "worktree" in launched.stderr.lower()


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_stop_refuses_mismatched_tmux_user_option_marker(tmp_path: Path) -> None:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    env = with_adapter_path(executable)
    flags = ("--state-dir", str(state), "--fleet", fleet_id, "--json")
    controller = RuntimeController(state, fleet_id)
    controller.spawn(
        seat_id="discovery-seat",
        cli=str(executable),
        launch_argv=discovery_argv(),
        session_capture="command_json_list_delta",
        session_list_argv=discovery_list_argv(str(executable)),
        session_id_pointer="/id",
        session_cwd_pointer="/directory",
        lead=True,
        model="fixture/model",
    )
    record_path = state / "v1" / "fleets" / fleet_id / "seats" / "discovery-seat.json"
    record = json.loads(record_path.read_text())
    target = f"{record['tmux']['session_name']}:{record['tmux']['window_name']}"

    try:
        subprocess.run(
            [
                "tmux",
                "set-option",
                "-w",
                "-t",
                target,
                "@foil-seat-id",
                "wrong-seat",
            ],
            check=True,
        )
        stopped = run_foil("seat", "stop", *flags, "--seat", "discovery-seat", env=env)
        assert stopped.returncode != 0
        assert "unverified" in stopped.stderr
        assert subprocess.run(
            ["tmux", "has-session", "-t", record["tmux"]["session_name"]],
            check=False,
        ).returncode == 0
    finally:
        subprocess.run(
            ["tmux", "kill-session", "-t", record["tmux"]["session_name"]],
            check=False,
        )


def test_runtime_help_is_composable_and_documents_json_contract() -> None:
    result = run_foil("--help")

    assert result.returncode == 0
    for command in ("seat", "status", "resume", "poll-status"):
        assert command in result.stdout
    assert "launch" not in result.stdout

    for command in ("status", "resume"):
        help_result = run_foil(command, "--help")
        assert help_result.returncode == 0
        assert "--state-dir" in help_result.stdout
        assert "--fleet" in help_result.stdout
        assert "--json" in help_result.stdout
        assert "--config" not in help_result.stdout

    seat_help = run_foil("seat", "--help")
    assert seat_help.returncode == 0
    for name in ("spawn", "list", "inspect", "stop", "remove", "wake"):
        assert name in seat_help.stdout


class FakeTmux:
    def __init__(self) -> None:
        self.dead = False
        self.identity_matches = True
        self.launches: list[TmuxTarget] = []

    def launch(
        self,
        *,
        fleet_id: str,
        seat_id: str,
        session_name: str,
        window_name: str,
        working_directory: Path,
        runner_argv: list[str],
    ) -> TmuxTarget:
        del fleet_id, seat_id, working_directory, runner_argv
        target = TmuxTarget(
            session_name=session_name,
            window_name=window_name,
            session_id=f"${len(self.launches) + 1}",
            window_id=f"@{len(self.launches) + 1}",
        )
        self.launches.append(target)
        return target

    def probe(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> ProbeResult:
        del fleet_id, seat_id
        if self.dead:
            return ProbeResult(ProbeState.DEAD, False)
        return ProbeResult(ProbeState.ALIVE, self.identity_matches, target)

    def stop_verified(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        del fleet_id, seat_id
        if self.dead:
            return False
        self.dead = True
        self.launches = [item for item in self.launches if item != target]
        return True

    def abandon_window(self, target: TmuxTarget) -> None:
        self.launches = [item for item in self.launches if item != target]
        self.dead = True


def _bare_name_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RuntimeController, FakeTmux, Path]:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    tmux = FakeTmux()
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    controller = RuntimeController(state, fleet_id, tmux=tmux)
    return controller, tmux, executable


def test_spawn_rejects_duplicate_and_worker_before_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)

    def capture_native(runtime: SeatRuntime, baseline: set[str], *, wait_seconds: float) -> str:
        del baseline, wait_seconds
        return f"native-{runtime.seat.seat_id}"

    monkeypatch.setattr(controller, "_capture_delta", capture_native)
    with pytest.raises(LifecycleError, match="lead seat"):
        controller.spawn(
            seat_id="worker",
            cli=executable.name,
            launch_argv=generated_argv(),
        )
    first = controller.spawn(
        seat_id="generated-seat",
        cli=executable.name,
        launch_argv=generated_argv(),
        resume_argv=generated_resume(),
        lead=True,
    )
    assert {seat["seat_id"] for seat in first} == {"generated-seat"}
    second = controller.spawn(
        seat_id="discovery-seat",
        cli=executable.name,
        launch_argv=discovery_argv(),
        session_capture="command_json_list_delta",
        session_list_argv=discovery_list_argv(executable.name),
        session_id_pointer="/id",
        session_cwd_pointer="/directory",
    )
    assert {seat["seat_id"] for seat in second} == {"discovery-seat"}
    assert len(tmux.launches) == 2
    with pytest.raises(LifecycleError, match="already registered"):
        controller.spawn(
            seat_id="generated-seat",
            cli=executable.name,
            launch_argv=generated_argv(),
        )


def test_worker_env_cannot_claim_operator_or_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)
    controller.spawn(
        seat_id="lead",
        cli=executable.name,
        launch_argv=generated_argv(),
        lead=True,
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer")
    for actor in (None, "operator", "lead"):
        with pytest.raises(FleetError):
            controller.spawn(
                seat_id="intruder",
                cli=executable.name,
                launch_argv=generated_argv(),
                actor=actor,
            )
    assert len(tmux.launches) == 1
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    worker = controller.spawn(
        seat_id="implementer",
        cli=executable.name,
        launch_argv=generated_argv(),
    )
    assert {seat["seat_id"] for seat in worker} == {"implementer"}


def test_shared_cwd_is_required_for_a_second_project_root_seat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)
    controller.spawn(
        seat_id="lead",
        cli=executable.name,
        launch_argv=generated_argv(),
        lead=True,
    )
    with pytest.raises(LifecycleError, match="already used"):
        controller.spawn(
            seat_id="roommate",
            cli=executable.name,
            launch_argv=generated_argv(),
            isolated=False,
        )
    shared = controller.spawn(
        seat_id="roommate",
        cli=executable.name,
        launch_argv=generated_argv(),
        shared_cwd=True,
    )
    assert {seat["seat_id"] for seat in shared} == {"roommate"}
    assert len(tmux.launches) == 2
    lead_root = Path(controller.inspect("lead")["registry"]["working_directory"])
    assert not (lead_root / "FOIL.md").exists()
    bootstrap = Path(controller.inspect("roommate")["bootstrap"]["state_dir"])
    assert (
        bootstrap
        / "v1"
        / "fleets"
        / controller.fleet.fleet_id
        / "adapter-state"
        / "roommate"
        / "FOIL.md"
    ).is_file()


def test_failed_registry_write_stops_the_new_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)

    def boom(record):
        raise RuntimeError("injected registry failure")

    monkeypatch.setattr(controller.registry, "write_seat", boom)
    with pytest.raises(RuntimeError, match="injected registry failure"):
        controller.spawn(
            seat_id="lead",
            cli=executable.name,
            launch_argv=generated_argv(),
            lead=True,
        )
    assert tmux.launches == []
    with pytest.raises(FileNotFoundError):
        controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    events = (
        controller.state_root
        / "v1"
        / "fleets"
        / controller.fleet.fleet_id
        / "events"
        / "events.jsonl"
    )
    assert "spawn_failed" in events.read_text()


def test_fresh_resume_refuses_unverified_tmux_instead_of_orphaning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)
    monkeypatch.setattr(
        controller,
        "_capture_delta",
        lambda runtime, baseline, *, wait_seconds: f"native-{runtime.seat.seat_id}",
    )
    controller.spawn(
        seat_id="generated-seat",
        cli=executable.name,
        launch_argv=generated_argv(),
        resume_argv=generated_resume(),
        lead=True,
    )
    assert len(tmux.launches) == 1
    tmux.identity_matches = False
    with pytest.raises(LifecycleError, match="unverified"):
        controller.resume(seat_id="generated-seat", force_fresh=True)
    assert len(tmux.launches) == 1


def test_dead_tmux_status_and_native_resume_survive_transient_adapter_path_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)

    def capture_native(runtime: SeatRuntime, baseline: set[str], *, wait_seconds: float) -> str:
        del baseline, wait_seconds
        return f"native-{runtime.seat.seat_id}"

    monkeypatch.setattr(controller, "_capture_delta", capture_native)
    spawn_fixture_seats(controller, executable, command=executable.name)
    assert all(seat["registry"]["native_session_id"] for seat in controller.status())

    tmux.dead = True
    stripped = os.pathsep.join(
        part
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part and Path(part).resolve() != executable.parent.resolve()
    )
    monkeypatch.setenv("PATH", stripped)
    assert shutil.which(executable.name) is None

    status = controller.status()
    assert {seat["seat_id"]: seat["state"] for seat in status} == {
        "discovery-seat": "exited",
        "generated-seat": "exited",
    }

    resumed = controller.resume()
    assert {seat["seat_id"] for seat in resumed} == {"discovery-seat", "generated-seat"}
    assert all(seat["action"] == "resume_native" for seat in resumed)
    assert all(seat["state"] == "working" for seat in resumed)
    plans = list((tmp_path / "state").glob("v1/fleets/*/runner-plans/*.json"))
    assert len(plans) == 2
    for plan in plans:
        argv = json.loads(plan.read_text(encoding="utf-8"))["argv"]
        assert Path(argv[0]).resolve() == executable.resolve()
        assert argv[1] == "resume"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_cli_dead_tmux_status_and_native_resume_survive_transient_adapter_path_loss(
    tmp_path: Path,
) -> None:
    executable = make_fixture_executable(tmp_path)
    project = make_project(tmp_path)
    state, fleet_id = write_live_fleet(tmp_path, project)
    launch_env = with_adapter_path(executable)
    lost_env = without_adapter_path(executable)
    flags = ("--state-dir", str(state), "--fleet", fleet_id, "--json")
    assert shutil.which(executable.name, path=lost_env["PATH"]) is None

    try:
        controller = RuntimeController(state, fleet_id)
        spawn_fixture_seats(controller, executable)
        status_live = controller.status()
        session_names = {
            seat["registry"]["tmux"]["session_name"] for seat in status_live
        }
        assert len(session_names) == 1
        subprocess.run(
            ["tmux", "kill-session", "-t", session_names.pop()],
            check=False,
            capture_output=True,
        )

        status = run_foil("status", *flags, env=lost_env)
        assert status.returncode == 0, status.stderr
        status_payload = json.loads(status.stdout)
        assert {seat["seat_id"] for seat in status_payload["seats"]} == {
            "discovery-seat",
            "generated-seat",
        }
        assert all(seat["state"] == "exited" for seat in status_payload["seats"])

        resumed = run_foil("resume", *flags, env=lost_env)
        assert resumed.returncode == 0, resumed.stderr
        resume_payload = json.loads(resumed.stdout)
        assert {seat["action"] for seat in resume_payload["seats"]} == {"resume_native"}
        assert all(seat["state"] == "working" for seat in resume_payload["seats"])
    finally:
        run_foil("seat", "stop", *flags, "--all", env=launch_env)


def test_auto_permission_fails_on_profile_owned_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, executable = _bare_name_controller(tmp_path, monkeypatch)
    with pytest.raises(LifecycleError, match="unsupported"):
        controller.spawn(
            seat_id="lead",
            cli=executable.name,
            launch_argv=generated_argv(),
            lead=True,
            permission="auto",
        )
    with pytest.raises(FileNotFoundError):
        controller.registry.read_seat(controller.fleet.fleet_id, "lead")
