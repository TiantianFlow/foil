"""Roster management operations: list, show, add, update, remove."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foil.cli import main
from foil.errors import FoilError
from foil.lifecycle import init_project
from foil.roster_ops import (
    add_template,
    list_roster,
    remove_template,
    show_template,
    update_template,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an initialized foil project and run commands inside it.

    The current directory has to be that project. From a linked worktree
    with no ``.foil`` of its own, discovery walks up to the main checkout.
    """
    import subprocess

    subprocess.run(
        ["git", "-C", str(tmp_path), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    init_project(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_roster_list_shows_default_templates(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list_roster shows the three default templates."""
    list_roster(project, as_json=False)
    out = capsys.readouterr().out
    assert "lead [" in out
    assert "implementer [" in out
    assert "reviewer [" in out


def test_roster_list_shows_available_personas(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list_roster shows personas without templates."""
    list_roster(project, as_json=False)
    out = capsys.readouterr().out
    # Personas that exist but don't have templates
    assert "+ documentation-writer" in out or "+ researcher" in out


def test_roster_list_json_format(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """list_roster --json produces valid JSON."""
    list_roster(project, as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "templates" in data
    assert "personas" in data
    assert len(data["templates"]) == 3  # lead, implementer, reviewer
    assert isinstance(data["personas"], list)


def test_roster_show_displays_template(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show_template displays one template's configuration."""
    show_template(project, "lead", as_json=False)
    out = capsys.readouterr().out
    assert "role: lead" in out
    assert "harness:" in out
    assert "permission:" in out


def test_roster_show_json_format(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show_template --json produces valid JSON."""
    show_template(project, "implementer", as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["role"] == "implementer"
    assert "harness" in data
    assert "worktree" in data
    assert data["worktree"] is True  # implementer has worktree


def test_roster_show_unknown_template_fails(project: Path) -> None:
    """show_template fails for unknown template."""
    with pytest.raises(FoilError, match="unknown template"):
        show_template(project, "nonexistent", as_json=False)


def test_roster_add_creates_template_from_persona(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add_template creates a template for an existing persona."""
    add_template(project, "researcher", from_file=None)
    out = capsys.readouterr().out
    assert "created template 'researcher'" in out
    
    # Verify template exists and is valid
    template_path = project / ".foil" / "templates" / "researcher.toml"
    assert template_path.is_file()
    content = template_path.read_text()
    assert 'persona = "personas/researcher.md"' in content


def test_roster_add_duplicate_fails(project: Path) -> None:
    """add_template fails when template already exists."""
    with pytest.raises(FoilError, match="already exists"):
        add_template(project, "lead", from_file=None)


def test_roster_add_without_persona_fails(project: Path) -> None:
    """add_template fails when persona file doesn't exist."""
    with pytest.raises(FoilError, match="persona file not found") as caught:
        add_template(project, "nonexistent", from_file=None)
    assert "\n" not in str(caught.value)


def test_roster_add_from_file(
    project: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """add_template creates a template from a TOML file."""
    toml_file = tmp_path / "custom.toml"
    toml_file.write_text(
        'harness = "claude"\n'
        'model = "claude-sonnet-5.5"\n'
        'persona = "personas/researcher.md"\n'
        'worktree = true\n'
        'permission = "auto"\n',
        encoding="utf-8",
    )
    
    add_template(project, "custom", from_file=str(toml_file))
    out = capsys.readouterr().out
    assert "created template 'custom'" in out
    
    template_path = project / ".foil" / "templates" / "custom.toml"
    assert template_path.is_file()
    content = template_path.read_text()
    assert "claude-sonnet-5.5" in content


def test_roster_add_invalid_role_name_fails(project: Path) -> None:
    """add_template fails for invalid role name."""
    with pytest.raises(FoilError, match="invalid role name"):
        add_template(project, "bad/name", from_file=None)


def test_roster_update_harness(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the harness field."""
    update_template(project, "lead", "harness", "codex")
    out = capsys.readouterr().out
    assert "updated template 'lead'" in out
    
    template_path = project / ".foil" / "templates" / "lead.toml"
    content = template_path.read_text()
    assert 'harness = "codex"' in content


def test_roster_update_model(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the model field."""
    update_template(project, "implementer", "model", "claude-opus-5.5")
    out = capsys.readouterr().out
    assert "updated template 'implementer'" in out
    
    template_path = project / ".foil" / "templates" / "implementer.toml"
    content = template_path.read_text()
    assert 'model = "claude-opus-5.5"' in content


def test_roster_update_permission(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the permission field."""
    update_template(project, "reviewer", "permission", "auto")
    out = capsys.readouterr().out
    assert "updated template 'reviewer'" in out
    
    template_path = project / ".foil" / "templates" / "reviewer.toml"
    content = template_path.read_text()
    assert 'permission = "auto"' in content


def test_roster_update_worktree(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the worktree field."""
    update_template(project, "lead", "worktree", "true")
    out = capsys.readouterr().out
    assert "updated template 'lead'" in out
    
    template_path = project / ".foil" / "templates" / "lead.toml"
    content = template_path.read_text()
    assert "worktree = true" in content


def test_roster_update_invalid_field_fails(project: Path) -> None:
    """update_template fails for invalid field name."""
    with pytest.raises(FoilError, match="invalid field") as caught:
        update_template(project, "lead", "badfield", "value")
    assert "\n" not in str(caught.value)


def test_roster_update_invalid_permission_value_fails(project: Path) -> None:
    """update_template fails for invalid permission value."""
    with pytest.raises(FoilError, match="permission must be"):
        update_template(project, "lead", "permission", "badvalue")


def test_roster_update_invalid_worktree_value_fails(project: Path) -> None:
    """update_template fails for invalid worktree value."""
    with pytest.raises(FoilError, match="worktree must be"):
        update_template(project, "lead", "worktree", "badvalue")


def test_roster_update_unknown_template_fails(project: Path) -> None:
    """update_template fails for unknown template."""
    with pytest.raises(FoilError, match="unknown template"):
        update_template(project, "nonexistent", "harness", "claude")


def test_roster_remove_deletes_template(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """remove_template deletes a non-protected template."""
    # First add a template
    add_template(project, "researcher", from_file=None)
    capsys.readouterr()  # Clear output
    
    # Then remove it
    remove_template(project, "researcher")
    out = capsys.readouterr().out
    assert "removed template 'researcher'" in out
    
    template_path = project / ".foil" / "templates" / "researcher.toml"
    assert not template_path.exists()


def test_roster_remove_protected_fails(project: Path) -> None:
    """remove_template fails for protected templates."""
    with pytest.raises(FoilError, match="cannot remove protected") as caught:
        remove_template(project, "lead")
    assert "\n" not in str(caught.value)
    
    with pytest.raises(FoilError, match="cannot remove protected"):
        remove_template(project, "implementer")
    
    with pytest.raises(FoilError, match="cannot remove protected"):
        remove_template(project, "reviewer")


def test_roster_remove_unknown_template_fails(project: Path) -> None:
    """remove_template fails for unknown template."""
    with pytest.raises(FoilError, match="unknown template"):
        remove_template(project, "nonexistent")


def test_roster_cli_errors_are_one_line(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI prints roster failures as one stderr line (N8)."""
    assert main(["roster", "remove", "lead"]) == 1
    removed = capsys.readouterr().err
    assert removed.count("\n") == 1
    assert "cannot remove protected" in removed

    assert main(["roster", "update", "lead", "badfield=value"]) == 1
    updated = capsys.readouterr().err
    assert updated.count("\n") == 1
    assert "invalid field" in updated

    assert main(["roster", "add", "nonexistent"]) == 1
    added = capsys.readouterr().err
    assert added.count("\n") == 1
    assert "persona file not found" in added


def test_roster_add_does_not_follow_a_symlink(project: Path) -> None:
    """A template symlink is not treated as a missing file."""
    outside = project / "escaped.toml"
    link = project / ".foil" / "templates" / "researcher.toml"
    link.symlink_to(outside)
    with pytest.raises(FoilError, match="refusing symlink"):
        add_template(project, "researcher", from_file=None)
    assert not outside.exists()
    assert link.is_symlink()


def test_roster_add_from_file_does_not_follow_a_symlink(
    project: Path,
    tmp_path: Path,
) -> None:
    """--from does not write through a template symlink, then delete the link."""
    outside = project / "escaped.toml"
    outside.write_text("original\n", encoding="utf-8")
    link = project / ".foil" / "templates" / "custom.toml"
    link.symlink_to(outside)
    source = tmp_path / "custom.toml"
    source.write_text("not = a template [\n", encoding="utf-8")
    with pytest.raises(FoilError, match="refusing symlink"):
        add_template(project, "custom", from_file=str(source))
    assert outside.read_text(encoding="utf-8") == "original\n"
    assert link.is_symlink()


def test_roster_update_does_not_follow_a_symlink(project: Path) -> None:
    """update refuses a symlinked template instead of writing its target."""
    outside = project / "escaped.toml"
    outside.write_text(
        'harness = "claude"\n'
        'persona = "personas/lead.md"\n'
        "worktree = false\n"
        'permission = "ask"\n',
        encoding="utf-8",
    )
    link = project / ".foil" / "templates" / "lead.toml"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(FoilError, match="unknown template"):
        update_template(project, "lead", "model", "other")
    assert "other" not in outside.read_text(encoding="utf-8")
    assert link.is_symlink()


def test_roster_cli_list(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster list command works."""
    code = main(["roster", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "lead [" in out


def test_roster_cli_show(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster show command works."""
    code = main(["roster", "show", "implementer"])
    assert code == 0
    out = capsys.readouterr().out
    assert "role: implementer" in out


def test_roster_cli_add(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster add command works."""
    code = main(["roster", "add", "researcher"])
    assert code == 0
    out = capsys.readouterr().out
    assert "created template 'researcher'" in out


def test_roster_cli_update(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster update command works."""
    code = main(["roster", "update", "lead", "model=claude-opus-5.5"])
    assert code == 0
    out = capsys.readouterr().out
    assert "updated template 'lead'" in out


def test_roster_cli_update_without_equals_fails(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """foil roster update without = in FIELD=VALUE fails."""
    code = main(["roster", "update", "lead", "model"])
    assert code == 1
    err = capsys.readouterr().err
    assert "FIELD=VALUE format required" in err


def test_roster_cli_remove(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster remove command works."""
    # First add a template
    main(["roster", "add", "researcher"])
    capsys.readouterr()
    
    # Then remove it
    code = main(["roster", "remove", "researcher"])
    assert code == 0
    out = capsys.readouterr().out
    assert "removed template 'researcher'" in out


def test_roster_authority_worker_cannot_list(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Workers cannot use roster commands."""
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    code = main(["roster", "list"])
    assert code == 1
    err = capsys.readouterr().err
    assert "not allowed" in err


def test_roster_authority_lead_can_list(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Lead can use roster commands."""
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    code = main(["roster", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "lead [" in out
