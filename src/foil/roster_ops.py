"""Roster management operations: list, show, add, update, remove templates."""

from __future__ import annotations

import json
from pathlib import Path

from foil.errors import FoilError
from foil.presets import installed_harness, load_preset, load_template, template_permission
from foil.project import foil_root
from foil.store import SAFE_ID, create_exclusive, load_registry, write_bytes

_PROTECTED_ROLES = {"lead", "implementer", "reviewer"}
_TEMPLATE_FIELDS = {"harness", "model", "persona", "worktree", "permission"}


def _shown(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


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
        raise FoilError(f"foil: persona path must stay inside .foil/templates: {persona}")


def _accept(toplevel: Path, role: str) -> None:
    template = load_template(toplevel, role)
    _persona_inside(str(template.get("persona") or ""))
    load_preset(toplevel, str(template["harness"]))


def _in_use(toplevel: Path, role: str) -> None:
    registry = load_registry(toplevel)
    for seat in registry["seats"].values():
        if seat["template"] == role and seat["state"] != "killed":
            raise FoilError(f"foil: template '{role}' is still in use")


def _packaged_persona_names(toplevel: Path) -> list[str]:
    """List available packaged personas from templates/personas directory."""
    personas_dir = foil_root(toplevel) / "templates" / "personas"
    if not personas_dir.is_dir():
        return []
    return sorted(item.name[:-3] for item in personas_dir.glob("*.md"))


def _template_names(toplevel: Path) -> list[str]:
    """List existing template names."""
    templates_dir = foil_root(toplevel) / "templates"
    if not templates_dir.is_dir():
        return []
    return sorted(path.stem for path in templates_dir.glob("*.toml"))


def list_roster(toplevel: Path, *, as_json: bool = False) -> None:
    """List all templates and available personas."""
    templates = _template_names(toplevel)
    personas = _packaged_persona_names(toplevel)

    # Personas that don't have templates
    available_personas = [p for p in personas if p not in templates]

    if as_json:
        template_data = []
        for name in templates:
            try:
                template = load_template(toplevel, name)
                template_data.append({
                    "role": name,
                    "harness": template["harness"],
                    "model": template.get("model", ""),
                    "worktree": template.get("worktree", False),
                    "permission": template.get("permission", "ask"),
                })
            except FoilError:
                template_data.append({"role": name, "error": "invalid template"})

        output = {
            "templates": template_data,
            "personas": available_personas,
        }
        print(json.dumps(output, indent=2))
    else:
        for name in templates:
            try:
                template = load_template(toplevel, name)
                model = f"/{template['model']}" if template.get("model") else ""
                print(f"{name} [{template['harness']}{model}]")
            except FoilError:
                print(f"{name} [invalid]")

        for name in available_personas:
            print(f"+ {name}")


def show_template(toplevel: Path, role: str, *, as_json: bool = False) -> None:
    """Show one template's configuration."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{_shown(role)}'")

    template = load_template(toplevel, role)

    if as_json:
        output = {
            "role": role,
            "harness": template["harness"],
            "model": template.get("model", ""),
            "persona": template.get("persona", ""),
            "worktree": template.get("worktree", False),
            "permission": template.get("permission", "ask"),
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"role: {role}")
        print(f"harness: {template['harness']}")
        if template.get("model"):
            print(f"model: {template['model']}")
        if template.get("persona"):
            print(f"persona: {template['persona']}")
        print(f"worktree: {template.get('worktree', False)}")
        print(f"permission: {template.get('permission', 'ask')}")


def add_template(
    toplevel: Path,
    role: str,
    *,
    from_file: str | None = None,
    in_fleet: bool = False,
) -> None:
    """Create a template from a TOML file or from the persona of the same name."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: invalid role name '{_shown(role)}'")

    templates_dir = foil_root(toplevel) / "templates"
    template_path = templates_dir / f"{role}.toml"
    _refuse_symlink(template_path)
    if template_path.exists():
        raise FoilError(f"foil: template '{role}' already exists")

    if from_file:
        source = Path(from_file)
        if not source.is_file():
            raise FoilError(f"foil: file not found: {_shown(from_file)}")
        content = source.read_text(encoding="utf-8")
        if in_fleet and template_permission(content) != "ask":
            raise FoilError("foil: permission is outside the fleet only")
    else:
        personas_dir = templates_dir / "personas"
        persona_file = personas_dir / f"{role}.md"

        if not persona_file.is_file():
            raise FoilError(
                f"foil: persona file not found: personas/{role}.md. "
                "Create the persona file first, or use --from FILE"
            )

        content = (
            f'harness = "{installed_harness(toplevel)}"\n'
            f'persona = "personas/{role}.md"\n'
            "worktree = false\n"
            'permission = "ask"\n'
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
        raise FoilError(f"foil: unknown template '{_shown(role)}'")

    if field not in _TEMPLATE_FIELDS:
        fields = ", ".join(sorted(_TEMPLATE_FIELDS))
        raise FoilError(f"foil: invalid field '{_shown(field)}'. Valid fields: {fields}")

    # Load existing template to validate it exists
    template = load_template(toplevel, role)
    template_path = template["path"]
    _refuse_symlink(template_path)
    if field == "harness":
        _in_use(toplevel, role)

    # Read current content. Refuse a value that could escape its TOML string
    # before any byte is replaced.
    original = template_path.read_text(encoding="utf-8")
    previous_permission = template_permission(original)
    formatted = _formatted_value(field, value)
    lines = original.splitlines(keepends=True)

    # Parse and update
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{field} ="):
            new_lines.append(f"{field} = {formatted}\n")
            updated = True
        else:
            new_lines.append(line)

    # If field wasn't found, append it
    if not updated:
        new_lines.append(f"{field} = {formatted}\n")

    # Write back atomically. A symlink is refused before this write.
    _replace_template_file(template_path, "".join(new_lines))

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
        raise FoilError(f"foil: unknown template '{_shown(role)}'")

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
    print(f"foil: removed template '{role}'")
