"""Settings by screen. Rows are ``roster list``. A write is ``roster update``.

The page keeps nothing. A global or project list is applied as ``user``.
A fleet list is the in-fleet update, as ``lead``, because that is the
command that writes ``overrides.toml``.
"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path
from typing import Any

from foil.api import roster_update
from foil.errors import FoilError, shown
from foil.inbox import projects
from foil.presets import entry_label, path_description, persona_stems, preset_ids, preset_source
from foil.project import checkout_for_key, foil_root, store_root
from foil.roster_ops import roster_list_payload, roster_show_payload, write_revision
from foil.scope import bound
from foil.store import FLEET_ID, SAFE_ID, fleet_ids

_HEX = re.compile(r"[0-9a-f]{64}")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,200}")
_FIELDS = {"harness", "model", "persona", "worktree", "permission"}
_LAYERS = {"global", "project", "fleet"}
_WRITE_KEYS = {"project", "fleet", "role", "field", "value", "base", "layer", "before"}


class StaleRoster(FoilError):
    """A roster write whose base revision is no longer current."""

    def __init__(self, message: str, diff: dict[str, str]) -> None:
        super().__init__(message)
        self.diff = diff


def _invalid() -> FoilError:
    return FoilError("foil: settings change is invalid")


def _persona_files(toplevel: Path) -> list[dict[str, str]]:
    """Every persona file, highest layer first. A fleet has no persona file."""

    from importlib.resources import files

    layers: list[tuple[str, Path]] = []
    project_dir = foil_root(toplevel) / "personas"
    global_dir = store_root() / "personas"
    if project_dir != global_dir:
        layers.append(("project", project_dir))
    layers.append(("global", global_dir))
    packaged = files("foil").joinpath("defaults", "personas")
    if packaged.is_dir():
        layers.append(("packaged", Path(str(packaged))))
    found: dict[str, tuple[str, Path]] = {}
    for layer, directory in layers:
        for name in persona_stems(directory):
            if name not in found:
                found[name] = (layer, directory / f"{name}.md")
    rows = []
    for name in sorted(found):
        layer, path = found[name]
        rows.append({"name": name, "layer": layer, "description": path_description(path)})
    return rows


def _presets(toplevel: Path) -> list[dict[str, str]]:
    ids, _skipped = preset_ids(toplevel)
    return [{"id": harness_id, "layer": preset_source(toplevel, harness_id)} for harness_id in ids]


def _index() -> list[dict[str, Any]]:
    rows = []
    for key, checkout in projects():
        try:
            names = fleet_ids(checkout)
        except FoilError:
            names = []
        rows.append({"key": key, "fleets": names})
    return rows


def _scope(project: str, fleet: str) -> tuple[Path | None, bool]:
    if not isinstance(project, str) or not isinstance(fleet, str):
        raise _invalid()
    if project and not _PROJECT.fullmatch(project):
        raise FoilError(f"foil: unknown project '{shown(project)}'")
    if fleet and not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    if fleet and not project:
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    if not project:
        return None, False
    checkout = checkout_for_key(project)
    if fleet and fleet not in fleet_ids(checkout):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    return checkout, bool(fleet)


def view(project: str = "", fleet: str = "") -> dict[str, Any]:
    """The settings screen. ``templates`` and ``personas`` match ``roster list``."""

    checkout, inside = _scope(project, fleet)
    fleet_base = ""
    if inside and checkout is not None:
        with bound(checkout, fleet, "lead", inside=True):
            payload = roster_list_payload(checkout)
            fleet_base = write_revision(checkout, fleet_memory=True)
        toplevel, scope = checkout, "fleet"
    elif checkout is not None:
        payload = roster_list_payload(checkout)
        toplevel, scope = checkout, "project"
    else:
        toplevel = store_root()
        payload = roster_list_payload(toplevel)
        scope = "global"
    project_base = ""
    if checkout is not None:
        with contextlib.suppress(FoilError):
            project_base = write_revision(checkout, project=True)
    return {
        "scope": scope,
        "project": project,
        "fleet": fleet,
        "templates": payload["templates"],
        "personas": payload["personas"],
        "bases": {
            "global": write_revision(store_root()),
            "project": project_base,
            "fleet": fleet_base,
        },
        "persona_files": _persona_files(toplevel),
        "presets": _presets(toplevel),
        "projects": _index(),
    }


def field_text(row: dict[str, Any], field: str) -> str:
    """One field in the form ``roster list`` prints a harness list."""

    if row.get("error"):
        raise FoilError("foil: invalid template")
    if field == "harness":
        return ",".join(entry_label(entry) for entry in row["harness"])
    if field == "model":
        entries = row.get("harness") or []
        if len(entries) != 1:
            return ""
        return str(entries[0].get("model") or "")
    if field == "worktree":
        return "true" if row.get("worktree") else "false"
    if field in {"permission", "persona"}:
        return str(row.get(field) or "")
    raise FoilError(f"foil: invalid field '{shown(field)}'")


def current_value(
    role: str, field: str, *, layer: str, project: str = "", fleet: str = "main"
) -> str:
    """The effective value a write of this layer would replace."""

    if layer == "fleet":
        checkout = checkout_for_key(project)
        with bound(checkout, fleet, "lead", inside=True):
            row = roster_show_payload(checkout, role)
    elif layer == "project":
        row = roster_show_payload(checkout_for_key(project), role)
    else:
        row = roster_show_payload(store_root(), role)
    return field_text(row, field)


def reraise_stale(
    exc: FoilError,
    *,
    role: str,
    field: str,
    before: str,
    after: str,
    layer: str,
    project: str = "",
    fleet: str = "main",
) -> None:
    text = str(exc)
    if not text.startswith("foil: roster changed, current revision "):
        raise exc
    try:
        now = current_value(role, field, layer=layer, project=project, fleet=fleet)
    except FoilError:
        now = ""
    raise StaleRoster(
        text,
        {"role": role, "field": field, "before": before, "now": now, "after": after},
    ) from exc


def _strings(body: dict[str, Any]) -> dict[str, str] | None:
    if set(body) - _WRITE_KEYS:
        return None
    values: dict[str, str] = {}
    for key in ("role", "field", "value", "base", "layer"):
        item = body.get(key)
        if not isinstance(item, str):
            return None
        values[key] = item
    for key in ("project", "fleet", "before"):
        item = body.get(key, "")
        if not isinstance(item, str):
            return None
        values[key] = item
    return values


def apply(body: dict[str, Any]) -> dict[str, bool]:
    """One screen write. The revision is the one the page showed."""

    values = _strings(body)
    if values is None:
        raise _invalid()
    project, fleet = values["project"], values["fleet"]
    role, field, value = values["role"], values["field"], values["value"]
    base, layer, before = values["base"], values["layer"], values["before"]
    if (
        field not in _FIELDS
        or layer not in _LAYERS
        or not SAFE_ID.fullmatch(role)
        or not _HEX.fullmatch(base)
        or not value
        or len(value) > 400
        or len(before) > 400
        or any(ord(char) < 32 or ord(char) == 127 for char in value + before)
    ):
        raise _invalid()
    if layer == "fleet":
        if not project or not fleet:
            raise _invalid()
    elif fleet or (layer == "project" and not project):
        raise _invalid()
    if project and not _PROJECT.fullmatch(project):
        raise FoilError(f"foil: unknown project '{shown(project)}'")
    if fleet and not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    checkout = checkout_for_key(project) if project else None
    if layer == "fleet" and checkout is not None and fleet not in fleet_ids(checkout):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    change = f"{field}={value}"
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if layer == "fleet":
                roster_update(
                    checkout, fleet, "lead", role, change, inside=True, base=base
                )
            elif layer == "project":
                roster_update(
                    checkout, "main", "user", role, change, project_layer=True, base=base
                )
            else:
                roster_update(checkout, "main", "user", role, change, base=base)
    except FoilError as exc:
        reraise_stale(
            exc,
            role=role,
            field=field,
            before=before,
            after=value,
            layer=layer,
            project=project,
            fleet=fleet or "main",
        )
    return {"ok": True}
