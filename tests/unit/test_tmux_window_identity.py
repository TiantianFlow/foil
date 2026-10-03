"""TmuxController must target exact window identity, never name fallbacks.

The fake tmux emulates the observed server behavior that trapped stale seat
records: a `session:window-name` target whose name is gone silently resolves
onto the session's current window.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import empty_registry, save_registry
from foil.tmux import ProbeState, TmuxController, TmuxError, TmuxTarget
from tests.test_init import _init_git_repository

FAKE_TMUX = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_TMUX_STATE"])
state = json.loads(state_path.read_text())


def resolve_window(target):
    if target.startswith("@"):
        return state["windows"].get(target)
    if ":" in target:
        session, _, name = target.partition(":")
        for window in state["windows"].values():
            if window["session"] == session and window["name"] == name:
                return window
        # The observed tmux behavior: a missing name falls back onto the
        # session's current window instead of failing.
        current = state["current"].get(session)
        return state["windows"].get(current)
    current = state["current"].get(target)
    return state["windows"].get(current)


args = sys.argv[1:]
if args[0] == "display-message":
    target = args[args.index("-t") + 1]
    fmt = args[-1]
    window = resolve_window(target)
    if window is None:
        print(f"can't find window: {target}", file=sys.stderr)
        sys.exit(1)
    out = fmt
    for key, token in (
        ("session_id", "#{session_id}"),
        ("id", "#{window_id}"),
        ("fleet", "#{@foil-fleet-id}"),
        ("seat", "#{@foil-seat-id}"),
        ("name", "#{window_name}"),
        ("dead", "#{pane_dead}"),
    ):
        out = out.replace(token, window.get(key) or ("0" if key == "dead" else ""))
    print(out)
elif args[0] == "capture-pane":
    target = args[args.index("-t") + 1]
    start = args[args.index("-S") + 1]
    window = resolve_window(target)
    if window is None or "-p" not in args:
        print("can't find window", file=sys.stderr)
        sys.exit(1)
    print(f"{window['seat']}:{start}")
elif args[0] == "kill-window":
    target = args[args.index("-t") + 1]
    window = resolve_window(target)
    if window is None:
        print(f"can't find window: {target}", file=sys.stderr)
        sys.exit(1)
    state["kills"].append(window["id"])
    del state["windows"][window["id"]]
    state_path.write_text(json.dumps(state))
"""


def _install_fake_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    executable = tmp_path / "tmux"
    executable.write_text(FAKE_TMUX, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    state = {
        "windows": {
            "@0": {
                "id": "@0",
                "session": "foil-demo",
                "session_id": "$0",
                "name": "lead",
                "fleet": "fleet-1",
                "seat": "lead",
                "dead": "0",
            },
            "@1": {
                "id": "@1",
                "session": "foil-demo",
                "session_id": "$0",
                "name": "worker",
                "fleet": "fleet-1",
                "seat": "worker",
            },
        },
        "current": {"foil-demo": "@0"},
        "kills": [],
    }
    state_path = tmp_path / "fake-tmux-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("FAKE_TMUX_STATE", str(state_path))
    return executable


def _kills(tmp_path: Path) -> list[str]:
    return json.loads((tmp_path / "fake-tmux-state.json").read_text())["kills"]


def _stale_worker_target() -> TmuxTarget:
    # Recorded while the worker window existed; the "@1" id is now gone and
    # the "worker" name would fall back onto the live lead window.
    return TmuxTarget(
        session_name="foil-demo",
        window_name="worker",
        session_id="$0",
        window_id="@1",
    )


def test_probe_reports_a_missing_recorded_window_as_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    del state["windows"]["@1"]  # worker window is gone; only lead @0 remains
    state_path.write_text(json.dumps(state))
    tmux = TmuxController(executable=str(executable))

    probe = tmux.probe("fleet-1", "worker", _stale_worker_target())

    assert probe.state is ProbeState.DEAD
    assert probe.identity_matches is False


def test_probe_by_window_id_verifies_a_live_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    tmux = TmuxController(executable=str(executable))

    probe = tmux.probe(
        "fleet-1",
        "worker",
        _stale_worker_target(),
    )

    assert probe.state is ProbeState.ALIVE
    assert probe.identity_matches is True
    assert probe.observed is not None and probe.observed.window_id == "@1"


def test_stop_without_a_stored_session_id_still_checks_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    tmux = TmuxController(executable=str(executable))
    target = TmuxTarget(
        session_name="foil-demo",
        window_name="worker",
        session_id=None,
        window_id="@1",
    )

    assert tmux.window_exists("@1") is True
    assert tmux.window_exists("@9") is False
    assert tmux.stop_verified("fleet-1", "worker", target) is True
    assert _kills(tmp_path) == ["@1"]
    assert tmux.capture_pane("@0", 40) == "lead:-40\n"
    with pytest.raises(TmuxError):
        tmux.capture_pane("@1", 40)


def test_probe_with_a_wrong_recorded_window_id_is_not_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    tmux = TmuxController(executable=str(executable))

    probe = tmux.probe(
        "fleet-1",
        "worker",
        TmuxTarget(
            session_name="foil-demo",
            window_name="worker",
            session_id="$0",
            window_id="@0",  # actually the lead window
        ),
    )

    assert probe.state is ProbeState.ALIVE
    assert probe.identity_matches is False


def test_a_dead_pane_is_dead_and_only_an_explicit_remove_stops_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    state["windows"]["@1"]["dead"] = "1"
    state_path.write_text(json.dumps(state))
    tmux = TmuxController(executable=str(executable))

    probe = tmux.probe("fleet-1", "worker", _stale_worker_target())

    assert probe.state is ProbeState.DEAD
    assert probe.identity_matches is True
    assert tmux.stop_verified("fleet-1", "worker", _stale_worker_target()) is False
    assert _kills(tmp_path) == []
    assert tmux.remove_verified("fleet-1", "worker", _stale_worker_target()) is True
    assert _kills(tmp_path) == ["@1"]
    assert "@0" in json.loads(state_path.read_text())["windows"]


def test_stop_verified_treats_a_missing_stale_window_as_already_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    del state["windows"]["@1"]
    state_path.write_text(json.dumps(state))
    tmux = TmuxController(executable=str(executable))

    assert tmux.stop_verified("fleet-1", "worker", _stale_worker_target()) is False
    assert _kills(tmp_path) == []  # the live lead window was never targeted


def test_stop_verified_kills_only_the_exact_recorded_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    tmux = TmuxController(executable=str(executable))

    assert tmux.stop_verified("fleet-1", "worker", _stale_worker_target()) is True
    assert _kills(tmp_path) == ["@1"]
    state = json.loads((tmp_path / "fake-tmux-state.json").read_text())
    assert "@0" in state["windows"]


def test_abandon_window_never_falls_back_onto_another_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    del state["windows"]["@1"]
    state_path.write_text(json.dumps(state))
    tmux = TmuxController(executable=str(executable))

    tmux.abandon_window(_stale_worker_target())

    assert _kills(tmp_path) == []


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _init_git_repository(repo)
    foil = repo / ".foil" / "run"
    foil.mkdir(parents=True)
    monkeypatch.chdir(repo)
    return repo


def _seat(name: str, window_id: str) -> dict[str, str]:
    return {
        "name": name,
        "template": name.split("-", 1)[0],
        "harness": "codex",
        "model": "",
        "window_id": window_id,
        "state": "",
        "worktree": "",
        "branch": "",
        "session_id": "",
    }


def _use_fake_tmux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_tmux(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")


def test_kill_of_a_live_seat_still_stops_its_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_fake_tmux(tmp_path, monkeypatch)
    repo = _project(tmp_path, monkeypatch)
    registry = empty_registry()
    registry["fleet_id"] = "fleet-1"
    registry["tmux_session"] = "foil-demo"
    registry["seats"] = {"worker": _seat("worker", "@1")}
    save_registry(repo, registry)

    assert main(["seat", "kill", "worker"]) == 0
    assert capsys.readouterr().err == ""
    assert _kills(tmp_path) == ["@1"]
    saved = json.loads((repo / ".foil" / "run" / "registry.json").read_text())
    assert saved["seats"]["worker"]["state"] == "killed"


def test_kill_of_a_dead_seat_does_not_stop_a_reused_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The worker record still names @1, but that id now belongs to a window
    # carrying another seat. Killing the dead record must not stop it.
    _use_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    state["windows"]["@1"]["seat"] = "intruder"
    state_path.write_text(json.dumps(state))
    repo = _project(tmp_path, monkeypatch)
    registry = empty_registry()
    registry["fleet_id"] = "fleet-1"
    registry["tmux_session"] = "foil-demo"
    registry["seats"] = {
        "worker": _seat("worker", "@1"),
        "other": _seat("other", "@0"),
    }
    save_registry(repo, registry)

    assert main(["seat", "kill", "worker"]) == 0
    assert capsys.readouterr().err == ""
    assert _kills(tmp_path) == []
    state = json.loads((tmp_path / "fake-tmux-state.json").read_text())
    assert "@1" in state["windows"]
    saved = json.loads((repo / ".foil" / "run" / "registry.json").read_text())
    assert saved["seats"]["worker"]["state"] == "killed"
    assert saved["seats"]["other"]["state"] == ""

    # The other seat is dead too: its stored id names the worker's window.
    # kill --all must mark every seat killed and still leave that window.
    assert main(["seat", "kill", "--all"]) == 0
    assert capsys.readouterr().err == ""
    assert _kills(tmp_path) == []
    state = json.loads((tmp_path / "fake-tmux-state.json").read_text())
    assert set(state["windows"]) == {"@0", "@1"}
    saved = json.loads((repo / ".foil" / "run" / "registry.json").read_text())
    assert saved["seats"]["worker"]["state"] == "killed"
    assert saved["seats"]["other"]["state"] == "killed"


def test_kill_of_a_dead_seat_with_no_marker_leaves_the_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    state["windows"]["@1"]["fleet"] = ""
    state["windows"]["@1"]["seat"] = ""
    state_path.write_text(json.dumps(state))
    repo = _project(tmp_path, monkeypatch)
    registry = empty_registry()
    registry["fleet_id"] = "fleet-1"
    registry["tmux_session"] = "foil-demo"
    registry["seats"] = {"worker": _seat("worker", "@1")}
    save_registry(repo, registry)

    assert main(["seat", "kill", "worker"]) == 0
    assert capsys.readouterr().err == ""
    assert _kills(tmp_path) == []
    assert "@1" in json.loads(state_path.read_text())["windows"]
    saved = json.loads((repo / ".foil" / "run" / "registry.json").read_text())
    assert saved["seats"]["worker"]["state"] == "killed"


def test_name_only_targets_require_exact_name_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _install_fake_tmux(tmp_path, monkeypatch)
    state_path = tmp_path / "fake-tmux-state.json"
    state = json.loads(state_path.read_text())
    del state["windows"]["@1"]
    state_path.write_text(json.dumps(state))
    tmux = TmuxController(executable=str(executable))

    stale = tmux.probe(
        "fleet-1",
        "worker",
        TmuxTarget(
            session_name="foil-demo",
            window_name="worker",
            session_id="$0",
            window_id=None,
        ),
    )
    assert stale.state is ProbeState.DEAD

    present = tmux.probe(
        "fleet-1",
        "lead",
        TmuxTarget(
            session_name="foil-demo",
            window_name="lead",
            session_id="$0",
            window_id=None,
        ),
    )
    assert present.state is ProbeState.ALIVE
    assert present.observed is not None and present.observed.window_id == "@0"
