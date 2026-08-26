"""Real-tmux regressions for exact window identity (the stale-remove defect).

A recorded seat window whose name is gone must probe DEAD so seat stop/remove
can clean the registry row, and no operation may fall back onto another live
window in the same session.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from foil.tmux import ProbeState, TmuxController

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _two_window_session(tmp_path: Path) -> tuple[TmuxController, str, object, object]:
    controller = TmuxController()
    session = f"foil-test-{uuid.uuid4().hex[:12]}"
    fleet = "fleet-1"
    lead = controller.launch(
        fleet_id=fleet,
        seat_id="lead",
        session_name=session,
        window_name="lead",
        working_directory=tmp_path,
        runner_argv=["sleep", "300"],
    )
    worker = controller.launch(
        fleet_id=fleet,
        seat_id="worker",
        session_name=session,
        window_name="worker",
        working_directory=tmp_path,
        runner_argv=["sleep", "300"],
    )
    return controller, session, lead, worker


def test_missing_recorded_window_probes_dead_and_survives_cleanup(
    tmp_path: Path,
) -> None:
    controller, session, lead, worker = _two_window_session(tmp_path)
    assert worker.window_id and lead.window_id
    try:
        # The recorded worker window disappears; its name is now stale.
        killed = _tmux("kill-window", "-t", worker.window_id)
        assert killed.returncode == 0

        probe = controller.probe("fleet-1", "worker", worker)
        assert probe.state is ProbeState.DEAD

        # Cleanup paths succeed without touching the live lead window.
        assert controller.stop_verified("fleet-1", "worker", worker) is False
        controller.abandon_window(worker)

        lead_probe = controller.probe("fleet-1", "lead", lead)
        assert lead_probe.state is ProbeState.ALIVE
        assert lead_probe.identity_matches is True
        assert _tmux("display-message", "-p", "-t", lead.window_id, "#W").stdout.strip()
    finally:
        _tmux("kill-session", "-t", session)


def test_name_only_probe_requires_exact_name_resolution(
    tmp_path: Path,
) -> None:
    """Without a stored window_id, names must resolve exactly or report DEAD."""

    controller, session, lead, worker = _two_window_session(tmp_path)
    assert worker.window_id and lead.window_id
    name_only = replace(worker, window_id=None)
    try:
        present = controller.probe("fleet-1", "worker", name_only)
        assert present.state is ProbeState.ALIVE
        assert present.observed is not None
        assert present.observed.window_id == worker.window_id
        # Without a stored window_id the identity stays unverified.
        assert present.identity_matches is False

        killed = _tmux("kill-window", "-t", worker.window_id)
        assert killed.returncode == 0
        stale = controller.probe("fleet-1", "worker", name_only)
        assert stale.state is ProbeState.DEAD
        # The live lead window was never resolved as the worker.
        assert _tmux("display-message", "-p", "-t", lead.window_id, "#W").returncode == 0
    finally:
        _tmux("kill-session", "-t", session)
