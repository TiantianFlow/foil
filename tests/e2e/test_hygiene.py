"""Foil runtime files must not be stageable in the project or worker clones."""

from __future__ import annotations

import os
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


def _commit_product(path: Path) -> Path:
    path.mkdir(parents=True)
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
        ["git", "init", "--quiet", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )
    (path / "APP.md").write_text("product\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "APP.md"],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "product root"],
        cwd=path,
        check=True,
        capture_output=True,
        env=env,
    )
    return path


def test_isolated_spawn_from_a_foreign_git_root(initialized: OperatorFleet) -> None:
    product = _commit_product(initialized.root.parent / "product")
    initialized.spawn("lead", "grok", lead=True, role="manager").json()
    spawned = initialized.spawn(
        "implementer",
        "grok",
        isolated=True,
        role="implementer",
        extra=("--cwd", str(product)),
    )
    assert spawned.returncode == 0, spawned.stderr
    dest = product / "worktrees" / "implementer"
    record = spawned.json()["seats"][0]["registry"]
    assert Path(record["worktree_path"]).resolve() == dest.resolve()
    assert (dest / ".git").is_file()
    assert (dest / "APP.md").read_text(encoding="utf-8") == "product\n"
    assert not (dest / ".foil").exists()
    assert not (initialized.project / "worktrees").exists()
    listed = _git("worktree", "list", "--porcelain", cwd=product)
    assert str(dest.resolve()) in listed
    log = _git("log", "--format=%s", cwd=product)
    assert "foil identity" not in log.splitlines()
    assert "worktrees/" not in _git("status", "--short", cwd=initialized.project)
    doctor = initialized.foil("doctor", *initialized.fleet_flags(), "--json").json()
    assert doctor["worktrees"]["ok"] is True
    refused = initialized.spawn(
        "roommate",
        "grok",
        isolated=False,
        role="implementer",
        extra=("--cwd", str(product)),
    )
    assert refused.returncode != 0
    assert "canonical checkout" in refused.stderr.lower()
