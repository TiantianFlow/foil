"""Roster management operations: list, show, add, update, remove templates."""

from __future__ import annotations

import json
from pathlib import Path

from foil.errors import FoilError, shown
from foil.presets import (
    entry_label,
    installed_harness,
    load_preset,
    load_template,
    path_description,
    persona_stems,
    preset_source,
    read_description,
    render_template,
    template_permission,
)
from foil.project import foil_root
from foil.store import SAFE_ID, create_exclusive, load_registry, save_registry, write_bytes

_PROTECTED_ROLES = {"lead", "implementer", "reviewer"}
_TEMPLATE_FIELDS = {"harness", "model", "persona", "worktree", "permission"}


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")


def _create_template_file(path: Path, content: str) -> None:
    """Create a template without following a symlink out of the directory."""
    _refuse_symlink(path)
    if path.exists():
        raise FoilError(f"foil: template '{path.stem}' already exists")
    try:
        create_exclusive(path, content.encode())
    except FileExistsError:
        _refuse_symlink(path)
        raise FoilError(f"foil: template '{path.stem}' already exists") from None


def _replace_template_file(path: Path, content: str) -> None:
    _refuse_symlink(path)
    write_bytes(path, content.encode())


def _formatted_value(field: str, value: str) -> str:
    """Format one template field. String values cannot break out of a TOML string."""
    if field == "worktree":
        if value.lower() not in {"true", "false"}:
            raise FoilError("foil: worktree must be 'true' or 'false'")
        return value.lower()
    if field == "permission":
        if value not in {"ask", "auto"}:
            raise FoilError("foil: permission must be 'ask' or 'auto'")
        return f'"{value}"'
    if field in {"harness", "model", "persona"} and value.startswith("-"):
        raise FoilError("foil: value must not start with '-'")
    if any(char in value for char in '"\\') or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise FoilError(
            "foil: value must not contain a quote, a backslash, or a control character"
        )
    return f'"{value}"'


def _persona_inside(persona: str) -> None:
    if "\n" in persona or "\r" in persona or not persona.endswith(".md"):
        return
    relative = Path(persona)
    if relative.is_absolute() or ".." in relative.parts:
        raise FoilError(
            f"foil: persona path must stay inside the project templates: {persona}"
        )


def _accept(toplevel: Path, role: str) -> None:
    template = load_template(toplevel, role)
    _persona_inside(str(template.get("persona") or ""))
    for entry in template["harness"]:
        load_preset(toplevel, str(entry["id"]))


def _drop_cursor(toplevel: Path, role: str) -> None:
    registry = load_registry(toplevel)
    cursors = registry["harness_cursor"]
    if role not in cursors:
        return
    del cursors[role]
    save_registry(toplevel, registry)


def _in_use(toplevel: Path, role: str) -> None:
    registry = load_registry(toplevel)
    for seat in registry["seats"].values():
        if seat["template"] == role and seat["state"] != "killed":
            raise FoilError(f"foil: template '{role}' is still in use")


def _template_names(toplevel: Path) -> list[str]:
    """List existing template names."""
    templates_dir = foil_root(toplevel) / "templates"
    if not templates_dir.is_dir():
        return []
    return sorted(path.stem for path in templates_dir.glob("*.toml"))


def _listed(toplevel: Path, name: str) -> dict | None:
    try:
        template = load_template(toplevel, name)
    except FoilError:
        return None
    entries = template["harness"]
    return {
        "role": name,
        "harness": [{"id": entry["id"], "model": entry["model"]} for entry in entries],
        "worktree": template.get("worktree", False),
        "permission": template.get("permission", "auto"),
        "preset": [preset_source(toplevel, str(entry["id"])) for entry in entries],
        "description": read_description(template),
    }


def list_roster(toplevel: Path, *, as_json: bool = False) -> None:
    """List all templates and available personas."""
    names = _template_names(toplevel)
    persona_dir = foil_root(toplevel) / "templates" / "personas"
    available = [
        {"name": item, "description": path_description(persona_dir / f"{item}.md")}
        for item in persona_stems(persona_dir)
        if item not in names
    ]
    loaded = [(name, _listed(toplevel, name)) for name in names]
    if as_json:
        missing = {"error": "invalid template", "preset": "invalid", "description": ""}
        templates = [
            row if row is not None else {"role": name, **missing} for name, row in loaded
        ]
        print(json.dumps({"templates": templates, "personas": available}, indent=2))
        return
    for name, row in loaded:
        if row is None:
            print(f"{name}\tinvalid\tinvalid\t")
            continue
        harness = ",".join(entry_label(entry) for entry in row["harness"])
        preset = ",".join(row["preset"])
        print(f"{name}\t{harness}\t{preset}\t{row['description']}")
    for person in available:
        print(f"+ {person['name']}\t{person['description']}")


def show_template(toplevel: Path, role: str, *, as_json: bool = False) -> None:
    """Show one template's configuration."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{shown(role)}'")

    template = load_template(toplevel, role)
    entries = template["harness"]
    preset = [preset_source(toplevel, str(entry["id"])) for entry in entries]
    description = read_description(template)
    harness = ",".join(entry_label(entry) for entry in entries)

    if as_json:
        output = {
            "role": role,
            "harness": [{"id": entry["id"], "model": entry["model"]} for entry in entries],
            "persona": template.get("persona", ""),
            "worktree": template.get("worktree", False),
            "permission": template.get("permission", "auto"),
            "preset": preset,
            "description": description,
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"role: {role}")
        print(f"harness: {harness}")
        if template.get("persona"):
            print(f"persona: {template['persona']}")
        print(f"worktree: {template.get('worktree', False)}")
        print(f"permission: {template.get('permission', 'auto')}")
        print(f"preset: {','.join(preset)}")
        print(f"description: {description}")


def add_template(
    toplevel: Path,
    role: str,
    *,
    from_file: str | None = None,
    in_fleet: bool = False,
) -> None:
    """Create a template from a TOML file or from the persona of the same name."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: invalid role name '{shown(role)}'")

    templates_dir = foil_root(toplevel) / "templates"
    template_path = templates_dir / f"{role}.toml"
    _refuse_symlink(template_path)
    if template_path.exists():
        raise FoilError(f"foil: template '{role}' already exists")

    if from_file:
        source = Path(from_file)
        if not source.is_file():
            raise FoilError(f"foil: file not found: {shown(from_file)}")
        content = source.read_text(encoding="utf-8")
        if in_fleet and template_permission(content) != "auto":
            raise FoilError("foil: permission is outside the fleet only")
    else:
        personas_dir = templates_dir / "personas"
        persona_file = personas_dir / f"{role}.md"

        if not persona_file.is_file():
            raise FoilError(
                f"foil: persona file not found: personas/{role}.md. "
                "Create the persona file first, or use --from FILE"
            )

        content = render_template(
            [{"id": installed_harness(toplevel), "model": ""}],
            persona=f"personas/{role}.md",
            worktree=False,
            permission="auto",
        )

    created = False
    try:
        _create_template_file(template_path, content)
        created = True
        _accept(toplevel, role)
    except FoilError:
        if created and template_path.is_file() and not template_path.is_symlink():
            template_path.unlink()
        raise

    print(f"foil: created template '{role}'")


def update_template(
    toplevel: Path,
    role: str,
    field: str,
    value: str,
    *,
    in_fleet: bool = False,
) -> None:
    """Update one field in a template."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{shown(role)}'")

    if field not in _TEMPLATE_FIELDS:
        fields = ", ".join(sorted(_TEMPLATE_FIELDS))
        raise FoilError(f"foil: invalid field '{shown(field)}'. Valid fields: {fields}")

    template = load_template(toplevel, role)
    template_path = template["path"]
    _refuse_symlink(template_path)
    entries = [{"id": item["id"], "model": item["model"]} for item in template["harness"]]
    if field in {"harness", "model"} and len(entries) != 1:
        raise FoilError(
            f"foil: template '{role}' has more than one harness entry; "
            f"edit {shown(str(template_path))}"
        )

    # Refuse a value that could escape its TOML string before any byte is replaced.
    original = template_path.read_text(encoding="utf-8")
    previous_permission = template_permission(original)
    _formatted_value(field, value)
    persona = str(template.get("persona") or "")
    worktree = bool(template.get("worktree", False))
    permission = str(template.get("permission", "auto"))
    if field == "harness":
        entries[0]["id"] = value
    elif field == "model":
        entries[0]["model"] = value
    elif field == "persona":
        persona = value
    elif field == "worktree":
        worktree = value.lower() == "true"
    else:
        permission = value

    _replace_template_file(
        template_path,
        render_template(
            entries, persona=persona, worktree=worktree, permission=permission
        ),
    )

    # Validate by loading. An in-fleet caller must not change permission,
    # including by a value that parses as a second assignment.
    try:
        _accept(toplevel, role)
        written = template_path.read_text(encoding="utf-8")
        if in_fleet and template_permission(written) != previous_permission:
            raise FoilError("foil: permission is outside the fleet only")
    except FoilError:
        _replace_template_file(template_path, original)
        raise

    print(f"foil: updated template '{role}': {field} = {value}")


def remove_template(toplevel: Path, role: str) -> None:
    """Delete a template (fails for protected roles)."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{shown(role)}'")

    if role in _PROTECTED_ROLES:
        protected = ", ".join(sorted(_PROTECTED_ROLES))
        raise FoilError(
            f"foil: cannot remove protected template '{role}'. Protected: {protected}"
        )

    # Load to validate it exists. The name was checked before this path.
    template = load_template(toplevel, role)
    _in_use(toplevel, role)
    template_path = template["path"]
    _refuse_symlink(template_path)

    template_path.unlink()
    _drop_cursor(toplevel, role)
    print(f"foil: removed template '{role}'")
