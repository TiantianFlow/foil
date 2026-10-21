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
    template_permission,
)
from foil.project import foil_root, store_root
from foil.store import SAFE_ID, write_bytes

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
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative.parts != ("personas", relative.name)
    ):
        raise FoilError(f"foil: persona path must stay inside personas: {persona}")


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


def _accept(toplevel: Path, role: str, root: Path, *, mapping_only: bool) -> None:
    """Check the layer this write just stored.

    A project harness or model write is a mapping list and no role row.
    A project persona, worktree, or permission write is a role row, and the
    mapping can stay on the global layer.
    """
    roles = _roles_at(root) or []
    match = next((item for item in roles if item["id"] == role), None)
    entries = [item for item in _maps_at(root) if item["role"] == role]
    if mapping_only:
        if not entries:
            raise FoilError("foil: invalid template")
    else:
        if match is None:
            raise FoilError(f"foil: unknown template '{role}'")
        persona = str(match.get("persona") or "")
        _persona_inside(persona)
        if root == store_root() and not entries:
            raise FoilError("foil: invalid template")
        if persona.endswith(".md") and "\n" not in persona and "\r" not in persona:
            persona_text(_persona_template(toplevel, role, persona))
    for entry in entries:
        load_preset(toplevel, str(entry["id"]))


def _persona_template(toplevel: Path, role: str, persona: str) -> dict:
    """A template dict whose path makes the persona lookup use this project."""
    try:
        root = foil_root(toplevel)
    except FoilError:
        root = store_root()
    return {"name": role, "path": root / "templates" / f"{role}.toml", "persona": persona}


def _seat_uses(path: Path, role: str) -> bool:
    """True when this registry file has a live seat of the role. A link is skipped."""
    if path.is_symlink() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    seats = payload.get("seats") if isinstance(payload, dict) else None
    if not isinstance(seats, dict):
        return False
    for seat in seats.values():
        if (
            isinstance(seat, dict)
            and seat.get("template") == role
            and seat.get("state") != "killed"
        ):
            return True
    return False


def _in_use(toplevel: Path, role: str) -> None:
    """Fail when any fleet registry in the store has a live seat of this role."""
    del toplevel
    projects = store_root() / "projects"
    if projects.is_symlink() or not projects.is_dir():
        return
    for project in projects.iterdir():
        if project.is_symlink() or not project.is_dir():
            continue
        fleets = project / "fleets"
        if fleets.is_symlink() or not fleets.is_dir():
            continue
        for fleet in fleets.iterdir():
            if (
                fleet.is_symlink()
                or not fleet.is_dir()
                or SAFE_ID.fullmatch(fleet.name) is None
            ):
                continue
            if _seat_uses(fleet / "run" / "registry.json", role):
                raise FoilError(f"foil: template '{role}' is still in use")


def _checked_out(toplevel: Path) -> bool:
    try:
        foil_root(toplevel)
    except FoilError:
        return False
    return True


def _visible_roles(toplevel: Path) -> list[dict] | None:
    """Effective roles. Outside a checkout this is the global layer only."""
    if not _checked_out(toplevel):
        return _roles_at(store_root())
    return read_roles(toplevel)


def _visible_maps(toplevel: Path) -> list[dict[str, str]]:
    if not _checked_out(toplevel):
        return _maps_at(store_root())
    return read_maps(toplevel)


def _role_layer(toplevel: Path, role: str) -> str:
    if _checked_out(toplevel):
        text = _text_at(foil_root(toplevel) / "roles.toml")
        if text is not None and any(item["id"] == role for item in _parse_role_text(text)):
            return "project"
    if any(item["id"] == role for item in (_roles_at(store_root()) or [])):
        return "global"
    return "global"


def _mapping_layer(toplevel: Path, role: str) -> str:
    if _checked_out(toplevel):
        from foil.presets import fleet_overrides

        if any(item["role"] == role for item in fleet_overrides(toplevel)):
            return "fleet"
        project = _maps_at(foil_root(toplevel))
        if any(item["role"] == role for item in project):
            return "project"
    return "global"


def _parse_role_text(text: str) -> list[dict]:
    from foil.presets import _parse_roles

    return _parse_roles(text)


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
        "layer": _role_layer(toplevel, name),
        "mapping": _mapping_layer(toplevel, name),
        "description": description,
    }


def _persona_dirs(toplevel: Path) -> list[Path]:
    """Project, then global, then the package. A higher file hides a lower one."""
    from importlib.resources import files

    found = []
    if _checked_out(toplevel):
        found.append(foil_root(toplevel) / "personas")
    found.append(store_root() / "personas")
    packaged = files("foil").joinpath("defaults", "personas")
    if packaged.is_dir():
        found.append(Path(str(packaged)))
    return found


def roster_rows(toplevel: Path) -> tuple[list[tuple[str, dict | None]], list[dict[str, str]]]:
    """Each role with its listed row (None when invalid), then personas with no role."""
    names = set(_template_names(toplevel))
    seen: set[str] = set()
    available = []
    for directory in _persona_dirs(toplevel):
        for item in persona_stems(directory):
            if item in names or item in seen:
                continue
            seen.add(item)
            available.append(
                {"name": item, "description": path_description(directory / f"{item}.md")}
            )
    available.sort(key=lambda item: item["name"])
    return [(name, _listed(toplevel, name)) for name in sorted(names)], available


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
        print(f"\tlayer\t{row['layer']}")
        print(f"\tmapping\t{row['mapping']}")
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
            "layer": _role_layer(toplevel, role),
            "mapping": _mapping_layer(toplevel, role),
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
        print(f"layer: {_role_layer(toplevel, role)}")
        print(f"mapping: {_mapping_layer(toplevel, role)}")
        print(f"description: {description}")


def _restore(path: Path, payload: bytes | None) -> None:
    if payload is None:
        if path.is_file() and not path.is_symlink():
            path.unlink()
        return
    write_bytes(path, payload)


def _persona_body(toplevel: Path, persona: str) -> str:
    """The persona body the lookup can read, so the description stays one line."""
    if not persona or "\n" in persona or "\r" in persona or not persona.endswith(".md"):
        return persona
    _persona_inside(persona)
    try:
        return persona_text(_persona_template(toplevel, "", persona))
    except FoilError as exc:
        if "persona file not found" in str(exc):
            return ""
        raise


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
        _accept(toplevel, role, root, mapping_only=False)
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
    mapping_only = project and field in {"harness", "model"} and row is None
    if not mapping_only:
        _save_roles_at(root, roles)
    if field in {"harness", "model"} or maps_path.is_file():
        _save_maps_at(root, maps)
    try:
        _accept(toplevel, role, root, mapping_only=mapping_only)
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
