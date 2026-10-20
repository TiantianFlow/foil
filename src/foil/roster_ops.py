"""Roster management operations: list, show, add, update, remove templates."""

from __future__ import annotations

import json
from pathlib import Path

from foil.errors import FoilError, shown
from foil.presets import (
    description_of,
    entry_label,
    fleet_list,
    installed_harness,
    load_preset,
    load_template,
    parse_template_text,
    path_description,
    persona_stems,
    persona_text,
    preset_source,
    read_description,
    read_maps,
    read_roles,
    remember_entries,
    save_maps,
    save_roles,
    template_permission,
)
from foil.project import foil_root
from foil.store import SAFE_ID, load_registry, write_bytes

_PROTECTED_ROLES = {"lead", "implementer", "reviewer"}
_TEMPLATE_FIELDS = {"harness", "model", "persona", "worktree", "permission"}


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")


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


def _entries(value: str, current: list[dict[str, str]]) -> list[dict[str, str]]:
    """A ``harness`` value: ``ID[/MODEL][,ID[/MODEL]...]``, the form ``roster list`` prints.

    A single bare id on a one-entry list keeps that entry's model.
    """

    _formatted_value("harness", value)
    if "," not in value and "/" not in value and len(current) == 1:
        return [{"id": value, "model": current[0]["model"]}]
    entries = []
    for label in value.split(","):
        harness_id, _sep, model = label.partition("/")
        if not SAFE_ID.fullmatch(harness_id) or model.startswith("-"):
            raise FoilError(f"foil: invalid harness entry '{shown(label)}'")
        entries.append({"id": harness_id, "model": model})
    return entries


def _new_entries(
    role: str, field: str, value: str, current: list[dict[str, str]]
) -> list[dict[str, str]]:
    """The list a ``harness`` or ``model`` update leaves. One model cannot edit a list."""

    if field == "harness":
        return _entries(value, current)
    if len(current) != 1:
        raise FoilError(
            f"foil: template '{role}' has more than one harness entry; "
            "set the whole list with harness=ID/MODEL,ID/MODEL"
        )
    return [{"id": current[0]["id"], "model": value}]


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


def _in_use(toplevel: Path, role: str) -> None:
    registry = load_registry(toplevel)
    for seat in registry["seats"].values():
        if seat["template"] == role and seat["state"] != "killed":
            raise FoilError(f"foil: template '{role}' is still in use")


def _template_names(toplevel: Path) -> list[str]:
    """List existing template names."""
    return sorted(role["id"] for role in (read_roles(toplevel) or []))


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


def roster_rows(toplevel: Path) -> tuple[list[tuple[str, dict | None]], list[dict[str, str]]]:
    """Each role with its listed row (None when invalid), then personas with no role."""
    names = _template_names(toplevel)
    persona_dir = foil_root(toplevel) / "templates" / "personas"
    available = [
        {"name": item, "description": path_description(persona_dir / f"{item}.md")}
        for item in persona_stems(persona_dir)
        if item not in names
    ]
    return [(name, _listed(toplevel, name)) for name in names], available


def list_roster(toplevel: Path, *, as_json: bool = False) -> None:
    """List all templates and available personas."""
    loaded, available = roster_rows(toplevel)
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


def _restore(path: Path, payload: bytes | None) -> None:
    if payload is None:
        if path.is_file() and not path.is_symlink():
            path.unlink()
        return
    write_bytes(path, payload)


def _add_listed(
    toplevel: Path,
    role: str,
    *,
    from_file: str | None,
    in_fleet: bool,
) -> None:
    root = foil_root(toplevel)
    roles_path = root / "roles.toml"
    maps_path = root / "mapping.toml"
    _refuse_symlink(roles_path)
    if maps_path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if from_file:
        source = Path(from_file)
        if not source.is_file():
            raise FoilError(f"foil: file not found: {shown(from_file)}")
        content = source.read_text(encoding="utf-8")
        if in_fleet and template_permission(content) != "auto":
            raise FoilError("foil: permission is outside the fleet only")
        parsed = parse_template_text(content)
    else:
        persona_file = root / "templates" / "personas" / f"{role}.md"
        if not persona_file.is_file():
            raise FoilError(
                f"foil: persona file not found: personas/{role}.md. "
                "Create the persona file first, or use --from FILE"
            )
        parsed = {
            "persona": f"personas/{role}.md",
            "worktree": False,
            "permission": "auto",
            "harness": [{"id": installed_harness(toplevel), "model": ""}],
        }
    persona_path = root / "templates" / f"{role}.toml"
    try:
        description = description_of(
            persona_text({"persona": parsed["persona"], "path": persona_path})
        )
    except FoilError:
        description = ""
    roles = list(read_roles(toplevel) or [])
    roles.append(
        {
            "id": role,
            "description": description,
            "persona": parsed["persona"],
            "worktree": parsed["worktree"],
            "permission": parsed["permission"],
        }
    )
    maps = list(read_maps(toplevel))
    for entry in parsed["harness"]:
        maps.append({"role": role, "id": entry["id"], "model": entry["model"]})
    original_roles = roles_path.read_bytes() if roles_path.is_file() else None
    original_maps = maps_path.read_bytes() if maps_path.is_file() else None
    save_roles(toplevel, roles)
    save_maps(toplevel, maps)
    try:
        _accept(toplevel, role)
    except FoilError:
        _restore(roles_path, original_roles)
        _restore(maps_path, original_maps)
        raise
    print(f"foil: created template '{role}'")


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

    listed = read_roles(toplevel)
    if listed is not None and any(item["id"] == role for item in listed):
        raise FoilError(f"foil: template '{role}' already exists")
    _add_listed(toplevel, role, from_file=from_file, in_fleet=in_fleet)


def _update_listed(
    toplevel: Path,
    role: str,
    field: str,
    value: str,
    *,
    in_fleet: bool,
    template: dict,
) -> None:
    root = foil_root(toplevel)
    roles_path = root / "roles.toml"
    maps_path = root / "mapping.toml"
    _refuse_symlink(roles_path)
    if maps_path.is_symlink():
        raise FoilError("foil: refusing symlink")
    roles = list(read_roles(toplevel) or [])
    maps = list(read_maps(toplevel))
    current = [item for item in maps if item["role"] == role]
    _formatted_value(field, value)
    original_roles = roles_path.read_bytes()
    original_maps = maps_path.read_bytes() if maps_path.is_file() else None
    previous = str(template.get("permission", "auto"))
    row = next(item for item in roles if item["id"] == role)
    if field in {"harness", "model"}:
        entries = _new_entries(role, field, value, current)
        place = maps.index(current[0]) if current else len(maps)
        rest = [item for item in maps if item["role"] != role]
        maps = rest[:place] + [{"role": role, **entry} for entry in entries] + rest[place:]
    elif field == "persona":
        row["persona"] = value
        try:
            row["description"] = description_of(
                persona_text({"persona": value, "path": root / "templates" / f"{role}.toml"})
            )
        except FoilError:
            row["description"] = ""
    elif field == "worktree":
        row["worktree"] = value.lower() == "true"
    else:
        row["permission"] = value
    save_roles(toplevel, roles)
    save_maps(toplevel, maps)
    try:
        _accept(toplevel, role)
        if in_fleet and str(load_template(toplevel, role)["permission"]) != previous:
            raise FoilError("foil: permission is outside the fleet only")
    except FoilError:
        _restore(roles_path, original_roles)
        _restore(maps_path, original_maps)
        raise
    print(f"foil: updated template '{role}': {field} = {value}")


def _remember(toplevel: Path, role: str, field: str, value: str) -> None:
    """An in-fleet harness or model edit: this fleet's overrides, not the mapping."""

    template = load_template(toplevel, role)
    current = fleet_list(toplevel, role) or template["harness"]
    _formatted_value(field, value)
    entries = _new_entries(role, field, value, current)
    for entry in entries:
        load_preset(toplevel, entry["id"])
    remember_entries(toplevel, role, entries)
    shown_value = value if field == "model" else ",".join(entry_label(e) for e in entries)
    print(f"foil: fleet memory: {role} {field} = {shown_value}")


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

    if field in {"harness", "model"} and in_fleet:
        _remember(toplevel, role, field, value)
        return

    template = load_template(toplevel, role)
    _update_listed(toplevel, role, field, value, in_fleet=in_fleet, template=template)


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
    if template_path.is_file():
        template_path.unlink()
    roles = read_roles(toplevel)
    if roles is not None and any(item["id"] == role for item in roles):
        save_roles(toplevel, [item for item in roles if item["id"] != role])
        save_maps(toplevel, [item for item in read_maps(toplevel) if item["role"] != role])
    print(f"foil: removed template '{role}'")
