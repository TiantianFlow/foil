"""Foil runtime files must not be stageable in the project or worker clones."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.e2e.harness import OperatorFleet


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_spawned_workers_do_not_dirty_git_status(initialized: OperatorFleet) -> None:
    before = _git("status", "--short", cwd=initialized.project)
    initialized.start_complementary_fleet()
    after = _git("status", "--short", cwd=initialized.project)
    assert after == before
    assert "worktrees/" not in after
    assert "FOIL.md" not in after

    _git("add", "-A", cwd=initialized.project)
    staged = _git("diff", "--cached", "--name-only", cwd=initialized.project)
    assert "worktrees/" not in staged
    assert not any(line.endswith("FOIL.md") for line in staged.splitlines())

    for seat_id in ("implementer", "reviewer-challenger"):
        clone = initialized.project / "worktrees" / seat_id
        assert (clone / "FOIL.md").is_file()
        clone_status = _git("status", "--short", cwd=clone)
        assert clone_status == ""
        _git("add", "-A", cwd=clone)
        clone_staged = _git("diff", "--cached", "--name-only", cwd=clone)
        assert "FOIL.md" not in clone_staged.splitlines()

    gitignore = initialized.project / ".gitignore"
    assert not gitignore.exists() or "worktrees" not in gitignore.read_text(encoding="utf-8")


def test_isolated_spawn_preserves_a_tracked_foil_md(initialized: OperatorFleet) -> None:
    tracked = "# Project-owned FOIL.md\n"
    (initialized.project / "FOIL.md").write_text(tracked, encoding="utf-8")
    initialized.git("add", "FOIL.md")
    initialized.git("commit", "--quiet", "-m", "Track project FOIL.md")

    initialized.spawn("lead", "grok", lead=True, role="manager")
    spawned = initialized.spawn("implementer", "grok", role="implementer")
    assert spawned.returncode == 0, spawned.stderr

    clone = initialized.project / "worktrees" / "implementer"
    assert (clone / "FOIL.md").read_text(encoding="utf-8") == tracked
    assert _git("status", "--short", cwd=clone) == ""
    canonical = (
        initialized.state
        / "v1"
        / "fleets"
        / initialized.fleet_id
        / "adapter-state"
        / "implementer"
        / "FOIL.md"
    )
    assert canonical.is_file()
