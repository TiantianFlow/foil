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
    preset_source,
    read_description,
    read_maps,
    read_roles,
    remember_entries,
    template_permission,
)
from foil.project import foil_root, store_root
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


def _layer(toplevel: Path, *, project: bool) -> Path:
    """The global settings, or the project override when ``--project`` is set."""
    if project:
        return foil_root(toplevel)
    return store_root()


def _roles_at(root: Path) -> list[dict] | None:
    from foil.presets import _parse_roles

    text = _text_at(root / "roles.toml")
    if text is None:
        return None
    return _parse_roles(text)


def _maps_at(root: Path) -> list[dict[str, str]]:
    from foil.presets import _parse_maps

    text = _text_at(root / "mapping.toml")
    if text is None:
        return []
    return _parse_maps(text)


def _text_at(path: Path) -> str | None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FoilError("foil: invalid template") from exc


def _save_roles_at(root: Path, roles: list[dict]) -> None:
    from foil.presets import render_roles

    path = root / "roles.toml"
    _refuse_symlink(path)
    root.mkdir(parents=True, exist_ok=True)
    write_bytes(path, render_roles(roles).encode())


def _save_maps_at(root: Path, maps: list[dict[str, str]]) -> None:
    from foil.presets import render_maps

    path = root / "mapping.toml"
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    root.mkdir(parents=True, exist_ok=True)
    write_bytes(path, render_maps(maps).encode())


def _accept(toplevel: Path, role: str, root: Path) -> None:
    """Check the role this write just stored. The loader still reads the project."""
    roles = _roles_at(root) or []
    match = next((item for item in roles if item["id"] == role), None)
    if match is None:
        raise FoilError(f"foil: unknown template '{role}'")
    entries = [item for item in _maps_at(root) if item["role"] == role]
    if not entries:
        raise FoilError("foil: invalid template")
    _persona_inside(str(match.get("persona") or ""))
    persona = str(match.get("persona") or "")
    if persona.endswith(".md") and "\n" not in persona and "\r" not in persona:
        relative = Path(persona)
        if not relative.is_absolute() and ".." not in relative.parts:
            base = foil_root(toplevel) / "templates"
            path = base / relative
            if path.is_symlink() or not path.is_file():
                raise FoilError(f"foil: persona file not found: {persona}")
    for entry in entries:
        load_preset(toplevel, str(entry["id"]))


def _in_use(toplevel: Path, role: str) -> None:
    registry = load_registry(toplevel)
    for seat in registry["seats"].values():
        if seat["template"] == role and seat["state"] != "killed":
            raise FoilError(f"foil: template '{role}' is still in use")


def _visible_roles(toplevel: Path) -> list[dict] | None:
    """Project roles when that file exists, otherwise the global roles.

    The loader still reads only the project. List and show fall through so a
    checkout with no override still shows the settings.
    """
    listed = read_roles(toplevel)
    if listed is not None:
        return listed
    return _roles_at(store_root())


def _visible_maps(toplevel: Path) -> list[dict[str, str]]:
    if (foil_root(toplevel) / "mapping.toml").is_file():
        return read_maps(toplevel)
    return _maps_at(store_root())


def _template_names(toplevel: Path) -> list[str]:
    """List existing template names."""
    return sorted(role["id"] for role in (_visible_roles(toplevel) or []))


def _listed(toplevel: Path, name: str) -> dict | None:
    roles = _visible_roles(toplevel) or []
    match = next((role for role in roles if role["id"] == name), None)
    if match is None:
        return None
    entries = [
        {"id": item["id"], "model": item["model"]}
        for item in _visible_maps(toplevel)
        if item["role"] == name
    ]
    if not entries:
        return None
    template = {
        "name": name,
        "path": foil_root(toplevel) / "templates" / f"{name}.toml",
        "harness": entries,
        "persona": match["persona"],
        "worktree": match["worktree"],
        "permission": match["permission"],
        "description": match["description"],
    }
    try:
        description = read_description(template)
    except FoilError:
        return None
    entries = template["harness"]
    return {
        "role": name,
        "harness": [{"id": entry["id"], "model": entry["model"]} for entry in entries],
        "worktree": template.get("worktree", False),
        "permission": template.get("permission", "auto"),
        "preset": [preset_source(toplevel, str(entry["id"])) for entry in entries],
        "description": description,
    }


def roster_rows(toplevel: Path) -> tuple[list[tuple[str, dict | None]], list[dict[str, str]]]:
    """Each role with its listed row (None when invalid), then personas with no role."""
    names = _template_names(toplevel)
    persona_dir = store_root() / "personas"
    if not persona_dir.is_dir():
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

    roles = _visible_roles(toplevel) or []
    match = next((item for item in roles if item["id"] == role), None)
    entries = [
        {"id": item["id"], "model": item["model"]}
        for item in _visible_maps(toplevel)
        if item["role"] == role
    ]
    if match is None or not entries:
        raise FoilError(f"foil: unknown template '{role}'")
    template = {
        "name": role,
        "path": foil_root(toplevel) / "templates" / f"{role}.toml",
        "harness": entries,
        "persona": match["persona"],
        "worktree": match["worktree"],
        "permission": match["permission"],
        "description": match["description"],
    }
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


def _persona_body(toplevel: Path, persona: str) -> str:
    """The persona body the current lookup can read, so the description stays one line."""
    if not persona or "\n" in persona or "\r" in persona or not persona.endswith(".md"):
        return persona
    relative = Path(persona)
    if relative.is_absolute() or ".." in relative.parts:
        raise FoilError(f"foil: persona path must stay inside personas: {persona}")
    path = foil_root(toplevel) / "templates" / relative
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _add_listed(
    toplevel: Path,
    role: str,
    *,
    from_file: str | None,
    in_fleet: bool,
    project: bool,
) -> None:
    root = _layer(toplevel, project=project)
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
        persona = f"personas/{role}.md"
        if not _persona_body(toplevel, persona):
            raise FoilError(
                f"foil: persona file not found: personas/{role}.md. "
                "Create the persona file first, or use --from FILE"
            )
        parsed = {
            "persona": persona,
            "worktree": False,
            "permission": "auto",
            "harness": [{"id": installed_harness(toplevel), "model": ""}],
        }
    try:
        description = description_of(_persona_body(toplevel, str(parsed["persona"])))
    except FoilError:
        description = ""
    roles = list(_roles_at(root) or [])
    roles.append(
        {
            "id": role,
            "description": description,
            "persona": parsed["persona"],
            "worktree": parsed["worktree"],
            "permission": parsed["permission"],
        }
    )
    maps = list(_maps_at(root))
    for entry in parsed["harness"]:
        maps.append({"role": role, "id": entry["id"], "model": entry["model"]})
    original_roles = roles_path.read_bytes() if roles_path.is_file() else None
    original_maps = maps_path.read_bytes() if maps_path.is_file() else None
    _save_roles_at(root, roles)
    _save_maps_at(root, maps)
    try:
        _accept(toplevel, role, root)
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
    project: bool = False,
) -> None:
    """Create a template from a TOML file or from the persona of the same name."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: invalid role name '{shown(role)}'")

    listed = _roles_at(_layer(toplevel, project=project))
    if listed is not None and any(item["id"] == role for item in listed):
        raise FoilError(f"foil: template '{role}' already exists")
    _add_listed(toplevel, role, from_file=from_file, in_fleet=in_fleet, project=project)


def _effective_role(toplevel: Path, role: str) -> dict:
    """The role row a first ``--project`` field write copies from."""
    listed = _roles_at(store_root())
    match = next((item for item in (listed or []) if item["id"] == role), None)
    if match is not None:
        return match
    return load_template(toplevel, role)


def _effective_maps(toplevel: Path, role: str) -> list[dict[str, str]]:
    rows = [item for item in _maps_at(store_root()) if item["role"] == role]
    if rows:
        return rows
    template = load_template(toplevel, role)
    return [
        {"role": role, "id": entry["id"], "model": entry["model"]}
        for entry in template["harness"]
    ]


def _update_listed(
    toplevel: Path,
    role: str,
    field: str,
    value: str,
    *,
    in_fleet: bool,
    project: bool,
) -> None:
    root = _layer(toplevel, project=project)
    roles_path = root / "roles.toml"
    maps_path = root / "mapping.toml"
    _refuse_symlink(roles_path)
    if maps_path.is_symlink():
        raise FoilError("foil: refusing symlink")
    roles = list(_roles_at(root) or [])
    maps = list(_maps_at(root))
    _formatted_value(field, value)
    original_roles = roles_path.read_bytes() if roles_path.is_file() else None
    original_maps = maps_path.read_bytes() if maps_path.is_file() else None
    row = next((item for item in roles if item["id"] == role), None)
    if row is None and project and field in {"persona", "worktree", "permission"}:
        source = _effective_role(toplevel, role)
        row = {
            "id": role,
            "description": source.get("description", ""),
            "persona": source.get("persona", ""),
            "worktree": source.get("worktree", False),
            "permission": source.get("permission", "auto"),
        }
        roles.append(row)
    if row is None and not project:
        raise FoilError(f"foil: unknown template '{role}'")
    current = [item for item in maps if item["role"] == role]
    if not current and project and field in {"harness", "model"}:
        current = _effective_maps(toplevel, role)
    if field in {"harness", "model"}:
        if not current:
            raise FoilError(f"foil: unknown template '{role}'")
        entries = _new_entries(role, field, value, current)
        place = maps.index(current[0]) if current[0] in maps else len(maps)
        rest = [item for item in maps if item["role"] != role]
        maps = rest[:place] + [{"role": role, **entry} for entry in entries] + rest[place:]
    elif field == "persona":
        if row is None:
            raise FoilError(f"foil: unknown template '{role}'")
        row["persona"] = value
        try:
            row["description"] = description_of(_persona_body(toplevel, value))
        except FoilError:
            row["description"] = ""
    elif field == "worktree":
        if row is None:
            raise FoilError(f"foil: unknown template '{role}'")
        row["worktree"] = value.lower() == "true"
    else:
        if row is None:
            raise FoilError(f"foil: unknown template '{role}'")
        previous = str(row.get("permission", "auto"))
        row["permission"] = value
        if in_fleet and value != previous:
            raise FoilError("foil: permission is outside the fleet only")
    # A first project harness or model write is a mapping list and no role row.
    if field not in {"harness", "model"} or row is not None or not project:
        _save_roles_at(root, roles)
    _save_maps_at(root, maps)
    try:
        _accept(toplevel, role, root)
    except FoilError:
        _restore(roles_path, original_roles)
        _restore(maps_path, original_maps)
        raise
    print(f"foil: updated template '{role}': {field} = {value}")


def _remember(toplevel: Path, role: str, field: str, value: str) -> None:
    """An in-fleet harness or model edit: this fleet's overrides, not the mapping."""

    project_roles = foil_root(toplevel) / "roles.toml"
    template = load_template(toplevel, role) if project_roles.is_file() else None
    if template is None:
        match = next(
            (item for item in (_roles_at(store_root()) or []) if item["id"] == role),
            None,
        )
        entries = [
            {"id": item["id"], "model": item["model"]}
            for item in _maps_at(store_root())
            if item["role"] == role
        ]
        if match is None or not entries:
            raise FoilError(f"foil: unknown template '{role}'")
        template = {"harness": entries, "permission": match.get("permission", "auto")}
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
    project: bool = False,
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

    _update_listed(toplevel, role, field, value, in_fleet=in_fleet, project=project)


def remove_template(toplevel: Path, role: str, *, project: bool = False) -> None:
    """Delete a template. ``--project`` drops the project rows and leaves the global role."""
    if not SAFE_ID.fullmatch(role):
        raise FoilError(f"foil: unknown template '{shown(role)}'")

    root = _layer(toplevel, project=project)
    roles = _roles_at(root)
    maps = _maps_at(root)
    has_role = roles is not None and any(item["id"] == role for item in roles)
    has_map = any(item["role"] == role for item in maps)
    if project:
        if not has_role and not has_map:
            raise FoilError(f"foil: unknown template '{role}'")
        if has_role:
            _save_roles_at(root, [item for item in (roles or []) if item["id"] != role])
        if has_map:
            _save_maps_at(root, [item for item in maps if item["role"] != role])
        print(f"foil: removed template '{role}'")
        return

    if role in _PROTECTED_ROLES:
        protected = ", ".join(sorted(_PROTECTED_ROLES))
        raise FoilError(
            f"foil: cannot remove protected template '{role}'. Protected: {protected}"
        )
    if not has_role:
        raise FoilError(f"foil: unknown template '{role}'")
    _in_use(toplevel, role)
    _save_roles_at(root, [item for item in (roles or []) if item["id"] != role])
    _save_maps_at(root, [item for item in maps if item["role"] != role])
    print(f"foil: removed template '{role}'")
