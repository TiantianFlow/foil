"""Roster management operations: list, show, add, update, remove."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from foil.cli import main
from foil.errors import FoilError
from foil.lifecycle import init_project, list_seats
from foil.project import foil_root, store_root
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
    # Init still writes the defaults into the project. This task's writes are
    # global, and the loader this file calls still reads the project, so the
    # fixture moves those defaults onto the global layer and leaves a copy
    # where the loader looks.
    project_root = foil_root(tmp_path)
    home = store_root()
    for name in ("roles.toml", "mapping.toml"):
        source = project_root / name
        if source.is_file():
            (home / name).write_bytes(source.read_bytes())
            source.unlink()
    from importlib.resources import files

    personas = project_root / "templates" / "personas"
    personas.mkdir(parents=True, exist_ok=True)
    packaged = files("foil").joinpath("defaults", "personas")
    for item in packaged.iterdir():
        target = personas / item.name
        if item.name.endswith(".md") and not target.exists():
            target.write_bytes(item.read_bytes())
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_roster_list_shows_default_templates(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """list_roster shows the three default templates."""
    list_roster(project, as_json=False)
    out = capsys.readouterr().out
    assert "\tpackaged\tYou are the lead seat of this fleet." in out
    assert "\tpackaged\tYou make the change the task asks for," in out
    assert "\tpackaged\tYou check the change the lead names" in out
    assert "\tlayer\tglobal\n" in out
    assert "\tmapping\tglobal\n" in out


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
    assert data["templates"][0]["preset"][0] in {"packaged", "global", "project", "invalid"}
    assert data["templates"][0]["layer"] in {"global", "project", "fleet"}
    assert data["templates"][0]["mapping"] in {"global", "project", "fleet"}
    assert isinstance(data["templates"][0]["harness"], list)
    assert "description" in data["templates"][0]


def test_roster_show_displays_template(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show_template displays one template's configuration."""
    show_template(project, "lead", as_json=False)
    out = capsys.readouterr().out
    assert "role: lead" in out
    assert "harness:" in out
    assert "permission:" in out
    assert "preset: packaged" in out
    assert "layer: global" in out
    assert "mapping: global" in out
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
    assert data["preset"] == ["packaged"]
    assert data["layer"] == "global"
    assert data["mapping"] == "global"
    assert isinstance(data["harness"], list)
    assert "model" not in data
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
    roles = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "researcher"' in roles
    assert 'persona = "personas/researcher.md"' in roles
    assert 'permission = "auto"' in roles


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
        "worktree = true\n"
        'permission = "auto"\n',
        encoding="utf-8",
    )

    add_template(project, "custom", from_file=str(toml_file))
    out = capsys.readouterr().out
    assert "created template 'custom'" in out

    mapping = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "custom"' in mapping
    assert "claude-sonnet-5.5" in mapping


def test_roster_add_invalid_role_name_fails(project: Path) -> None:
    """add_template fails for invalid role name."""
    with pytest.raises(FoilError, match="invalid role name"):
        add_template(project, "bad/name", from_file=None)


def test_roster_update_harness(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the harness field."""
    update_template(project, "lead", "harness", "codex")
    out = capsys.readouterr().out
    assert "updated template 'lead'" in out

    content = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "lead"' in content
    assert 'harness = "codex"' in content


def test_roster_update_model(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the model field."""
    update_template(project, "implementer", "model", "claude-opus-5.5")
    out = capsys.readouterr().out
    assert "updated template 'implementer'" in out

    content = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'model = "claude-opus-5.5"' in content


def test_roster_update_permission(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the permission field."""
    update_template(project, "reviewer", "permission", "auto")
    out = capsys.readouterr().out
    assert "updated template 'reviewer'" in out

    content = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "reviewer"' in content
    assert 'permission = "auto"' in content


def test_roster_update_worktree(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """update_template changes the worktree field."""
    update_template(project, "lead", "worktree", "true")
    out = capsys.readouterr().out
    assert "updated template 'lead'" in out

    content = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "lead"' in content
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

    roles = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "researcher"' not in roles


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
    root = store_root()
    before = (
        (root / "roles.toml").read_bytes(),
        (root / "mapping.toml").read_bytes(),
    )

    assert main(["roster", "update", "lead", "bad\nfield=value"]) == 1
    updated = capsys.readouterr().err
    assert updated.count("\n") == 1
    assert "invalid field 'badfield'" in updated
    assert ((root / "roles.toml").read_bytes(), (root / "mapping.toml").read_bytes()) == before

    assert main(["roster", "add", "custom", "--from", "missing\nfile.toml"]) == 1
    missing = capsys.readouterr().err
    assert missing.count("\n") == 1
    assert "file not found: missingfile.toml" in missing
    assert not (foil_root(project) / "templates" / "custom.toml").exists()


def test_roster_add_does_not_follow_a_symlink(project: Path) -> None:
    """A template symlink is not treated as a missing file."""
    outside = project / "escaped.toml"
    outside.write_text("keep\n", encoding="utf-8")
    link = store_root() / "roles.toml"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(FoilError, match="refusing symlink"):
        add_template(project, "researcher", from_file=None)
    assert outside.read_text(encoding="utf-8") == "keep\n"
    assert link.is_symlink()


def test_roster_add_from_file_does_not_follow_a_symlink(
    project: Path,
    tmp_path: Path,
) -> None:
    """--from does not write through a template symlink, then delete the link."""
    outside = project / "escaped.toml"
    outside.write_text("original\n", encoding="utf-8")
    link = store_root() / "roles.toml"
    link.unlink()
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
        'harness = "claude"\npersona = "personas/lead.md"\nworktree = false\npermission = "ask"\n',
        encoding="utf-8",
    )
    link = store_root() / "roles.toml"
    link.unlink()
    link.symlink_to(outside)
    with pytest.raises(FoilError, match="refusing symlink"):
        update_template(project, "lead", "model", "other")
    assert "other" not in outside.read_text(encoding="utf-8")
    assert link.is_symlink()


def test_roster_cli_list(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """foil roster list command works."""
    code = main(["roster", "list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "\tpackaged\tYou are the lead seat of this fleet." in out


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
    templates = foil_root(project) / "templates"
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
    before = _roster_bytes(project)
    with pytest.raises(FoilError):
        update_template(project, "lead", "model", 'say "hi"')
    assert _roster_bytes(project) == before


def test_unknown_harness_and_parent_persona_roll_back(project: Path) -> None:
    before = _roster_bytes(project)
    with pytest.raises(FoilError, match="unknown harness"):
        update_template(project, "lead", "harness", "not-a-harness")
    assert _roster_bytes(project) == before
    with pytest.raises(FoilError, match="must stay inside"):
        update_template(project, "lead", "persona", "../secret.md")
    assert _roster_bytes(project) == before


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
    assert not (foil_root(project) / "templates" / "custom.toml").exists()


def test_init_report_names_personas_without_templates(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init_project(str(project))
    out = capsys.readouterr().out
    line = next(item for item in out.splitlines() if item.startswith("Available personas"))
    names = [item.strip() for item in line.split(":", 1)[1].split(",")]
    assert "researcher" in names
    for role in ("lead", "implementer", "reviewer"):
        assert role not in names


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


def test_remove_refuses_a_live_seat_and_a_harness_edit_does_not(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seat(project, state="")
    with pytest.raises(FoilError, match="still in use"):
        remove_template(project, "researcher")
    assert 'id = "researcher"' in (store_root() / "roles.toml").read_text(encoding="utf-8")
    update_template(project, "researcher", "harness", "codex")
    text = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "researcher"' in text
    assert 'harness = "codex"' in text
    assert load_registry(project)["seats"]["researcher-1"]["harness"] == "fake"
    list_seats(project)
    assert "researcher-1\tresearcher\tdead\t\tfake\t" in capsys.readouterr().out


def test_killed_seat_does_not_block_remove(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seat(project, state="killed")
    remove_template(project, "researcher")
    assert "removed template 'researcher'" in capsys.readouterr().out
    assert 'id = "researcher"' not in (store_root() / "roles.toml").read_text(
        encoding="utf-8"
    )


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


def _seal(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``path`` unreadable, even where chmod alone does not (as root)."""
    path.chmod(0)
    try:
        path.read_bytes()
    except PermissionError:
        return
    read_text, read_bytes = Path.read_text, Path.read_bytes

    def refuse(original: Callable[..., Any]) -> Callable[..., Any]:
        def read(self: Path, *args: Any, **kwargs: Any) -> Any:
            if self.resolve() == path.resolve():
                raise PermissionError(13, "Permission denied", str(self))
            return original(self, *args, **kwargs)

        return read

    monkeypatch.setattr(Path, "read_text", refuse(read_text))
    monkeypatch.setattr(Path, "read_bytes", refuse(read_bytes))


def _install(project: Path, role: str, body: str) -> None:
    """Seed one role on the global layer. ``project`` is unused; the caller has a checkout."""
    del project
    from foil.presets import parse_template_text, render_maps, render_roles

    parsed = parse_template_text(body)
    root = store_root()
    roles = _roles_of(root)
    roles = [item for item in roles if item["id"] != role]
    roles.append(
        {
            "id": role,
            "description": "",
            "persona": parsed["persona"],
            "worktree": parsed["worktree"],
            "permission": parsed["permission"],
        }
    )
    maps = [item for item in _maps_of(root) if item["role"] != role]
    for entry in parsed["harness"]:
        maps.append({"role": role, "id": entry["id"], "model": entry["model"]})
    (root / "roles.toml").write_text(render_roles(roles), encoding="utf-8")
    (root / "mapping.toml").write_text(render_maps(maps), encoding="utf-8")


def _roles_of(root: Path) -> list[dict[str, Any]]:
    from foil.presets import _parse_roles

    path = root / "roles.toml"
    if not path.is_file():
        return []
    return _parse_roles(path.read_text(encoding="utf-8"))


def _global_role(role: str) -> dict[str, Any]:
    match = next(item for item in _roles_of(store_root()) if item["id"] == role)
    return match


def _global_maps(role: str) -> list[dict[str, str]]:
    return [
        {"id": item["id"], "model": item["model"]}
        for item in _maps_of(store_root())
        if item["role"] == role
    ]


def _maps_of(root: Path) -> list[dict[str, str]]:
    from foil.presets import _parse_maps

    path = root / "mapping.toml"
    if not path.is_file():
        return []
    return _parse_maps(path.read_text(encoding="utf-8"))


def _roster_bytes(project: Path) -> bytes:
    """The project override, when one exists. Unflagged writes leave this unchanged."""
    root = foil_root(project)
    chunks = [
        (root / name).read_bytes() if (root / name).is_file() else b""
        for name in ("roles.toml", "mapping.toml")
    ]
    return b"".join(chunks)


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


def test_lead_cannot_add_from_with_ask_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _permission_source(project / "ask.toml", "ask")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    before = (store_root() / "roles.toml").read_bytes()
    assert main(["roster", "add", "custom", "--from", str(source)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == "foil: roles are outside the fleet only"
    assert (store_root() / "roles.toml").read_bytes() == before


def test_lead_can_add_from_when_permission_is_auto(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _permission_source(project / "plain.toml", None)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    before = (store_root() / "roles.toml").read_bytes()
    assert main(["roster", "add", "custom", "--from", str(source)]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == "foil: roles are outside the fleet only"
    assert (store_root() / "roles.toml").read_bytes() == before


def test_outside_caller_can_add_from_with_auto_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
) -> None:
    source = _permission_source(project / "auto.toml", "auto")
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["roster", "add", "custom", "--from", str(source)]) == 0
    text = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "custom"' in text
    assert 'permission = "auto"' in text


def test_lead_cannot_set_a_leading_dash_model(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """model=--yolo must not be written, or later placed as a harness flag."""
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    before = _roster_bytes(project)
    assert main(["roster", "update", "implementer", "model=--yolo"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "must not start with '-'" in err
    assert _roster_bytes(project) == before
    for field in ("persona", "harness"):
        assert main(["roster", "update", "implementer", f"{field}=--yolo"]) == 1
        assert _roster_bytes(project) == before
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
    assert not (foil_root(project) / "templates" / "custom.toml").exists()


def test_lead_cannot_inject_permission_through_another_field(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A quoted newline in another field must not set permission.

    The source omits permission, which counts as auto. On the unfixed writer
    the model value closes its string and adds permission = "ask".
    """
    source = _permission_source(project / "plain.toml", None)
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["roster", "add", "custom", "--from", str(source)]) == 0
    capsys.readouterr()
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    before = _roster_bytes(project)
    injected = 'm"\npermission = "ask'
    assert main(["roster", "update", "custom", f"model={injected}"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "quote, a backslash, or a control character" in err
    assert _roster_bytes(project) == before
    assert _global_role("custom")["permission"] == "auto"


def test_in_fleet_update_rolls_back_a_permission_change(project: Path) -> None:
    before = _roster_bytes(project)
    with pytest.raises(FoilError, match="permission is outside the fleet only"):
        update_template(project, "lead", "permission", "ask", in_fleet=True)
    assert _roster_bytes(project) == before


def test_lead_cannot_set_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _roster_bytes(project)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "lead", "permission=ask"]) == 1
    assert "permission is outside the fleet only" in capsys.readouterr().err
    assert _roster_bytes(project) == before


def test_outside_caller_can_set_permission(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["roster", "update", "reviewer", "permission=auto"]) == 0
    text = (store_root() / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "reviewer"' in text
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
    assert "\tpackaged\tYou are the lead seat of this fleet." in out


def test_roster_list_keeps_a_missing_persona_and_an_unknown_harness(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    planted = foil_root(project) / "templates" / "personas"
    if planted.is_dir():
        for path in planted.iterdir():
            path.unlink()
        planted.rmdir()
    personas = store_root() / "personas"
    personas.mkdir(parents=True, exist_ok=True)
    _install(
        project,
        "quiet",
        'harness = "fake"\npersona = "personas/missing.md"\nworktree = false\npermission = "ask"\n',
    )
    _install(
        project,
        "stranger",
        'harness = "no-such"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
    )
    harnesses = store_root() / "harnesses"
    harnesses.mkdir()
    (harnesses / "local.toml").write_text(
        'id = "local"\ncommand = ["true"]\nsession_id = "none"\n\n'
        "[permission]\nask = []\nauto = []\n",
        encoding="utf-8",
    )
    _install(
        project,
        "local",
        'harness = "local"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
    )
    (harnesses / "badbytes.toml").write_bytes(b"\xff\xfe")
    _install(
        project,
        "r2",
        'harness = "badbytes"\npersona = "personas/researcher.md"\nworktree = false\n'
        'permission = "ask"\n',
    )
    locked = personas / "locked.md"
    locked.write_text("# Locked\n\nYou cannot read this.\n", encoding="utf-8")
    _seal(locked, monkeypatch)
    _install(
        project,
        "sealed",
        'harness = "fake"\npersona = "personas/locked.md"\nworktree = false\npermission = "ask"\n',
    )
    try:
        assert main(["roster", "list"]) == 0
        out = capsys.readouterr().out
        assert "quiet\tfake\tpackaged\t\n" in out
        assert "stranger\tno-such\tinvalid\tYou answer a bounded question with evidence" in out
        assert "local\tlocal\tglobal\tYou answer a bounded question with evidence" in out
        assert "r2\tbadbytes\tinvalid\tYou answer a bounded question with evidence" in out
        assert "sealed\tfake\tpackaged\t\n" in out
        assert main(["roster", "list", "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        by_role = {item["role"]: item for item in data["templates"]}
        assert by_role["quiet"]["description"] == ""
        assert by_role["quiet"]["preset"] == ["packaged"]
        assert by_role["stranger"]["preset"] == ["invalid"]
        assert by_role["local"]["preset"] == ["global"]
        assert by_role["r2"]["preset"] == ["invalid"]
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
    planted = foil_root(project) / "templates" / "personas"
    if planted.is_dir():
        for path in planted.iterdir():
            path.unlink()
        planted.rmdir()
    personas = store_root() / "personas"
    personas.mkdir(parents=True, exist_ok=True)
    (personas / "bad.md").write_bytes(b"# Bad\n\xff\xfe")
    (personas / "garbled.md").write_bytes(b"\xff")
    _install(
        project,
        "bad",
        'harness = "fake"\npersona = "personas/bad.md"\nworktree = false\npermission = "ask"\n',
    )
    list_roster(project, as_json=False)
    out = capsys.readouterr().out
    assert "bad\tfake\tpackaged\t\n" in out
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
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = foil_root(project) / "templates"
    # seat list still reads the project role file. Put the defaults back there.
    if not (foil_root(project) / "roles.toml").is_file():
        (foil_root(project) / "roles.toml").write_bytes((store_root() / "roles.toml").read_bytes())
        (foil_root(project) / "mapping.toml").write_bytes(
            (store_root() / "mapping.toml").read_bytes()
        )
    (templates / "garbled.toml").write_bytes(b"\xff\xfe")
    locked = templates / "locked.toml"
    locked.write_text(
        'harness = "fake"\npersona = "personas/lead.md"\nworktree = false\npermission = "ask"\n',
        encoding="utf-8",
    )
    _seal(locked, monkeypatch)
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
    planted = foil_root(project) / "templates" / "personas"
    if planted.is_dir():
        for path in planted.iterdir():
            path.unlink()
        planted.rmdir()
    personas = store_root() / "personas"
    personas.mkdir(parents=True, exist_ok=True)
    (personas / "leaky.md").write_text("# Leaky\n\nghp_canary\n", encoding="utf-8")
    (personas / "loose.md").write_text("# Loose\n\nghp_canary\n", encoding="utf-8")
    _install(
        project,
        "leaky",
        'harness = "fake"\npersona = "personas/leaky.md"\nworktree = false\npermission = "ask"\n',
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
    assert "leaky\tfake\tpackaged\t\n" in outputs[0]
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
    templates = foil_root(project) / "templates"
    (templates / "ext").symlink_to(outside, target_is_directory=True)
    _install(
        project,
        "ext",
        'harness = "fake"\npersona = "ext/x.md"\nworktree = false\npermission = "ask"\n',
    )
    from foil.presets import persona_text

    with pytest.raises(FoilError, match="persona path must stay inside"):
        persona_text(
            {
                "persona": "ext/x.md",
                "path": foil_root(project) / "templates" / "ext.toml",
            }
        )
    assert main(["roster", "list"]) == 0
    listed = capsys.readouterr().out
    assert "ext\tfake\tpackaged\t\n" in listed
    assert main(["roster", "show", "ext", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["description"] == ""
    assert "Outside text" not in listed


def test_roster_update_keeps_the_model_when_the_harness_changes(project: Path) -> None:
    _install(
        project,
        "lead",
        'harness = "claude"\n'
        'model = "opus"\n'
        'persona = "personas/lead.md"\n'
        "worktree = false\n"
        'permission = "auto"\n',
    )
    update_template(project, "lead", "harness", "codex")
    text = (store_root() / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "lead"' in text
    assert 'harness = "codex"' in text
    assert 'model = "opus"' in text
    assert _global_maps("lead") == [{"id": "codex", "model": "opus"}]


def test_roster_update_edits_a_list_only_as_a_whole(project: Path) -> None:
    _install(
        project,
        "lead",
        'persona = "personas/lead.md"\n'
        "worktree = false\n"
        'permission = "auto"\n'
        "\n"
        "[[harness]]\n"
        'id = "claude"\n'
        'model = "opus"\n'
        "\n"
        "[[harness]]\n"
        'id = "codex"\n'
        'model = "gpt-5.5"\n',
    )
    before = _roster_bytes(project)
    with pytest.raises(FoilError, match="more than one harness entry.*harness=ID/MODEL"):
        update_template(project, "lead", "model", "other")
    for bad in ("claude/opus,", "claude/opus,codex/-x", "claude,bad id", "nope/x"):
        with pytest.raises(FoilError):
            update_template(project, "lead", "harness", bad)
    assert _roster_bytes(project) == before
    others = [row for row in _maps_of(store_root()) if row["role"] != "lead"]
    update_template(project, "lead", "harness", "codex/gpt-5.5,claude,gemini/pro/2")
    assert _global_maps("lead") == [
        {"id": "codex", "model": "gpt-5.5"},
        {"id": "claude", "model": ""},
        {"id": "gemini", "model": "pro/2"},
    ]
    assert [row for row in _maps_of(store_root()) if row["role"] != "lead"] == others
    update_template(project, "lead", "harness", "gemini")
    assert _global_maps("lead") == [{"id": "gemini", "model": ""}]
    update_template(project, "lead", "persona", "personas/reviewer.md")
    assert _global_role("lead")["persona"] == "personas/reviewer.md"


def _cli(argv: list[str]) -> int:
    """Run one roster command. A missing flag fails this test."""
    try:
        return main(argv)
    except SystemExit as exc:
        raise AssertionError(f"{' '.join(argv)} is not accepted ({exc})") from exc


def _layer_text(root: Path, name: str) -> str:
    path = root / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _seed_global(role: str, *, harness: str = "claude", model: str = "") -> None:
    """Put one role on the global layer beside the roles already there."""
    from foil.presets import render_maps, render_roles

    root = store_root()
    root.mkdir(parents=True, exist_ok=True)
    roles = [item for item in _roles_of(root) if item["id"] != role]
    roles.append(
        {
            "id": role,
            "description": "",
            "persona": f"personas/{role}.md",
            "worktree": False,
            "permission": "auto",
        }
    )
    maps = [item for item in _maps_of(root) if item["role"] != role]
    maps.append({"role": role, "id": harness, "model": model})
    (root / "roles.toml").write_text(render_roles(roles), encoding="utf-8")
    (root / "mapping.toml").write_text(render_maps(maps), encoding="utf-8")


def test_unflagged_add_writes_the_global_layer_not_the_project(
    project: Path,
) -> None:
    """A checkout does not switch an unflagged add onto the project folder."""
    before = _roster_bytes(project)
    assert _cli(["roster", "add", "researcher"]) == 0
    assert _roster_bytes(project) == before
    assert 'id = "researcher"' in _layer_text(store_root(), "roles.toml")
    assert 'role = "researcher"' in _layer_text(store_root(), "mapping.toml")


def test_unflagged_update_writes_the_global_layer_not_the_project(
    project: Path,
) -> None:
    """An unflagged harness update edits the global mapping and leaves the project."""
    before = _roster_bytes(project)
    assert _cli(["roster", "update", "lead", "harness=codex"]) == 0
    assert _roster_bytes(project) == before
    assert 'role = "lead"' in _layer_text(store_root(), "mapping.toml")
    assert 'harness = "codex"' in _layer_text(store_root(), "mapping.toml")


def test_unflagged_remove_writes_the_global_layer_not_the_project(
    project: Path,
) -> None:
    """An unflagged remove deletes the global role and leaves the project folder."""
    _seed_global("researcher")
    before = _roster_bytes(project)
    assert _cli(["roster", "remove", "researcher"]) == 0
    assert _roster_bytes(project) == before
    assert 'id = "researcher"' not in _layer_text(store_root(), "roles.toml")
    assert 'role = "researcher"' not in _layer_text(store_root(), "mapping.toml")


def test_project_flag_writes_the_project_override(project: Path) -> None:
    """--project writes the project role and mapping, and needs this checkout."""
    assert _cli(["roster", "add", "researcher", "--project"]) == 0
    assert 'id = "researcher"' in _layer_text(foil_root(project), "roles.toml")
    assert 'role = "researcher"' in _layer_text(foil_root(project), "mapping.toml")
    assert 'id = "researcher"' not in _layer_text(store_root(), "roles.toml")


def test_project_flag_needs_a_checkout(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without a checkout, --project fails in one line and writes nothing."""
    for name in ("roles.toml", "mapping.toml"):
        path = store_root() / name
        if path.is_file():
            path.unlink()
    before = _roster_bytes(project)
    outside = project.parent / "no-checkout"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert _cli(["roster", "add", "researcher", "--project"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == "foil: --project needs a checkout"
    assert _roster_bytes(project) == before
    assert _layer_text(store_root(), "roles.toml") == ""


def test_project_flag_is_refused_inside_a_fleet(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Inside a fleet, --project is refused and writes nothing."""
    for name in ("roles.toml", "mapping.toml"):
        path = store_root() / name
        if path.is_file():
            path.unlink()
    before = _roster_bytes(project)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert _cli(["roster", "add", "researcher", "--project"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == "foil: --project is outside the fleet only"
    assert _roster_bytes(project) == before
    assert _layer_text(store_root(), "roles.toml") == ""


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["roster", "add", "researcher"], "foil: roles are outside the fleet only"),
        (["roster", "remove", "lead"], "foil: roles are outside the fleet only"),
        (
            ["roster", "update", "lead", "persona=personas/reviewer.md"],
            "foil: roles are outside the fleet only",
        ),
        (
            ["roster", "update", "lead", "worktree=true"],
            "foil: roles are outside the fleet only",
        ),
        (
            ["roster", "update", "lead", "permission=ask"],
            "foil: permission is outside the fleet only",
        ),
    ],
)
def test_in_fleet_role_edits_are_refused(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    message: str,
) -> None:
    """Inside a fleet, add, remove, persona, worktree, and permission write nothing."""
    before = _roster_bytes(project)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert _cli(argv) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert err.strip() == message
    assert _roster_bytes(project) == before


def test_remove_project_deletes_a_mapping_only_override(project: Path) -> None:
    """remove --project deletes mapping rows when the project has no role row."""
    root = foil_root(project)
    # The fixture moved init's defaults to the global layer. Put them back
    # before the extra global role, so the project file has no role row for it.
    if not (root / "roles.toml").is_file():
        (root / "roles.toml").write_bytes((store_root() / "roles.toml").read_bytes())
    if not (root / "mapping.toml").is_file():
        (root / "mapping.toml").write_bytes((store_root() / "mapping.toml").read_bytes())
    _seed_global("researcher", model="kept")
    roles_before = (root / "roles.toml").read_bytes()
    maps = root / "mapping.toml"
    original = maps.read_text(encoding="utf-8")
    maps.write_text(
        original + '\n[[map]]\nrole = "researcher"\nharness = "claude"\nmodel = "opus"\n',
        encoding="utf-8",
    )
    assert _cli(["roster", "remove", "researcher", "--project"]) == 0
    assert (root / "roles.toml").read_bytes() == roles_before
    assert 'role = "researcher"' not in maps.read_text(encoding="utf-8")
    assert 'id = "researcher"' in _layer_text(store_root(), "roles.toml")
    assert 'model = "kept"' in _layer_text(store_root(), "mapping.toml")


def test_remove_project_fails_when_the_project_has_neither(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """remove --project fails in one line when the project has no row and no mapping."""
    for name in ("roles.toml", "mapping.toml"):
        path = store_root() / name
        if path.is_file():
            path.unlink()
    before = _roster_bytes(project)
    assert _cli(["roster", "remove", "researcher", "--project"]) == 1
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert "\n" not in err.strip()
    assert _roster_bytes(project) == before
    assert _layer_text(store_root(), "roles.toml") == ""


def _drop_planted_personas(project: Path) -> None:
    """The fixture copies packaged personas into a directory init deletes.

    A test that leaves that copy does not show the packaged lookup.
    """
    planted = foil_root(project) / "templates" / "personas"
    if planted.is_dir():
        for path in planted.iterdir():
            path.unlink()
        planted.rmdir()
        parent = planted.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()


def test_packaged_persona_is_enough_after_init(project: Path) -> None:
    """F21: personas/ROLE.md resolves through the lookup. A packaged file is enough."""
    _drop_planted_personas(project)
    assert not (foil_root(project) / "templates" / "personas").exists()
    assert _cli(["roster", "update", "lead", "worktree=true"]) == 0
    assert _global_role("lead")["worktree"] is True
    assert _cli(["roster", "add", "researcher"]) == 0
    text = _layer_text(store_root(), "roles.toml")
    assert 'id = "researcher"' in text
    assert 'persona = "personas/researcher.md"' in text


def test_first_project_update_writes_one_layer(project: Path) -> None:
    """F21: the first --project harness write is a mapping list and no role row.

    The first persona, worktree, or permission write is a full role row.
    """
    _drop_planted_personas(project)
    root = foil_root(project)
    for name in ("roles.toml", "mapping.toml"):
        path = root / name
        if path.is_file():
            path.unlink()
    assert _cli(["roster", "update", "lead", "harness=grok", "--project"]) == 0
    assert not (root / "roles.toml").exists()
    assert 'role = "lead"' in _layer_text(root, "mapping.toml")
    assert 'harness = "grok"' in _layer_text(root, "mapping.toml")
    (root / "mapping.toml").unlink()
    assert _cli(["roster", "update", "lead", "worktree=true", "--project"]) == 0
    assert not (root / "mapping.toml").exists()
    role = _roles_of(root)
    lead = next(item for item in role if item["id"] == "lead")
    assert lead["worktree"] is True
    assert lead["persona"]
    assert lead["permission"] in {"ask", "auto"}


def test_roster_outside_a_checkout_uses_the_global_layer(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F21: unflagged roster commands do not need a checkout. Only --project does."""
    _drop_planted_personas(project)
    outside = project.parent / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    assert _cli(["roster", "list"]) == 0
    out = capsys.readouterr().out
    assert "not a git repository" not in out
    assert "lead" in out
    assert "+ researcher" in out
    assert _cli(["roster", "remove", "missing-role"]) == 1
    err = capsys.readouterr().err
    assert "not a git repository" not in err
    assert "unknown template" in err


def test_list_names_the_layer_of_the_role_and_the_mapping(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F21: preset source is packaged, global, project, or invalid, and layers are named."""
    _drop_planted_personas(project)
    assert _cli(["roster", "list"]) == 0
    listed = capsys.readouterr().out
    assert "\tpackaged\t" in listed
    assert "\tbuiltin\t" not in listed
    assert "\tlayer\tglobal\n" in listed
    assert "\tmapping\tglobal\n" in listed
    assert _cli(["roster", "show", "lead"]) == 0
    shown = capsys.readouterr().out
    assert "preset: packaged" in shown
    assert "layer: global" in shown
    assert "mapping: global" in shown
    root = foil_root(project)
    (root / "mapping.toml").write_text(
        '[[map]]\nrole = "lead"\nharness = "claude"\n', encoding="utf-8"
    )
    assert _cli(["roster", "show", "lead"]) == 0
    assert "mapping: project" in capsys.readouterr().out


def test_in_fleet_model_uses_a_project_mapping_only_override(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """F21 and F3: the effective list counts. One project mapping entry allows model=."""
    from foil.presets import render_maps

    _drop_planted_personas(project)
    root = store_root()
    maps = [item for item in _maps_of(root) if item["role"] != "implementer"]
    maps.append({"role": "implementer", "id": "claude", "model": "a"})
    maps.append({"role": "implementer", "id": "codex", "model": "b"})
    (root / "mapping.toml").write_text(render_maps(maps), encoding="utf-8")
    (foil_root(project) / "mapping.toml").write_text(
        '[[map]]\nrole = "implementer"\nharness = "claude"\nmodel = "one"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert _cli(["roster", "update", "implementer", "model=z"]) == 0
    err = capsys.readouterr().err
    assert "more than one" not in err
    text = (
        foil_root(project) / "fleets" / "main" / "overrides.toml"
    ).read_text(encoding="utf-8")
    assert 'role = "implementer"' in text
    assert 'model = "z"' in text


def test_list_shows_a_packaged_persona_with_no_template(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F21: list shows every persona that has no template, including the package."""
    _drop_planted_personas(project)
    assert not (store_root() / "personas").exists()
    assert _cli(["roster", "list"]) == 0
    assert "+ researcher" in capsys.readouterr().out


def test_remove_sees_a_seat_in_another_fleet(project: Path) -> None:
    """F21: remove fails while any fleet in the store has a live seat of that role."""
    import json

    from foil.store import SCHEMA_VERSION

    _drop_planted_personas(project)
    _seed_global("researcher")
    other = foil_root(project) / "fleets" / "other" / "run"
    other.mkdir(parents=True)
    other.joinpath("registry.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "fleet_id": "other",
                "tmux_session": "",
                "lead": "",
                "seats": {
                    "researcher-1": {
                        "name": "researcher-1",
                        "template": "researcher",
                        "harness": "fake",
                        "model": "",
                        "window_id": "",
                        "state": "dead",
                        "worktree": "",
                        "branch": "",
                        "session_id": "",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FoilError, match="still in use"):
        remove_template(project, "researcher")
    assert 'id = "researcher"' in _layer_text(store_root(), "roles.toml")
    seat = json.loads(other.joinpath("registry.json").read_text(encoding="utf-8"))
    seat["seats"]["researcher-1"]["state"] = "killed"
    other.joinpath("registry.json").write_text(json.dumps(seat), encoding="utf-8")
    assert _cli(["roster", "remove", "researcher"]) == 0
    assert 'id = "researcher"' not in _layer_text(store_root(), "roles.toml")
