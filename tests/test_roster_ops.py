"""Roster management operations: list, show, add, update, remove."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foil.cli import main
from foil.errors import FoilError
from foil.lifecycle import init_project, list_seats
from foil.presets import template_permission
from foil.roster_ops import (
    add_template,
    list_roster,
    remove_template,
    show_template,
    update_template,
)
from foil.store import load_registry, save_registry


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
    assert "\tbuiltin\tYou are the lead seat of this fleet." in out
    assert "\tbuiltin\tYou make the change the task asks for," in out
    assert "\tbuiltin\tYou check the change the lead names" in out


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
    assert data["personas"]
    assert {"name", "description"} <= set(data["personas"][0])
    assert data["templates"][0]["preset"] in {"builtin", "user", "invalid"}
    assert "description" in data["templates"][0]


def test_roster_show_displays_template(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show_template displays one template's configuration."""
    show_template(project, "lead", as_json=False)
    out = capsys.readouterr().out
    assert "role: lead" in out
    assert "harness:" in out
    assert "permission:" in out
    assert "preset: builtin" in out
    assert "description: You are the lead seat of this fleet." in out


def test_roster_show_json_format(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show_template --json produces valid JSON."""
    show_template(project, "implementer", as_json=True)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["role"] == "implementer"
    assert "harness" in data
    assert "worktree" in data
    assert data["worktree"] is True  # implementer has worktree
    assert data["preset"] == "builtin"
    assert data["description"].startswith("You make the change the task asks for,")


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


def test_newline_in_field_and_from_stays_one_line(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A newline in the field or in --from does not split the error."""
    lead = project / ".foil" / "templates" / "lead.toml"
    before = lead.read_bytes()

    assert main(["roster", "update", "lead", "bad\nfield=value"]) == 1
    updated = capsys.readouterr().err
    assert updated.count("\n") == 1
    assert "invalid field 'badfield'" in updated
    assert lead.read_bytes() == before

    assert main(["roster", "add", "custom", "--from", "missing\nfile.toml"]) == 1
    missing = capsys.readouterr().err
    assert missing.count("\n") == 1
    assert "file not found: missingfile.toml" in missing
    assert not (project / ".foil" / "templates" / "custom.toml").exists()


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
    assert "\tbuiltin\tYou are the lead seat of this fleet." in out


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


@pytest.mark.parametrize("role", ["..", "../outside", "/tmp/outside", "a/b"])
def test_unsafe_role_is_rejected_before_any_path_is_used(project: Path, role: str) -> None:
    templates = project / ".foil" / "templates"
    before = sorted(path.name for path in templates.iterdir())
    outside = project.parent / "outside.toml"
    with pytest.raises(FoilError, match="invalid role name"):
        add_template(project, role)
    with pytest.raises(FoilError, match="unknown template"):
        update_template(project, role, "model", "x")
    with pytest.raises(FoilError, match="unknown template"):
        remove_template(project, role)
    assert sorted(path.name for path in templates.iterdir()) == before
    assert not outside.exists()


def test_invalid_update_restores_the_same_bytes(project: Path) -> None:
    path = project / ".foil" / "templates" / "lead.toml"
    before = path.read_bytes()
    with pytest.raises(FoilError):
        update_template(project, "lead", "model", 'say "hi"')
    assert path.read_bytes() == before


def test_unknown_harness_and_parent_persona_roll_back(project: Path) -> None:
    path = project / ".foil" / "templates" / "lead.toml"
    before = path.read_bytes()
    with pytest.raises(FoilError, match="unknown harness"):
        update_template(project, "lead", "harness", "not-a-harness")
    assert path.read_bytes() == before
    with pytest.raises(FoilError, match="must stay inside"):
        update_template(project, "lead", "persona", "../secret.md")
    assert path.read_bytes() == before


def test_add_from_invalid_file_leaves_no_template(project: Path) -> None:
    source = project / "extra.toml"
    source.write_text(
        'harness = "claude"\nextra = true\nworktree = false\npermission = "ask"\n',
        encoding="utf-8",
    )
    with pytest.raises(FoilError, match="invalid template"):
        add_template(project, "custom", from_file=str(source))
    plain = project / "notes.toml"
    plain.write_text("this is not toml [\n", encoding="utf-8")
    with pytest.raises(FoilError, match="invalid template"):
        add_template(project, "custom", from_file=str(plain))
    assert not (project / ".foil" / "templates" / "custom.toml").exists()


def test_init_report_names_personas_without_templates(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init_project(str(project))
    out = capsys.readouterr().out
    assert "Available personas (use 'foil roster add'):" in out
    assert "researcher" in out


def _seat(project: Path, *, state: str) -> None:
    add_template(project, "researcher")
    registry = load_registry(project)
    registry["seats"]["researcher-1"] = {
        "name": "researcher-1",
        "template": "researcher",
        "harness": "fake",
        "window_id": "",
        "state": state,
        "worktree": "",
        "branch": "",
        "session_id": "",
    }
    save_registry(project, registry)


def test_remove_and_harness_change_refuse_a_live_seat(project: Path) -> None:
    _seat(project, state="")
    path = project / ".foil" / "templates" / "researcher.toml"
    before = path.read_bytes()
    with pytest.raises(FoilError, match="still in use"):
        remove_template(project, "researcher")
    with pytest.raises(FoilError, match="still in use"):
        update_template(project, "researcher", "harness", "codex")
    assert path.read_bytes() == before
    assert path.is_file()


def test_killed_seat_does_not_block_remove(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seat(project, state="killed")
    remove_template(project, "researcher")
    assert "removed template 'researcher'" in capsys.readouterr().out
    assert not (project / ".foil" / "templates" / "researcher.toml").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["roster", "list"],
        ["roster", "show", "lead"],
        ["roster", "add", "researcher"],
        ["roster", "update", "lead", "model=x"],
        ["roster", "remove", "reviewer"],
    ],
)
def test_worker_cannot_run_any_roster_command(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    monkeypatch.setenv("FOIL_SEAT_ID", "implementer-1")
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "not allowed" in err


def _permission_source(path: Path, permission: str | None) -> Path:
    lines = [
        'harness = "claude"',
        'persona = "personas/researcher.md"',
        "worktree = false",
    ]
    if permission is not None:
        lines.append(f'permission = "{permission}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_lead_cannot_add_from_with_non_ask_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _permission_source(project / "auto.toml", "auto")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "add", "custom", "--from", str(source)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == "foil: permission is outside the fleet only"
    assert not (project / ".foil" / "templates" / "custom.toml").exists()


def test_lead_can_add_from_when_permission_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _permission_source(project / "plain.toml", None)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "add", "custom", "--from", str(source)]) == 0
    text = (project / ".foil" / "templates" / "custom.toml").read_text(encoding="utf-8")
    assert "permission" not in text
    assert "created template 'custom'" in capsys.readouterr().out


def test_outside_caller_can_add_from_with_auto_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
) -> None:
    source = _permission_source(project / "auto.toml", "auto")
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["roster", "add", "custom", "--from", str(source)]) == 0
    text = (project / ".foil" / "templates" / "custom.toml").read_text(encoding="utf-8")
    assert 'permission = "auto"' in text


def test_lead_cannot_set_a_leading_dash_model(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """model=--yolo must not be written, or later placed as a harness flag."""
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    path = project / ".foil" / "templates" / "implementer.toml"
    before = path.read_bytes()
    assert main(["roster", "update", "implementer", "model=--yolo"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "must not start with '-'" in err
    assert path.read_bytes() == before
    for field in ("persona", "harness"):
        assert main(["roster", "update", "implementer", f"{field}=--yolo"]) == 1
        assert path.read_bytes() == before
    capsys.readouterr()


def test_add_from_leading_dash_model_leaves_no_template(project: Path) -> None:
    source = project / "dash.toml"
    source.write_text(
        'harness = "claude"\n'
        'model = "--yolo"\n'
        'persona = "personas/researcher.md"\n'
        "worktree = false\n"
        'permission = "ask"\n',
        encoding="utf-8",
    )
    with pytest.raises(FoilError, match="invalid template"):
        add_template(project, "custom", from_file=str(source))
    assert not (project / ".foil" / "templates" / "custom.toml").exists()


def test_lead_cannot_inject_permission_through_another_field(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A quoted newline in another field must not set permission.

    The source omits permission, which counts as ask. On the unfixed writer
    the model value closes its string and adds permission = "auto".
    """
    source = _permission_source(project / "plain.toml", None)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "add", "custom", "--from", str(source)]) == 0
    capsys.readouterr()
    path = project / ".foil" / "templates" / "custom.toml"
    before = path.read_bytes()
    injected = 'm"\npermission = "auto'
    assert main(["roster", "update", "custom", f"model={injected}"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "quote, a backslash, or a control character" in err
    assert path.read_bytes() == before
    assert template_permission(path.read_text(encoding="utf-8")) == "ask"


def test_in_fleet_update_rolls_back_a_permission_change(project: Path) -> None:
    path = project / ".foil" / "templates" / "lead.toml"
    before = path.read_bytes()
    with pytest.raises(FoilError, match="permission is outside the fleet only"):
        update_template(project, "lead", "permission", "auto", in_fleet=True)
    assert path.read_bytes() == before


def test_lead_cannot_set_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = project / ".foil" / "templates" / "lead.toml"
    before = path.read_bytes()
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "lead", "permission=auto"]) == 1
    assert "permission is outside the fleet only" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_outside_caller_can_set_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["roster", "update", "reviewer", "permission=auto"]) == 0
    text = (project / ".foil" / "templates" / "reviewer.toml").read_text(encoding="utf-8")
    assert 'permission = "auto"' in text
    assert "updated template 'reviewer'" in capsys.readouterr().out


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
    assert "\tbuiltin\tYou are the lead seat of this fleet." in out


def test_roster_list_keeps_a_missing_persona_and_an_unknown_harness(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    templates = project / ".foil" / "templates"
    (templates / "quiet.toml").write_text(
        'harness = "fake"\npersona = "personas/missing.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    (templates / "stranger.toml").write_text(
        'harness = "no-such"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    harnesses = project / ".foil" / "harnesses"
    (harnesses / "local.toml").write_text(
        'id = "local"\ncommand = ["true"]\nsession_id = "none"\n\n'
        "[permission]\nask = []\nauto = []\n",
        encoding="utf-8",
    )
    (templates / "local.toml").write_text(
        'harness = "local"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    (harnesses / "badbytes.toml").write_bytes(b"\xff\xfe")
    (templates / "r2.toml").write_text(
        'harness = "badbytes"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    locked = templates / "personas" / "locked.md"
    locked.write_text("# Locked\n\nYou cannot read this.\n", encoding="utf-8")
    locked.chmod(0)
    (templates / "sealed.toml").write_text(
        'harness = "fake"\npersona = "personas/locked.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    try:
        assert main(["roster", "list"]) == 0
        out = capsys.readouterr().out
        assert "quiet\tfake\tbuiltin\t\n" in out
        assert "stranger\tno-such\tinvalid\tYou answer a bounded question with evidence" in out
        assert "local\tlocal\tuser\tYou answer a bounded question with evidence" in out
        assert "r2\tbadbytes\tinvalid\tYou answer a bounded question with evidence" in out
        assert "sealed\tfake\tbuiltin\t\n" in out
        assert main(["roster", "list", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        by_role = {item["role"]: item for item in data["templates"]}
        assert by_role["quiet"]["description"] == ""
        assert by_role["quiet"]["preset"] == "builtin"
        assert by_role["stranger"]["preset"] == "invalid"
        assert by_role["local"]["preset"] == "user"
        assert by_role["r2"]["preset"] == "invalid"
        assert by_role["sealed"]["description"] == ""
        assert main(["roster", "show", "r2"]) == 0
        assert "preset: invalid" in capsys.readouterr().out
        assert main(["roster", "show", "sealed"]) == 0
        shown = capsys.readouterr().out
        assert "description: \n" in shown or shown.endswith("description:\n")
        registry = load_registry(project)
        registry["seats"]["sealed-1"] = {
            "name": "sealed-1",
            "template": "sealed",
            "harness": "fake",
            "model": "",
            "window_id": "",
            "state": "",
            "worktree": "",
            "branch": "",
            "session_id": "",
        }
        save_registry(project, registry)
        assert main(["seat", "list"]) == 0
        assert "sealed-1\tsealed\tdead\t\tfake\t\n" in capsys.readouterr().out
    finally:
        locked.chmod(0o644)


def test_unreadable_utf8_persona_lists_with_an_empty_description(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    templates = project / ".foil" / "templates"
    (templates / "personas" / "bad.md").write_bytes(b"# Bad\n\xff\xfe")
    (templates / "personas" / "garbled.md").write_bytes(b"\xff")
    (templates / "bad.toml").write_text(
        'harness = "fake"\npersona = "personas/bad.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    list_roster(project, as_json=False)
    out = capsys.readouterr().out
    assert "bad\tfake\tbuiltin\t\n" in out
    assert "+ garbled\t\n" in out
    registry = load_registry(project)
    registry["seats"]["bad-1"] = {
        "name": "bad-1",
        "template": "bad",
        "harness": "fake",
        "model": "",
        "window_id": "",
        "state": "",
        "worktree": "",
        "branch": "",
        "session_id": "",
    }
    save_registry(project, registry)
    list_seats(project)
    listed = capsys.readouterr().out
    assert "bad-1\tbad\tdead\t\tfake\t\n" in listed


def test_seat_list_keeps_an_unreadable_template(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    templates = project / ".foil" / "templates"
    (templates / "garbled.toml").write_bytes(b"\xff\xfe")
    locked = templates / "locked.toml"
    locked.write_text(
        'harness = "fake"\npersona = "personas/lead.md"\nworktree = false\npermission = "ask"\n',
        encoding="utf-8",
    )
    locked.chmod(0)
    registry = load_registry(project)
    for name, template in (("lead-1", "lead"), ("garbled-1", "garbled"), ("locked-1", "locked")):
        registry["seats"][name] = {
            "name": name,
            "template": template,
            "harness": "fake",
            "model": "",
            "window_id": "",
            "state": "",
            "worktree": "",
            "branch": "",
            "session_id": "",
        }
    save_registry(project, registry)
    try:
        assert main(["seat", "list"]) == 0
        out = capsys.readouterr().out
        assert "lead-1\tlead\tdead\t\tfake\tYou are the lead seat" in out
        assert "garbled-1\tgarbled\tdead\t\tfake\t\n" in out
        assert "locked-1\tlocked\tdead\t\tfake\t\n" in out
    finally:
        locked.chmod(0o644)


def test_a_credential_shaped_persona_line_is_never_printed(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    templates = project / ".foil" / "templates"
    (templates / "personas" / "leaky.md").write_text("# Leaky\n\nghp_canary\n", encoding="utf-8")
    (templates / "personas" / "loose.md").write_text("# Loose\n\nghp_canary\n", encoding="utf-8")
    (templates / "leaky.toml").write_text(
        'harness = "fake"\npersona = "personas/leaky.md"\nworktree = false\n'
        'permission = "ask"\n',
        encoding="utf-8",
    )
    registry = load_registry(project)
    registry["seats"]["leaky-1"] = {
        "name": "leaky-1",
        "template": "leaky",
        "harness": "fake",
        "model": "",
        "window_id": "",
        "state": "",
        "worktree": "",
        "branch": "",
        "session_id": "",
    }
    save_registry(project, registry)
    outputs = []
    for argv in (
        ["roster", "list"],
        ["roster", "list", "--json"],
        ["roster", "show", "leaky"],
        ["roster", "show", "leaky", "--json"],
        ["seat", "list"],
        ["seat", "list", "--json"],
    ):
        assert main(argv) == 0
        outputs.append(capsys.readouterr().out)
    assert all("ghp_canary" not in out for out in outputs)
    listed = json.loads(outputs[1])
    leaky = next(item for item in listed["templates"] if item["role"] == "leaky")
    assert leaky["description"] == ""
    assert {"name": "loose", "description": ""} in listed["personas"]
    assert "leaky\tfake\tbuiltin\t\n" in outputs[0]
    assert "+ loose\t\n" in outputs[0]
    assert json.loads(outputs[3])["description"] == ""
    seat = next(row for row in json.loads(outputs[5]) if row["name"] == "leaky-1")
    assert seat["description"] == ""


def test_a_persona_through_a_symlinked_directory_is_refused(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = project / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("# X\n\nOutside text.\n", encoding="utf-8")
    templates = project / ".foil" / "templates"
    (templates / "ext").symlink_to(outside, target_is_directory=True)
    (templates / "ext.toml").write_text(
        'harness = "fake"\npersona = "ext/x.md"\nworktree = false\npermission = "ask"\n',
        encoding="utf-8",
    )
    from foil.presets import load_template, persona_text

    with pytest.raises(FoilError, match="persona path must stay inside .foil/templates"):
        persona_text(load_template(project, "ext"))
    assert main(["roster", "list"]) == 0
    listed = capsys.readouterr().out
    assert "ext\tfake\tbuiltin\t\n" in listed
    assert main(["roster", "show", "ext", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["description"] == ""
    assert "Outside text" not in listed
