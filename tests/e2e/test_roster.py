"""End-to-end roster workflow: init, list, add, spawn."""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root, store_root
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
    # Roster still resolves a persona path under the project templates.
    personas = foil_root(repo) / "templates" / "personas"
    personas.mkdir(parents=True)
    packaged = files("foil").joinpath("defaults", "personas")
    for name in ("lead.md", "implementer.md", "reviewer.md", "researcher.md"):
        (personas / name).write_text(
            packaged.joinpath(name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    spare = store_root() / "personas"
    spare.mkdir()
    (spare / "spare.md").write_text("A spare persona with no role.\n", encoding="utf-8")

    return repo


def test_roster_full_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Full workflow: init → roster list → add researcher → verify."""
    _prepare(tmp_path, monkeypatch)

    # List roster
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "list"]) == 0
    listing = out.getvalue()

    # Verify default templates are present
    assert "\tpackaged\tYou are the lead seat of this fleet." in listing
    assert "\tpackaged\tYou make the change the task asks for," in listing
    assert "\tpackaged\tYou check the change the lead names" in listing

    # Verify available personas are shown
    assert "+ " in listing  # At least one persona without template

    # Add researcher template
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "add", "researcher"]) == 0
    add_output = out.getvalue()
    assert "created template 'researcher'" in add_output

    # Verify template file exists
    roles = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "researcher"' in roles
    assert 'persona = "personas/researcher.md"' in roles

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
    content = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'model = "claude-opus-5.5"' in content

    # List roster again to see researcher
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "list"]) == 0
    listing_after = out.getvalue()
    assert "researcher\t" in listing_after


def test_roster_update_existing_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Update an existing default template's fields."""
    _prepare(tmp_path, monkeypatch)

    # Update lead harness
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "update", "lead", "harness=codex"]) == 0
    assert "updated template 'lead'" in out.getvalue()

    # Verify update
    content = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "lead"' in content
    assert 'harness = "codex"' in content

    # Update implementer model
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "update", "implementer", "model=claude-sonnet-5.5"]) == 0
    assert "updated template 'implementer'" in out.getvalue()

    # Verify update
    content = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'model = "claude-sonnet-5.5"' in content


def test_roster_remove_non_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove a non-protected template."""
    _prepare(tmp_path, monkeypatch)

    # Add researcher
    assert main(["roster", "add", "researcher"]) == 0
    roles = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "researcher"' in roles

    # Remove researcher
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["roster", "remove", "researcher"]) == 0
    assert "removed template 'researcher'" in out.getvalue()

    # Verify template is gone
    assert 'id = "researcher"' not in (store_root() / "roles.toml").read_text(encoding="utf-8")


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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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
        assert seat["state"] == ""
        capsys.readouterr()
        assert main(["roster", "remove", "researcher"]) == 1
        err = capsys.readouterr().err
        assert err.count("\n") == 1
        assert err == "foil: roles are outside the fleet only\n"
        assert 'id = "researcher"' in (store_root() / "roles.toml").read_text(encoding="utf-8")
        assert main(["roster", "update", "researcher", "harness=codex"]) == 0
        overrides = foil_root(repo) / "fleets" / "main" / "overrides.toml"
        assert 'harness = "codex"' in overrides.read_text(encoding="utf-8")
        assert 'harness = "codex"' not in (store_root() / "mapping.toml").read_text(
            encoding="utf-8"
        )
        assert main(["seat", "list"]) == 0
        assert "researcher-1\tresearcher\talive\t\tfake\t" in capsys.readouterr().out
    finally:
        monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
        main(["seat", "kill", "--all"])
