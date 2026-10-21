"""Template and harness load/expand. User harness files override builtins."""

from __future__ import annotations

import shutil
import tomllib
import uuid
from contextvars import ContextVar
from importlib.resources import files
from pathlib import Path
from typing import Any

from foil.errors import FoilError, assert_no_secret, shown
from foil.project import foil_root, store_root
from foil.store import SAFE_ID, fleet_dir, scan, write_bytes

_PLACEHOLDERS = ("{model}", "{prompt}", "{session_id}")
_PRESET_KEYS = {
    "id",
    "command",
    "permission",
    "resume",
    "session_id",
    "env",
    "unverified",
    "models",
}
_TEMPLATE_KEYS = {"harness", "model", "persona", "worktree", "permission"}
_ENTRY_KEYS = {"id", "model"}
_ROLE_KEYS = {"id", "description", "persona", "worktree", "permission"}
_MAP_KEYS = {"role", "harness", "model"}
_FLEET_KEYS = {"role", "harness", "model", "note"}
_PRESET_SAME = ("command", "permission", "resume", "session_id", "env", "unverified", "models")
_ROLES = ("lead", "implementer", "reviewer")
BUILTIN_IDS = ("claude", "codex", "gemini", "opencode", "grok", "fake")
# Set while init checks that a harness is installed, before the project folder is ready.
_init_check: ContextVar[bool] = ContextVar("foil_init_check", default=False)


def _fail(label: str) -> None:
    raise FoilError(f"foil: invalid {label}")


def _permission_field(raw: dict[str, Any]) -> Any:
    return raw.get("permission", "auto")


def template_permission(text: str) -> Any:
    """Permission as load_template reads it. An omitted field is auto."""
    return _permission_field(_load_toml(text, "template"))


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        _fail(label)
    if any(("{" in item or "}" in item) and item not in _PLACEHOLDERS for item in value):
        _fail(label)
    return list(value)


def _builtin(kind: str, name: str) -> str:
    return files("foil").joinpath("defaults", kind, name).read_text(encoding="utf-8")


def _load_toml(text: str, label: str) -> dict[str, Any]:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FoilError(f"foil: invalid {label}") from exc
    if not isinstance(raw, dict):
        _fail(label)
    return raw


def _preset_from_table(raw: dict[str, Any]) -> dict[str, Any]:
    permission = raw.get("permission", {})
    harness_id = raw.get("id")
    if (
        set(raw) - _PRESET_KEYS
        or not isinstance(harness_id, str)
        or not SAFE_ID.fullmatch(harness_id)
        or raw.get("session_id") not in {"generated", "none"}
        or not isinstance(permission, dict)
        or set(permission) - {"ask", "auto"}
    ):
        _fail("preset")
    command = _strings(raw.get("command"), "preset")
    if not command:
        _fail("preset")
    resume = raw.get("resume")
    env = _strings(raw.get("env", []), "preset")
    assert_no_secret(env)
    models = raw.get("models", [])
    if not isinstance(models, list) or any(
        not isinstance(item, str) or not item or item.startswith("-") for item in models
    ):
        _fail("preset")
    return {
        "id": harness_id,
        "command": command,
        "permission": {
            "ask": _strings(permission.get("ask", []), "preset"),
            "auto": _strings(permission.get("auto", []), "preset"),
        },
        "resume": None if resume is None else _strings(resume, "preset"),
        "session_id": raw["session_id"],
        "env": env,
        "unverified": _strings(raw.get("unverified", []), "preset"),
        "models": list(models),
    }


def _parse_preset(text: str, harness_id: str) -> dict[str, Any]:
    preset = _preset_from_table(_load_toml(text, "preset"))
    if preset["id"] != harness_id:
        _fail("preset")
    return preset


def _catalog(text: str) -> dict[str, dict[str, Any]]:
    raw = _load_toml(text, "preset")
    items = raw.get("harness")
    if set(raw) - {"harness"} or not isinstance(items, list) or not items:
        _fail("preset")
    found: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            _fail("preset")
        preset = _preset_from_table(item)
        if preset["id"] in found:
            _fail("preset")
        found[preset["id"]] = preset
    return found


def _packaged_catalog() -> dict[str, dict[str, Any]]:
    text = files("foil").joinpath("defaults", "harnesses.toml").read_text(encoding="utf-8")
    return _catalog(text)


def _optional_text(path: Path) -> str | None:
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _layer_dirs(toplevel: Path) -> tuple[Path, Path]:
    """Global store root, then the project folder. Packaged files stay in the package."""

    return store_root(), foil_root(toplevel)


def _merged_catalog(toplevel: Path) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str]]]:
    """Packaged list, then each layer's list. A broken list file is skipped.

    The init harness check reads the packaged catalog and the global layer
    only, and does not open a project harness file.
    """

    skipped: list[tuple[str, str]] = []
    merged = _packaged_catalog()
    checking = _init_check.get()
    layers = (store_root(),) if checking else _layer_dirs(toplevel)
    for base in layers:
        text = _optional_text(base / "harnesses.toml")
        if text is None:
            continue
        try:
            merged.update(_catalog(text))
        except FoilError as exc:
            skipped.append(("harnesses.toml", str(exc).removeprefix("foil: ")))
    return merged, skipped


def _preset_on(base: Path, harness_id: str) -> dict[str, Any] | None:
    """The file at one layer, else that layer's list entry. A symlink is refused."""

    user = base / "harnesses" / f"{harness_id}.toml"
    if user.is_symlink():
        raise FoilError("foil: refusing symlink")
    if user.is_file():
        return _parse_preset(user.read_text(encoding="utf-8"), harness_id)
    text = _optional_text(base / "harnesses.toml")
    if text is None:
        return None
    try:
        return _catalog(text).get(harness_id)
    except FoilError:
        return None


def load_preset(toplevel: Path, harness_id: str) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(harness_id):
        raise FoilError(f"foil: unknown harness '{shown(harness_id)}'")
    # File beats list inside one layer. A higher list beats a lower file.
    # The init check reads the global layer and the package, not a project file.
    layers = (store_root(),) if _init_check.get() else _layer_dirs(toplevel)
    for base in reversed(layers):
        found = _preset_on(base, harness_id)
        if found is not None:
            return found
    packaged = _packaged_catalog().get(harness_id)
    if packaged is None:
        raise FoilError(f"foil: unknown harness '{harness_id}'")
    return packaged


def _is_flag(token: str) -> bool:
    return len(token) > 1 and token.startswith("-") and token not in _PLACEHOLDERS


def _fill(argv: list[str], values: dict[str, str | None]) -> list[str]:
    filled: list[str] = []
    for token in argv:
        if token not in _PLACEHOLDERS:
            filled.append(token)
            continue
        value = values[token[1:-1]]
        if value:
            filled.append(value)
        elif filled and _is_flag(filled[-1]):
            filled.pop()
    return filled


def _toml_basic(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\b", "\\b")
        .replace("\t", "\\t")
        .replace("\n", "\\n")
        .replace("\f", "\\f")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _entry(harness_id: Any, model: Any) -> dict[str, str]:
    if (
        not isinstance(harness_id, str)
        or not harness_id
        or not SAFE_ID.fullmatch(harness_id)
        or not isinstance(model, str)
        or model.startswith("-")
    ):
        _fail("template")
    return {"id": harness_id, "model": model}


def _harness_entries(raw: dict[str, Any]) -> list[dict[str, str]]:
    harness = raw.get("harness")
    # Legacy string form (`harness = "id"`, optional top-level `model`).
    # Remove this read when the project version reaches 0.5.0 or higher.
    if isinstance(harness, str):
        return [_entry(harness, raw.get("model", ""))]
    if "model" in raw or not isinstance(harness, list) or not harness:
        _fail("template")
    entries: list[dict[str, str]] = []
    for item in harness:
        if not isinstance(item, dict) or set(item) - _ENTRY_KEYS or "id" not in item:
            _fail("template")
        entries.append(_entry(item["id"], item.get("model", "")))
    return entries


def entry_label(entry: dict[str, str]) -> str:
    return f"{entry['id']}/{entry['model']}" if entry["model"] else entry["id"]


def render_roles(roles: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for role in roles:
        lines = [
            "[[role]]",
            f"id = {_toml_basic(str(role['id']))}",
            f"description = {_toml_basic(str(role.get('description') or ''))}",
            f"persona = {_toml_basic(str(role.get('persona') or ''))}",
            f"worktree = {'true' if role.get('worktree') else 'false'}",
            f"permission = {_toml_basic(str(role.get('permission') or 'auto'))}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_maps(maps: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for item in maps:
        lines = [
            "[[map]]",
            f"role = {_toml_basic(item['role'])}",
            f"harness = {_toml_basic(item['id'])}",
        ]
        if item.get("model"):
            lines.append(f"model = {_toml_basic(item['model'])}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _parse_roles(text: str) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    raw = _load_toml(text, "template")
    if not raw:
        return []
    items = raw.get("role")
    if set(raw) - {"role"} or not isinstance(items, list):
        _fail("template")
    roles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - _ROLE_KEYS or "id" not in item:
            _fail("template")
        role_id = item["id"]
        description = item.get("description", "")
        persona = item.get("persona", "")
        worktree = item.get("worktree", False)
        permission = _permission_field(item)
        if (
            not isinstance(role_id, str)
            or not SAFE_ID.fullmatch(role_id)
            or role_id in seen
            or not isinstance(description, str)
            or description.startswith("-")
            or not isinstance(persona, str)
            or persona.startswith("-")
            or not isinstance(worktree, bool)
            or permission not in {"ask", "auto"}
        ):
            _fail("template")
        seen.add(role_id)
        roles.append(
            {
                "id": role_id,
                "description": description,
                "persona": persona,
                "worktree": worktree,
                "permission": permission,
            }
        )
    return roles


def _parse_maps(text: str) -> list[dict[str, str]]:
    if not text.strip():
        return []
    raw = _load_toml(text, "template")
    if not raw:
        return []
    items = raw.get("map")
    if set(raw) - {"map"} or not isinstance(items, list):
        _fail("template")
    maps: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - _MAP_KEYS:
            _fail("template")
        role = item.get("role")
        if not isinstance(role, str) or not SAFE_ID.fullmatch(role) or "harness" not in item:
            _fail("template")
        entry = _entry(item["harness"], item.get("model", ""))
        maps.append({"role": role, "id": entry["id"], "model": entry["model"]})
    return maps


def _settings_base(toplevel: Path) -> Path:
    """The global layer. Init writes nothing under the project folder."""

    del toplevel
    return store_root()


def _effective_roles(toplevel: Path) -> list[dict[str, Any]] | None:
    """Global roles, replaced whole by a project file when one exists.

    The init harness check does not open a project settings file. Init deletes
    those paths without reading them, including a symlink.
    """

    text = _optional_text(store_root() / "roles.toml")
    roles = None if text is None else _parse_roles(text)
    if _init_check.get():
        return roles
    project = _optional_text(foil_root(toplevel) / "roles.toml")
    if project is None:
        return roles
    override = {item["id"]: item for item in _parse_roles(project)}
    if roles is None:
        return list(override.values())
    seen = {item["id"] for item in roles}
    merged = [override.get(item["id"], item) for item in roles]
    merged.extend(item for item in override.values() if item["id"] not in seen)
    return merged


def _effective_maps(toplevel: Path) -> list[dict[str, str]]:
    """Global mapping, with a project file replacing one role's whole list."""

    text = _optional_text(store_root() / "mapping.toml")
    maps = [] if text is None else _parse_maps(text)
    if _init_check.get():
        return maps
    project = _optional_text(foil_root(toplevel) / "mapping.toml")
    if project is None:
        return maps
    override = _parse_maps(project)
    replaced = {item["role"] for item in override}
    return [item for item in maps if item["role"] not in replaced] + override


def read_roles(toplevel: Path) -> list[dict[str, Any]] | None:
    """Profiles from ``roles.toml``, or None when that file is absent.

    A project file replaces the roles it names. Writes stay on the global layer.
    """

    return _effective_roles(toplevel)


def read_maps(toplevel: Path) -> list[dict[str, str]]:
    """The mapping list. A project file replaces each role it names, whole."""

    return _effective_maps(toplevel)


def save_roles(toplevel: Path, roles: list[dict[str, Any]]) -> None:
    path = _settings_base(toplevel) / "roles.toml"
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    write_bytes(path, render_roles(roles).encode())


def save_maps(toplevel: Path, maps: list[dict[str, str]]) -> None:
    path = _settings_base(toplevel) / "mapping.toml"
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    write_bytes(path, render_maps(maps).encode())


def render_fleet(items: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for item in items:
        lines = ["[[override]]", f"role = {_toml_basic(item['role'])}"]
        if item["id"]:
            lines.append(f"harness = {_toml_basic(item['id'])}")
        if item["model"]:
            lines.append(f"model = {_toml_basic(item['model'])}")
        if item.get("note"):
            lines.append(f"note = {_toml_basic(item['note'])}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def fleet_overrides(toplevel: Path, fleet: str | None = None) -> list[dict[str, str]]:
    """Fleet memory. A role's rows, in order, are that fleet's list for the role.

    ``id`` is empty on a row that names only a model (the older form).
    """

    text = _optional_text(fleet_dir(toplevel, fleet) / "overrides.toml")
    if text is None or not text.strip():
        return []
    raw = _load_toml(text, "fleet memory")
    if not raw:
        return []
    items = raw.get("override")
    if set(raw) - {"override"} or not isinstance(items, list):
        _fail("fleet memory")
    found: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) - _FLEET_KEYS:
            _fail("fleet memory")
        role = item.get("role")
        harness = item.get("harness", "")
        model = item.get("model", "")
        note = item.get("note", "")
        if (
            not isinstance(role, str)
            or not SAFE_ID.fullmatch(role)
            or not isinstance(harness, str)
            or (harness and not SAFE_ID.fullmatch(harness))
            or not isinstance(model, str)
            or not (model or harness)
            or model.startswith("-")
            or not isinstance(note, str)
        ):
            _fail("fleet memory")
        scan(model)
        scan(note)
        found.append({"role": role, "id": harness, "model": model, "note": note})
    return found


def spawn_entry(
    toplevel: Path,
    role: str,
    entries: list[dict[str, str]],
    count: int,
    fleet: str | None = None,
) -> dict[str, str]:
    """The harness and model the next spawn of ``role`` takes (F3).

    The fleet's rows for the role, when it has any, replace the mapping list.
    A row with no harness keeps the id the mapping selects when that list has
    one entry. On a longer list the older model-only row is refused.
    """

    chosen = select_harness(entries, count)
    rows = [item for item in fleet_overrides(toplevel, fleet) if item["role"] == role]
    if not rows:
        return chosen
    row = select_harness(rows, count)
    if not row["id"] and len(entries) > 1:
        raise FoilError(
            f"foil: template '{role}' has more than one harness entry; "
            f"set the whole list with foil roster update {role} harness=ID/MODEL,ID/MODEL"
        )
    return {"id": row["id"] or chosen["id"], "model": row["model"]}


def fleet_list(toplevel: Path, role: str) -> list[dict[str, str]]:
    """This fleet's list for ``role`` when every row names a harness, else empty."""

    rows = [item for item in fleet_overrides(toplevel) if item["role"] == role]
    if not all(item["id"] for item in rows):
        return []
    return [{"id": item["id"], "model": item["model"]} for item in rows]


def remember_entries(toplevel: Path, role: str, entries: list[dict[str, str]]) -> None:
    """Replace this fleet's rows for ``role`` with ``entries``, in order."""

    if not SAFE_ID.fullmatch(role) or not entries:
        _fail("fleet memory")
    for entry in entries:
        _entry(entry["id"], entry["model"])
        scan(entry["model"])
    items = [item for item in fleet_overrides(toplevel) if item["role"] != role]
    items += [{"role": role, "id": e["id"], "model": e["model"], "note": ""} for e in entries]
    path = fleet_dir(toplevel) / "overrides.toml"
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    write_bytes(path, render_fleet(items).encode())


def select_harness(entries: list[dict[str, str]], count: int) -> dict[str, str]:
    """Return ``entries[count % len(entries)]``."""

    return entries[count % len(entries)]


def expand_argv(
    preset: dict[str, Any],
    *,
    model: str | None = None,
    prompt: str | None = None,
    session_id: str | None = None,
    permission: str = "ask",
    resume: bool = False,
) -> list[str]:
    """Expand a preset. ``prompt`` is the caller's instruction sentence."""

    if permission not in {"ask", "auto"}:
        _fail("template")
    if model is not None and model.startswith("-"):
        raise FoilError("foil: model must not start with '-'")
    argv = preset["resume"] if resume else preset["command"]
    if not argv:
        _fail("preset")
    bound = None if preset["session_id"] == "none" else session_id or str(uuid.uuid4())
    values = {"model": model or None, "prompt": prompt or None, "session_id": bound}
    filled = _fill(argv, values)
    extra = preset["permission"][permission]
    text = values["prompt"]
    if not extra or not text or text not in filled:
        return filled + extra
    index = filled.index(text)
    if index > 0 and _is_flag(filled[index - 1]):
        index -= 1
    return filled[:index] + extra + filled[index:]


def parse_template_text(text: str) -> dict[str, Any]:
    raw = _load_toml(text, "template")
    if set(raw) - _TEMPLATE_KEYS:
        _fail("template")
    entries = _harness_entries(raw)
    persona = raw.get("persona", "")
    worktree = raw.get("worktree", False)
    permission = _permission_field(raw)
    if (
        not isinstance(persona, str)
        or persona.startswith("-")
        or not isinstance(worktree, bool)
        or permission not in {"ask", "auto"}
    ):
        _fail("template")
    return {
        "harness": entries,
        "persona": persona,
        "worktree": worktree,
        "permission": permission,
    }


def _listed_template(toplevel: Path, name: str) -> dict[str, Any] | None:
    roles = read_roles(toplevel)
    if roles is None:
        return None
    match = next((role for role in roles if role["id"] == name), None)
    if match is None:
        return None
    entries = [
        {"id": item["id"], "model": item["model"]}
        for item in read_maps(toplevel)
        if item["role"] == name
    ]
    if not entries:
        _fail("template")
    return {
        "name": name,
        "path": foil_root(toplevel) / "templates" / f"{name}.toml",
        "harness": entries,
        "persona": match["persona"],
        "worktree": match["worktree"],
        "permission": match["permission"],
        "description": match["description"],
    }


def load_template(toplevel: Path, name: str) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(name):
        raise FoilError(f"foil: unknown template '{shown(name)}'")
    listed = _listed_template(toplevel, name)
    if listed is None:
        raise FoilError(f"foil: unknown template '{name}'")
    return listed


def persona_text(template: dict[str, Any]) -> str:
    persona = template["persona"]
    if not persona or "\n" in persona or "\r" in persona or not persona.endswith(".md"):
        return persona
    relative = Path(persona)
    if relative.is_absolute() or ".." in relative.parts:
        raise FoilError(f"foil: persona path must stay inside personas: {persona}")
    name = relative.name
    if relative.parts != ("personas", name):
        raise FoilError(f"foil: persona path must stay inside personas: {persona}")
    packaged = files("foil").joinpath("defaults", "personas", name)
    project_root = template["path"].parent.parent
    bases = (store_root() / "personas" / name, project_root / "personas" / name)
    for path in bases:
        if path.is_symlink():
            raise FoilError("foil: refusing symlink")
    project, global_file = bases[1], bases[0]
    if project.is_file():
        return project.read_text(encoding="utf-8")
    if global_file.is_file():
        return global_file.read_text(encoding="utf-8")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    raise FoilError(f"foil: persona file not found: {persona}")


def description_of(text: str) -> str:
    stripped = (line.strip() for line in text.splitlines())
    found = next((line for line in stripped if line and not line.startswith("#")), "")
    try:
        scan(found)
    except FoilError:
        return ""
    return found.replace("\t", " ")


def read_description(template: dict[str, Any]) -> str:
    stored = template.get("description") or ""
    if isinstance(stored, str) and stored:
        try:
            scan(stored)
        except FoilError:
            return ""
        return stored.replace("\t", " ")
    try:
        return description_of(persona_text(template))
    except (FoilError, OSError, UnicodeDecodeError):
        return ""


def path_description(path: Path) -> str:
    try:
        text = "" if path.is_symlink() or not path.is_file() else path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return description_of(text)


def _same_preset(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(key) == right.get(key) for key in _PRESET_SAME)


def preset_source(toplevel: Path, harness_id: str) -> str:
    """Where the winning preset came from: packaged, global, project, or invalid."""

    try:
        loaded = load_preset(toplevel, harness_id)
    except (FoilError, OSError, UnicodeDecodeError):
        return "invalid"
    checking = _init_check.get()
    if not checking:
        try:
            project = foil_root(toplevel)
        except FoilError:
            project = None
        if project is not None:
            try:
                found = _preset_on(project, harness_id)
            except FoilError:
                return "invalid"
            if found is not None and _same_preset(found, loaded):
                return "project"
    try:
        found = _preset_on(store_root(), harness_id)
    except FoilError:
        return "invalid"
    if found is not None and _same_preset(found, loaded):
        return "global"
    packaged = _packaged_catalog().get(harness_id)
    if packaged is not None and _same_preset(loaded, packaged):
        return "packaged"
    return "invalid"


def persona_stems(directory: Path) -> list[str]:
    if directory.is_symlink() or not directory.is_dir():
        return []
    paths = directory.glob("*.md")
    return sorted(path.stem for path in paths if path.is_file() and not path.is_symlink())


def launch_command(
    toplevel: Path, name: str, *, prompt: str, session_id: str | None = None
) -> list[str]:
    template = load_template(toplevel, name)
    entry = spawn_entry(toplevel, name, template["harness"], 0)
    preset = load_preset(toplevel, entry["id"])
    return expand_argv(
        preset,
        model=entry["model"] or None,
        prompt=prompt,
        session_id=session_id,
        permission=template["permission"],
    )


def preset_ids(toplevel: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """Every eligible id, sorted by code point, and the list files skipped."""

    merged, skipped = _merged_catalog(toplevel)
    ids = {name for name in merged if name != "fake"}
    layers = (store_root(),) if _init_check.get() else _layer_dirs(toplevel)
    for base in layers:
        directory = base / "harnesses"
        if not directory.is_dir() or directory.is_symlink():
            continue
        for path in directory.glob("*.toml"):
            harness_id = path.stem
            if (
                path.is_symlink()
                or not path.is_file()
                or harness_id == "fake"
                or not SAFE_ID.fullmatch(harness_id)
            ):
                continue
            ids.add(harness_id)
    return sorted(ids), skipped


def installed_presets(
    toplevel: Path,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Eligible presets whose own command[0] is on PATH, and skipped files.

    ``fake`` is excluded. A user file that reuses a built-in id replaces
    that built-in. Ids are sorted by Unicode code point, case preserved:
    a tiebreak, not a ranking. One invalid file is skipped. Its display
    path and the reason are returned beside the presets that loaded.
    ``load_preset`` itself is unchanged, so a spawn that names a broken
    preset still fails.
    """

    ids, skipped = preset_ids(toplevel)
    found: list[dict[str, Any]] = []
    for harness_id in ids:
        try:
            preset = load_preset(toplevel, harness_id)
        except FoilError as exc:
            skipped.append(
                (f"harnesses/{harness_id}.toml", str(exc).removeprefix("foil: "))
            )
            continue
        if shutil.which(preset["command"][0]):
            found.append(preset)
    return found, skipped


def installed_harness(toplevel: Path | None = None) -> str:
    token = _init_check.set(True)
    try:
        found, _skipped = installed_presets(Path.cwd() if toplevel is None else toplevel)
    finally:
        _init_check.reset(token)
    if not found:
        raise FoilError("foil: no harness installed")
    return found[0]["id"]


def write_default_templates(toplevel: Path) -> list[str]:
    """Write each missing global default role. Return the names written.

    This does not open a project settings path, copy a persona, copy the
    harness catalog, or write a skill.
    """

    updated: list[str] = []
    present = {item["id"] for item in (read_roles(toplevel) or [])}
    missing = [role for role in _ROLES if role not in present]
    if not missing:
        return updated
    token = _init_check.set(True)
    try:
        found, _skipped = installed_presets(toplevel)
    finally:
        _init_check.reset(token)
    if not found:
        raise FoilError("foil: no harness installed")
    first = found[0]["id"]
    second = found[1]["id"] if len(found) > 1 else first
    roles = list(read_roles(toplevel) or [])
    maps = [item for item in read_maps(toplevel) if item["role"] not in missing]
    for role in missing:
        harness = second if role == "reviewer" else first
        roles.append(
            {
                "id": role,
                "description": description_of(_builtin("personas", f"{role}.md")),
                "persona": f"personas/{role}.md",
                "worktree": role == "implementer",
                "permission": "auto",
            }
        )
        maps.append({"role": role, "id": harness, "model": ""})
    save_roles(toplevel, roles)
    save_maps(toplevel, maps)
    return updated
