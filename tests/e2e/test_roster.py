"""End-to-end roster workflow: init, list, add, spawn."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry

pytestmark = pytest.mark.e2e


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("FOIL_SEAT_ID", None)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def _prepare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a minimal git repository with foil initialized."""
    repo = tmp_path / "project"
    repo.mkdir()

    # Create minimal project structure
    (repo / "README.md").write_text("# Test Project\n", encoding="utf-8")

    # Initialize git
    assert _git(repo, "init", "--quiet").returncode == 0
    assert _git(repo, "config", "user.name", "Foil Test").returncode == 0
    assert _git(repo, "config", "user.email", "foil-test@localhost").returncode == 0
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-m", "initial").returncode == 0

    # Change to repo directory
    monkeypatch.chdir(repo)

    # Initialize foil
    assert main(["init"]) == 0

    return repo


def test_roster_full_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Full workflow: init → roster list → add researcher → verify."""
    repo = _prepare(tmp_path, monkeypatch)

    # List roster
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "list"]) == 0
    listing = out.getvalue()

    # Verify default templates are present
    assert "lead [" in listing
    assert "implementer [" in listing
    assert "reviewer [" in listing

    # Verify available personas are shown
    assert "+ " in listing  # At least one persona without template

    # Add researcher template
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "add", "researcher"]) == 0
    add_output = out.getvalue()
    assert "created template 'researcher'" in add_output

    # Verify template file exists
    researcher_template = foil_root(repo) / "templates" / "researcher.toml"
    assert researcher_template.is_file()
    content = researcher_template.read_text()
    assert 'persona = "personas/researcher.md"' in content

    # Show the new template
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "show", "researcher"]) == 0
    show_output = out.getvalue()
    assert "role: researcher" in show_output
    assert "harness:" in show_output

    # Update researcher template
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "update", "researcher", "model=claude-opus-5.5"]) == 0
    update_output = out.getvalue()
    assert "updated template 'researcher'" in update_output

    # Verify update
    content = researcher_template.read_text()
    assert 'model = "claude-opus-5.5"' in content

    # List roster again to see researcher
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "list"]) == 0
    listing_after = out.getvalue()
    assert "researcher [" in listing_after


def test_roster_update_existing_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Update an existing default template's fields."""
    repo = _prepare(tmp_path, monkeypatch)

    # Update lead harness
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "update", "lead", "harness=codex"]) == 0
    assert "updated template 'lead'" in out.getvalue()

    # Verify update
    lead_template = foil_root(repo) / "templates" / "lead.toml"
    content = lead_template.read_text()
    assert 'harness = "codex"' in content

    # Update implementer model
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "update", "implementer", "model=claude-sonnet-5.5"]) == 0
    assert "updated template 'implementer'" in out.getvalue()

    # Verify update
    impl_template = foil_root(repo) / "templates" / "implementer.toml"
    content = impl_template.read_text()
    assert 'model = "claude-sonnet-5.5"' in content


def test_roster_remove_non_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove a non-protected template."""
    repo = _prepare(tmp_path, monkeypatch)

    # Add researcher
    assert main(["roster", "add", "researcher"]) == 0
    researcher_template = foil_root(repo) / "templates" / "researcher.toml"
    assert researcher_template.is_file()

    # Remove researcher
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "remove", "researcher"]) == 0
    assert "removed template 'researcher'" in out.getvalue()

    # Verify template is gone
    assert not researcher_template.exists()


def test_roster_cannot_remove_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cannot remove protected templates."""
    _prepare(tmp_path, monkeypatch)

    # Try to remove lead (should fail)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert main(["roster", "remove", "lead"]) == 1
    assert "cannot remove protected" in err.getvalue()

    # Try to remove implementer (should fail)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert main(["roster", "remove", "implementer"]) == 1
    assert "cannot remove protected" in err.getvalue()


def test_roster_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON output format works correctly."""
    import json

    _prepare(tmp_path, monkeypatch)

    # List in JSON format
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "list", "--json"]) == 0

    data = json.loads(out.getvalue())
    assert "templates" in data
    assert "personas" in data
    assert len(data["templates"]) == 3  # lead, implementer, reviewer

    # Show in JSON format
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "show", "lead", "--json"]) == 0

    data = json.loads(out.getvalue())
    assert data["role"] == "lead"
    assert "harness" in data
    assert "permission" in data


def test_added_role_can_be_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A role added with the roster can be spawned, then the fleet is stopped."""
    repo = _prepare(tmp_path, monkeypatch)
    for role in ("lead", "implementer", "reviewer"):
        assert main(["roster", "update", role, "harness=fake"]) == 0
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["roster", "add", "researcher"]) == 0
    assert main(["roster", "update", "researcher", "harness=fake"]) == 0
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    try:
        assert main(["seat", "spawn", "researcher"]) == 0
        seat = load_registry(repo)["seats"]["researcher-1"]
        assert seat["template"] == "researcher"
        assert seat["harness"] == "fake"
    finally:
        monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
        main(["seat", "kill", "--all"])
