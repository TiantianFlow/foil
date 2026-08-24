"""Runtime continuity CLI integration tests (CAP-012–CAP-019, CAP-024, CAP-029–CAP-034)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from foil.registry import TmuxTarget
from foil.runtime import RuntimeController, SeatRuntime
from foil.runtime import RuntimeError as LifecycleError
from foil.runtime_config import load_fleet_config
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


def write_adapters(
    tmp_path: Path,
    executable: Path,
    *,
    command_name: str | None = None,
) -> Path:
    adapter_dir = tmp_path / "adapters"
    adapter_dir.mkdir()
    command = command_name or str(executable)
    generated_launch = json.dumps(
        [
            command,
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
    )
    generated_resume = generated_launch.replace('"launch"', '"resume"', 1)
    discovered_launch = json.dumps(
        [
            command,
            "launch",
            "--seat",
            "{seat_id}",
            "--cwd",
            "{working_directory}",
            "--store",
            "{adapter_state_dir}/sessions.json",
        ]
    )
    discovered_resume = generated_resume
    discovery_argv = json.dumps(
        [
            command,
            "session",
            "list",
            "--format",
            "json",
            "--store",
            "{adapter_state_dir}/sessions.json",
        ]
    )
    common = f"""
schema_version = 1
observed_version = "1.0"
models = ["fixture/model"]
skill = "skills/adapters/fixture/SKILL.md"

[executable]
candidates = [{json.dumps(command)}]
version_argv = [{json.dumps(command)}, "--version"]
"""
    (adapter_dir / "generated.toml").write_text(
        (
            """
id = "fixture-generated"
"""
            + common
            + f"""
[launch]
argv = {generated_launch}

[resume]
supported = true
argv = {generated_resume}

[session_capture]
kind = "generated_uuid"
"""
        ),
        encoding="utf-8",
    )
    (adapter_dir / "discovery.toml").write_text(
        (
            """
id = "fixture-discovery"
"""
            + common
            + f"""
[launch]
argv = {discovered_launch}

[resume]
supported = true
argv = {discovered_resume}

[session_capture]
kind = "command_json_list_delta"
argv = {discovery_argv}
id_pointer = "/id"
cwd_pointer = "/directory"
"""
        ),
        encoding="utf-8",
    )
    return adapter_dir


def make_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "project"
    project.mkdir()
    git("init", "-b", "runtime-test", cwd=project)
    return project, "runtime-test"


def write_fleet(
    tmp_path: Path,
    project: Path,
    branch: str,
    adapter_dir: Path,
) -> tuple[Path, str]:
    fleet_id = f"fleet-{uuid.uuid4().hex}"
    config = tmp_path / "fleet.toml"
    config.write_text(
        f"""
schema_version = 1
fleet_id = {json.dumps(fleet_id)}
display_name = "Runtime fixtures"
adapter_paths = [{json.dumps(str(adapter_dir))}]

[[usage_pools]]
id = "generated-pool"
adapter = "fixture-generated"
model = "fixture/model"

[[usage_pools]]
id = "discovery-pool"
adapter = "fixture-discovery"
model = "fixture/model"

[[seats]]
id = "generated-seat"
display_name = "Generated"
usage_pool_id = "generated-pool"
working_directory = {json.dumps(str(project))}
worktree_path = {json.dumps(str(project))}
git_branch = {json.dumps(branch)}

[[seats]]
id = "discovery-seat"
display_name = "Discovery"
usage_pool_id = "discovery-pool"
working_directory = {json.dumps(str(project))}
worktree_path = {json.dumps(str(project))}
git_branch = {json.dumps(branch)}
""",
        encoding="utf-8",
    )
    return config, fleet_id


def discovery_runtime(
    tmp_path: Path,
) -> tuple[RuntimeController, SeatRuntime, Path, Path]:
    executable = make_fixture_executable(tmp_path)
    adapters = write_adapters(tmp_path, executable)
    project, branch = make_project(tmp_path)
    config_path, fleet_id = write_fleet(tmp_path, project, branch, adapters)
    state = tmp_path / "state"
    controller = RuntimeController(load_fleet_config(config_path), state)
    runtime = next(
        runtime
        for runtime in controller._runtimes()
        if runtime.seat.seat_id == "discovery-seat"
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
    session_store.parent.mkdir(parents=True)
    return controller, runtime, session_store, project


def test_discovery_baseline_only_retains_sessions_for_seat_cwd(tmp_path: Path) -> None:
    controller, runtime, session_store, project = discovery_runtime(tmp_path)
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
    record: dict[str, object],
) -> None:
    controller, runtime, session_store, project = discovery_runtime(tmp_path)
    selected = {
        key: str(project) if value == "{project}" else value
        for key, value in record.items()
    }
    session_store.write_text(json.dumps([selected]), encoding="utf-8")

    with pytest.raises(LifecycleError, match="session discovery"):
        controller._session_ids(runtime)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_launch_status_stop_and_native_resume_two_fixture_pools(tmp_path: Path) -> None:
    executable = make_fixture_executable(tmp_path)
    adapters = write_adapters(tmp_path, executable)
    project, branch = make_project(tmp_path)
    config, fleet_id = write_fleet(tmp_path, project, branch, adapters)
    state = tmp_path / "state"

    try:
        launched = run_foil(
            "launch", "--config", str(config), "--state-dir", str(state), "--json"
        )
        assert launched.returncode == 0, launched.stderr
        launch_payload = json.loads(launched.stdout)
        assert [seat["seat_id"] for seat in launch_payload["seats"]] == [
            "discovery-seat",
            "generated-seat",
        ]

        status = run_foil(
            "status", "--config", str(config), "--state-dir", str(state), "--json"
        )
        assert status.returncode == 0, status.stderr
        status_payload = json.loads(status.stdout)
        records = {seat["seat_id"]: seat for seat in status_payload["seats"]}
        assert records["discovery-seat"]["registry"]["native_session_id"] == (
            "native-discovery-seat"
        )
        assert uuid.UUID(records["generated-seat"]["registry"]["native_session_id"])
        assert {seat["usage_pool_id"] for seat in records.values()} == {
            "generated-pool",
            "discovery-pool",
        }
        assert all(seat["state"] == "working" for seat in records.values())

        revived = run_foil(
            "resume", "--config", str(config), "--state-dir", str(state), "--json"
        )
        assert revived.returncode == 0, revived.stderr
        assert {seat["action"] for seat in json.loads(revived.stdout)["seats"]} == {
            "revive_tmux"
        }

        stopped = run_foil(
            "stop", "--config", str(config), "--state-dir", str(state), "--json"
        )
        assert stopped.returncode == 0, stopped.stderr
        assert all(seat["state"] == "exited" for seat in json.loads(stopped.stdout)["seats"])

        resumed = run_foil(
            "resume", "--config", str(config), "--state-dir", str(state), "--json"
        )
        assert resumed.returncode == 0, resumed.stderr
        assert {seat["action"] for seat in json.loads(resumed.stdout)["seats"]} == {
            "resume_native"
        }

        events_path = state / "v1" / "fleets" / fleet_id / "events" / "events.jsonl"
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        assert {event["event_type"] for event in events} >= {
            "launch_result",
            "stop_result",
            "resume_decision",
            "resume_result",
        }
        assert all("argv" not in event for event in events)
    finally:
        run_foil("stop", "--config", str(config), "--state-dir", str(state), "--json")


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_launch_rejects_branch_mismatch_before_tmux_mutation(tmp_path: Path) -> None:
    executable = make_fixture_executable(tmp_path)
    adapters = write_adapters(tmp_path, executable)
    project, _branch = make_project(tmp_path)
    config, fleet_id = write_fleet(tmp_path, project, "wrong-branch", adapters)
    state = tmp_path / "state"

    launched = run_foil(
        "launch", "--config", str(config), "--state-dir", str(state), "--json"
    )

    assert launched.returncode != 0
    assert "branch" in launched.stderr.lower()
    probe = subprocess.run(
        ["tmux", "has-session", "-t", f"foil-runtime-fixtures-{fleet_id[-8:]}"],
        capture_output=True,
        check=False,
    )
    assert probe.returncode != 0


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_stop_refuses_mismatched_tmux_user_option_marker(tmp_path: Path) -> None:
    executable = make_fixture_executable(tmp_path)
    adapters = write_adapters(tmp_path, executable)
    project, branch = make_project(tmp_path)
    config, fleet_id = write_fleet(tmp_path, project, branch, adapters)
    state = tmp_path / "state"
    launched = run_foil(
        "launch", "--config", str(config), "--state-dir", str(state), "--json"
    )
    assert launched.returncode == 0, launched.stderr
    record_path = (
        state
        / "v1"
        / "fleets"
        / fleet_id
        / "seats"
        / "discovery-seat.json"
    )
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
        stopped = run_foil(
            "stop", "--config", str(config), "--state-dir", str(state), "--json"
        )
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
    for command in ("launch", "status", "stop", "resume", "poll-status"):
        assert command in result.stdout

    for command in ("launch", "status", "stop", "resume"):
        help_result = run_foil(command, "--help")
        assert help_result.returncode == 0
        assert "--config" in help_result.stdout
        assert "--state-dir" in help_result.stdout
        assert "--json" in help_result.stdout


class FakeTmux:
    def __init__(self) -> None:
        self.dead = False
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
        return ProbeResult(ProbeState.ALIVE, True, target)

    def stop_verified(self, fleet_id: str, seat_id: str, target: TmuxTarget) -> bool:
        del fleet_id, seat_id, target
        if self.dead:
            return False
        self.dead = True
        return True


def _bare_name_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RuntimeController, FakeTmux, Path]:
    executable = make_fixture_executable(tmp_path)
    adapters = write_adapters(tmp_path, executable, command_name=executable.name)
    project, branch = make_project(tmp_path)
    config_path, _fleet_id = write_fleet(tmp_path, project, branch, adapters)
    tmux = FakeTmux()
    monkeypatch.setenv("PATH", f"{executable.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    controller = RuntimeController(load_fleet_config(config_path), tmp_path / "state", tmux=tmux)
    return controller, tmux, executable


def test_dead_tmux_status_and_native_resume_survive_transient_adapter_path_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, tmux, executable = _bare_name_controller(tmp_path, monkeypatch)

    def capture_native(runtime: SeatRuntime, baseline: set[str], *, wait_seconds: float) -> str:
        del baseline, wait_seconds
        return f"native-{runtime.seat.seat_id}"

    monkeypatch.setattr(controller, "_capture_delta", capture_native)
    launched = controller.launch()
    assert {seat["seat_id"] for seat in launched} == {"discovery-seat", "generated-seat"}
    assert all(seat["registry"]["native_session_id"] for seat in launched)

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
    assert all(seat["registry"]["native_session_id"] for seat in resumed)
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
    adapters = write_adapters(tmp_path, executable, command_name=executable.name)
    project, branch = make_project(tmp_path)
    config, _fleet_id = write_fleet(tmp_path, project, branch, adapters)
    state = tmp_path / "state"
    launch_env = with_adapter_path(executable)
    lost_env = without_adapter_path(executable)
    assert shutil.which(executable.name, path=lost_env["PATH"]) is None

    try:
        launched = run_foil(
            "launch", "--config", str(config), "--state-dir", str(state), "--json", env=launch_env
        )
        assert launched.returncode == 0, launched.stderr
        launch_payload = json.loads(launched.stdout)
        session_names = {
            seat["registry"]["tmux"]["session_name"] for seat in launch_payload["seats"]
        }
        assert len(session_names) == 1
        subprocess.run(
            ["tmux", "kill-session", "-t", session_names.pop()],
            check=False,
            capture_output=True,
        )

        status = run_foil(
            "status", "--config", str(config), "--state-dir", str(state), "--json", env=lost_env
        )
        assert status.returncode == 0, status.stderr
        status_payload = json.loads(status.stdout)
        assert {seat["seat_id"] for seat in status_payload["seats"]} == {
            "discovery-seat",
            "generated-seat",
        }
        assert all(seat["state"] == "exited" for seat in status_payload["seats"])

        resumed = run_foil(
            "resume", "--config", str(config), "--state-dir", str(state), "--json", env=lost_env
        )
        assert resumed.returncode == 0, resumed.stderr
        resume_payload = json.loads(resumed.stdout)
        assert {seat["action"] for seat in resume_payload["seats"]} == {"resume_native"}
        assert all(seat["state"] == "working" for seat in resume_payload["seats"])
    finally:
        run_foil("stop", "--config", str(config), "--state-dir", str(state), "--json", env=launch_env)
