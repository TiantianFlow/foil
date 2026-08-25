"""Launch-confidence smokes beyond the walking-skeleton happy path."""

from __future__ import annotations

import subprocess

from tests.e2e.harness import OperatorFleet
from tests.e2e.test_user_journey import _seats


def test_doctor_reports_stale_worktrees_after_later_project_commits(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.lifecycle("launch").json()
    fresh = fleet.foil("doctor", "--config", str(fleet.config), "--json").json()
    assert fresh["worktrees"]["ok"] is True

    (fleet.project / "after-clone.txt").write_text("later", encoding="utf-8")
    fleet.git("add", "after-clone.txt")
    fleet.git("commit", "--quiet", "-m", "after clone")
    stale = fleet.foil("doctor", "--config", str(fleet.config), "--json").json()
    assert stale["worktrees"]["ok"] is False
    assert any(seat["stale"] for seat in stale["worktrees"]["seats"])
    assert not (
        fleet.project / "worktrees" / "implementer" / "after-clone.txt"
    ).exists()


def test_launch_completes_a_missing_seat_after_partial_registration(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    first = fleet.lifecycle("launch").json()
    assert set(_seats(first)) == {"implementer", "reviewer-challenger"}
    fleet.seat_record_path("reviewer-challenger").unlink()
    reviewer_window = next(
        name for name in fleet.tmux_windows() if "reviewer" in name
    )
    fleet.kill_tmux_window(reviewer_window)

    observed = _seats(fleet.lifecycle("status").json())
    assert observed["implementer"]["state"] == "working"
    assert observed["reviewer-challenger"]["state"] == "unknown"
    assert observed["reviewer-challenger"]["registry"] is None

    stopped = fleet.lifecycle("stop").json()
    assert _seats(stopped)["implementer"]["state"] == "exited"

    retried = fleet.lifecycle("launch").json()
    seats = _seats(retried)
    assert seats["reviewer-challenger"]["state"] == "working"
    assert seats["implementer"]["state"] == "exited"
    assert seats["reviewer-challenger"]["registry"] is not None
    assert len(fleet.tmux_windows()) == 1

    resumed = fleet.foil(
        "resume",
        "--config",
        str(fleet.config),
        "--state-dir",
        str(fleet.state),
        "--json",
        "--seat",
        "implementer",
    ).json()
    assert _seats(resumed)["implementer"]["state"] == "working"
    assert fleet.tmux_alive()
    assert len(fleet.tmux_windows()) == 2


def test_launch_after_a_full_stop_still_requires_resume(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.lifecycle("launch").json()
    fleet.lifecycle("stop").json()
    second = fleet.lifecycle("launch")
    assert second.returncode != 0
    assert "already" in second.stderr.lower()
    assert not fleet.tmux_alive()


def test_status_before_launch_lists_unregistered_seats(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    status = fleet.lifecycle("status").json()
    seats = _seats(status)
    assert set(seats) == {"implementer", "reviewer-challenger"}
    assert all(seat["state"] == "unknown" for seat in seats.values())
    assert all(seat["registry"] is None for seat in seats.values())


def test_fresh_resume_keeps_two_verified_windows(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    launched = _seats(fleet.lifecycle("launch").json())
    before = launched["implementer"]["registry"]
    freshened = fleet.foil(
        "resume",
        "--config",
        str(fleet.config),
        "--state-dir",
        str(fleet.state),
        "--json",
        "--fresh",
        "--seat",
        "implementer",
    ).json()
    after = _seats(freshened)["implementer"]
    assert after["action"] == "start_fresh"
    assert after["registry"]["previous_incarnation_id"] == before["incarnation_id"]
    windows = fleet.tmux_windows()
    assert len(windows) == 2
    assert len(set(windows)) == 2


def test_uncommitted_project_files_stay_out_of_seat_clones(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    leaked = "secret-draft.txt"
    (fleet.project / leaked).write_text("not for seats", encoding="utf-8")
    fleet.lifecycle("launch").json()
    for seat_id in ("implementer", "reviewer-challenger"):
        clone = fleet.project / "worktrees" / seat_id
        assert clone.is_dir()
        assert not (clone / leaked).exists()
        head = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert head.stdout.strip() == "foil-demo"
