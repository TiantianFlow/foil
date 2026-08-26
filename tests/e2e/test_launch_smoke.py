"""Spawn-confidence smokes beyond the walking-skeleton happy path."""

from __future__ import annotations

import subprocess

from tests.e2e.harness import OperatorFleet
from tests.e2e.test_user_journey import _seats


def test_doctor_reports_stale_worktrees_after_later_project_commits(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    fresh = fleet.foil("doctor", *fleet.fleet_flags(), "--json").json()
    assert fresh["worktrees"]["ok"] is True

    (fleet.project / "after-clone.txt").write_text("later", encoding="utf-8")
    fleet.git("add", "after-clone.txt")
    fleet.git("commit", "--quiet", "-m", "after clone")
    stale = fleet.foil("doctor", *fleet.fleet_flags(), "--json").json()
    assert stale["worktrees"]["ok"] is False
    assert any(seat["stale"] for seat in stale["worktrees"]["seats"])
    assert not (
        fleet.project / "worktrees" / "implementer" / "after-clone.txt"
    ).exists()


def test_operator_can_spawn_a_replacement_after_remove(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    fleet.foil(
        "seat",
        "remove",
        *fleet.fleet_flags(),
        "--json",
        "--seat",
        "reviewer-challenger",
    ).json()
    remaining = _seats(fleet.lifecycle("status").json())
    assert set(remaining) == {"lead", "implementer"}
    assert remaining["implementer"]["state"] == "working"

    replacement = fleet.spawn(
        "reviewer-challenger",
        "opencode",
        isolated=True,
        role="reviewer-challenger",
    ).json()
    assert _seats(replacement)["reviewer-challenger"]["state"] == "working"
    assert len(fleet.tmux_windows()) == 3


def test_spawn_after_a_full_stop_still_requires_resume(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    fleet.lifecycle("stop").json()
    second = fleet.spawn("lead", "grok", lead=True, role="manager")
    assert second.returncode != 0
    assert "already" in second.stderr.lower()
    assert not fleet.tmux_alive()
    resumed = fleet.lifecycle("resume").json()
    assert all(seat["state"] == "working" for seat in resumed["seats"])


def test_two_non_isolated_workers_require_shared_cwd(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.spawn("lead", "grok", lead=True, role="manager").json()
    refused = fleet.spawn(
        "roommate",
        "grok",
        isolated=False,
        role="implementer",
    )
    assert refused.returncode != 0
    assert "shared-cwd" in refused.stderr.lower() or "already used" in refused.stderr.lower()
    shared = fleet.spawn(
        "roommate",
        "grok",
        shared_cwd=True,
        role="implementer",
    )
    assert shared.returncode == 0, shared.stderr
    assert not (fleet.project / "FOIL.md").exists()


def test_status_before_spawn_lists_no_seats(initialized: OperatorFleet) -> None:
    fleet = initialized
    status = fleet.lifecycle("status").json()
    assert status["seats"] == []
    assert status["lead_seat_id"] is None


def test_fresh_resume_keeps_verified_windows(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    fleet.start_complementary_fleet()
    launched = _seats(fleet.lifecycle("status").json())
    before = launched["implementer"]["registry"]
    freshened = fleet.foil(
        "resume",
        *fleet.fleet_flags(),
        "--json",
        "--fresh",
        "--seat",
        "implementer",
    ).json()
    after = _seats(freshened)["implementer"]
    assert after["action"] == "start_fresh"
    assert after["registry"]["previous_incarnation_id"] == before["incarnation_id"]
    windows = fleet.tmux_windows()
    assert len(windows) == 3
    assert len(set(windows)) == 3


def test_uncommitted_project_files_stay_out_of_seat_clones(
    initialized: OperatorFleet,
) -> None:
    fleet = initialized
    leaked = "secret-draft.txt"
    (fleet.project / leaked).write_text("not for seats", encoding="utf-8")
    fleet.start_complementary_fleet()
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
