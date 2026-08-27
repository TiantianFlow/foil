"""Doctor reports host tools and live-seat worktrees."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from foil.doctor import DoctorError, doctor_report
from foil.fleet import FleetStore
from foil.onboarding import initialize_project


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def committed_repo(path: Path, *, marker: str = "APP.md") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-b", "main", cwd=path)
    (path / marker).write_text("product\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Product",
            "GIT_AUTHOR_EMAIL": "product@localhost",
            "GIT_COMMITTER_NAME": "Product",
            "GIT_COMMITTER_EMAIL": "product@localhost",
        }
    )
    subprocess.run(
        ["git", "add", marker],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "product root"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return path


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
    assert report["reconciliation"]["orphan_tmux"] == []
    assert report["reconciliation"]["missing_tmux"] == []
    assert report["reconciliation"]["stale_status"] == []


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
    parent_exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    clone_exclude = (worktree / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/worktrees/" in parent_exclude.splitlines()
    assert "/FOIL.md" in clone_exclude.splitlines()
    assert not (project / ".gitignore").exists()


def test_doctor_apply_links_a_foreign_git_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    result = initialize_project(project, environ={"FOIL_STATE_DIR": str(state)})
    git("init", "-b", "foil-demo", cwd=project)
    (project / "SUITE.md").write_text("roles\n", encoding="utf-8")
    fleet = FleetStore(state).read(result.fleet_id)
    product = committed_repo(tmp_path / "product")

    from datetime import UTC, datetime

    from foil.registry import RegistryStore, SeatRecord, TmuxTarget

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    worktree = product / "worktrees" / "implementer"
    RegistryStore(state).write_seat(
        SeatRecord(
            fleet_id=fleet.fleet_id,
            seat_id="implementer",
            working_directory=str(worktree),
            agent_kind="grok",
            native_session_id=None,
            tmux=TmuxTarget("s", "w", "$1", "@1"),
            git_branch="main",
            worktree_path=str(worktree),
            usage_pool_id="default",
            incarnation_id="inc-1",
            updated_at=now,
            extensions={"profile": {"cli": "grok", "isolated": True}},
        )
    )

    applied = doctor_report(state, fleet.fleet_id, apply=True)
    assert applied["worktrees"]["ok"] is True
    assert worktree.is_dir()
    assert (worktree / ".git").is_file()
    assert (worktree / "APP.md").read_text(encoding="utf-8") == "product\n"
    assert not (worktree / "SUITE.md").exists()
    assert not (project / "worktrees").exists()
    log = subprocess.run(
        ["git", "-C", str(product), "log", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "foil identity" not in log.stdout.splitlines()
    listed = subprocess.run(
        ["git", "-C", str(product), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert str(worktree.resolve()) in listed.stdout


def test_doctor_apply_refuses_a_foreign_canonical_checkout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "state"
    result = initialize_project(project, environ={"FOIL_STATE_DIR": str(state)})
    git("init", "-b", "foil-demo", cwd=project)
    fleet = FleetStore(state).read(result.fleet_id)
    product = committed_repo(tmp_path / "product")

    from datetime import UTC, datetime

    from foil.registry import RegistryStore, SeatRecord, TmuxTarget

    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    RegistryStore(state).write_seat(
        SeatRecord(
            fleet_id=fleet.fleet_id,
            seat_id="implementer",
            working_directory=str(product),
            agent_kind="grok",
            native_session_id=None,
            tmux=TmuxTarget("s", "w", "$1", "@1"),
            git_branch="main",
            worktree_path=str(product),
            usage_pool_id="default",
            incarnation_id="inc-1",
            updated_at=now,
            extensions={"profile": {"cli": "grok", "isolated": True}},
        )
    )

    with pytest.raises(DoctorError, match="canonical checkout"):
        doctor_report(state, fleet.fleet_id, apply=True)
    assert not (product / "worktrees").exists()
