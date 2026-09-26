"""TmuxController must target exact window identity, never name fallbacks.

The fake tmux emulates the observed server behavior that trapped stale seat
records: a `session:window-name` target whose name is gone silently resolves
onto the session's current window.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from foil.tmux import ProbeState, TmuxController, TmuxTarget

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
    ):
        out = out.replace(token, window.get(key) or "")
    print(out)
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
