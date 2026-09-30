"""Roster management operations: list, show, add, update, remove templates."""

from __future__ import annotations

import json
from pathlib import Path

from foil.errors import FoilError
from foil.presets import installed_harness, load_template
from foil.project import foil_root
from foil.store import SAFE_ID

_PROTECTED_ROLES = {"lead", "implementer", "reviewer"}
_TEMPLATE_FIELDS = {"harness", "model", "persona", "worktree", "permission"}


def _shown(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


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


def add_template(toplevel: Path, role: str, *, from_file: str | None = None) -> None:
    """Create a template from a TOML file or from the persona of the same name."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: invalid role name '{_shown(role)}'")

    templates_dir = foil_root(toplevel) / "templates"
    template_path = templates_dir / f"{role}.toml"

    if template_path.exists():
        raise FoilError(f"foil: template '{role}' already exists")

    if from_file:
        source = Path(from_file)
        if not source.is_file():
            raise FoilError(f"foil: file not found: {from_file}")
        content = source.read_text(encoding="utf-8")
        template_path.write_text(content, encoding="utf-8")
        # Validate by loading
        try:
            load_template(toplevel, role)
        except FoilError as exc:
            template_path.unlink()
            raise exc
    else:
        personas_dir = templates_dir / "personas"
        persona_file = personas_dir / f"{role}.md"

        if not persona_file.is_file():
            raise FoilError(
                f"foil: persona file not found: personas/{role}.md\n"
                f"Create the persona file first, or use --from FILE"
            )

        # Create minimal template pointing to the persona
        content = f"""harness = "{installed_harness(toplevel)}"
persona = "personas/{role}.md"
worktree = false
permission = "ask"
"""
        template_path.write_text(content, encoding="utf-8")

    print(f"foil: created template '{role}'")


def update_template(toplevel: Path, role: str, field: str, value: str) -> None:
    """Update one field in a template."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{_shown(role)}'")

    if field not in _TEMPLATE_FIELDS:
        raise FoilError(
            f"foil: invalid field '{field}'\n"
            f"Valid fields: {', '.join(sorted(_TEMPLATE_FIELDS))}"
        )

    # Load existing template to validate it exists
    template = load_template(toplevel, role)
    template_path = template["path"]

    # Read current content
    lines = template_path.read_text(encoding="utf-8").splitlines(keepends=True)

    # Parse and update
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{field} ="):
            # Format the new value based on field type
            if field == "worktree":
                if value.lower() not in {"true", "false"}:
                    raise FoilError("foil: worktree must be 'true' or 'false'")
                formatted = value.lower()
            elif field == "permission":
                if value not in {"ask", "auto"}:
                    raise FoilError("foil: permission must be 'ask' or 'auto'")
                formatted = f'"{value}"'
            else:
                formatted = f'"{value}"'
            new_lines.append(f"{field} = {formatted}\n")
            updated = True
        else:
            new_lines.append(line)

    # If field wasn't found, append it
    if not updated:
        if field == "worktree":
            if value.lower() not in {"true", "false"}:
                raise FoilError("foil: worktree must be 'true' or 'false'")
            formatted = value.lower()
        elif field == "permission":
            if value not in {"ask", "auto"}:
                raise FoilError("foil: permission must be 'ask' or 'auto'")
            formatted = f'"{value}"'
        else:
            formatted = f'"{value}"'
        new_lines.append(f"{field} = {formatted}\n")

    # Write back
    template_path.write_text("".join(new_lines), encoding="utf-8")

    # Validate by loading
    try:
        load_template(toplevel, role)
    except FoilError as exc:
        # Restore original
        template_path.write_text("".join(lines), encoding="utf-8")
        raise exc

    print(f"foil: updated template '{role}': {field} = {value}")


def remove_template(toplevel: Path, role: str) -> None:
    """Delete a template (fails for protected roles)."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{_shown(role)}'")

    if role in _PROTECTED_ROLES:
        raise FoilError(
            f"foil: cannot remove protected template '{role}'\n"
            f"Protected: {', '.join(sorted(_PROTECTED_ROLES))}"
        )

    # Load to validate it exists
    template = load_template(toplevel, role)
    template_path = template["path"]

    template_path.unlink()
    print(f"foil: removed template '{role}'")
