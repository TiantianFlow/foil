"""Doctor reports host tools and live-seat worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

from foil.doctor import doctor_report
from foil.fleet import FleetStore
from foil.onboarding import initialize_project


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_doctor_reports_empty_plan_after_init(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    result = initialize_project(project, environ={"FOIL_STATE_DIR": str(state)})
    git("init", "-b", "foil-demo", cwd=project)

    report = doctor_report(state, result.fleet_id, apply=False)
    assert report["tmux"]["ok"] is True or report["tmux"]["ok"] is False
    assert report["git"]["ok"] is True
    assert report["credentials_inspected"] is False
    assert report["plan"]["lead_seat_id"] is None
    assert report["plan"]["seats"] == []
    assert report["worktrees"]["ok"] is True
    names = {item["name"] for item in report["clis"]}
    assert {"grok", "opencode"} <= names


def test_doctor_reports_isolated_worktrees_after_apply(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    result = initialize_project(project, environ={"FOIL_STATE_DIR": str(state)})
    git("init", "-b", "foil-demo", cwd=project)
    fleet = FleetStore(state).read(result.fleet_id)

    from datetime import UTC, datetime

    from foil.registry import RegistryStore, SeatRecord, TmuxTarget

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    worktree = project / "worktrees" / "implementer"
    RegistryStore(state).write_seat(
        SeatRecord(
            fleet_id=fleet.fleet_id,
            seat_id="implementer",
            working_directory=str(worktree),
            agent_kind="grok",
            native_session_id=None,
            tmux=TmuxTarget("s", "w", "$1", "@1"),
            git_branch="foil-demo",
            worktree_path=str(worktree),
            usage_pool_id="default",
            incarnation_id="inc-1",
            updated_at=now,
            extensions={"profile": {"cli": "grok", "isolated": True}},
        )
    )

    dry = doctor_report(state, fleet.fleet_id, apply=False)
    assert dry["worktrees"]["ok"] is False
    applied = doctor_report(state, fleet.fleet_id, apply=True)
    assert applied["worktrees"]["ok"] is True
    assert worktree.is_dir()
