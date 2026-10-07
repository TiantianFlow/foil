"""Template and harness load/expand. User harness files override builtins."""

from __future__ import annotations

import shutil
import tomllib
import uuid
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

from foil.errors import FoilError, assert_no_secret, shown
from foil.project import foil_root
from foil.store import SAFE_ID, create_exclusive, scan, write_bytes

_PLACEHOLDERS = ("{model}", "{prompt}", "{session_id}")
_PRESET_KEYS = {"id", "command", "permission", "resume", "session_id", "env", "unverified"}
_TEMPLATE_KEYS = {"harness", "model", "persona", "worktree", "permission"}
_ROLES = ("lead", "implementer", "reviewer")
BUILTIN_IDS = ("claude", "codex", "gemini", "opencode", "grok", "fake")


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


def _parse_preset(text: str, harness_id: str) -> dict[str, Any]:
    raw = _load_toml(text, "preset")
    permission = raw.get("permission", {})
    if (
        set(raw) - _PRESET_KEYS
        or raw.get("id") != harness_id
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
    }


def load_preset(toplevel: Path, harness_id: str) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(harness_id):
        raise FoilError(f"foil: unknown harness '{shown(harness_id)}'")
    user = foil_root(toplevel) / "harnesses" / f"{harness_id}.toml"
    if user.is_symlink():
        raise FoilError("foil: refusing symlink")
    if user.is_file():
        return _parse_preset(user.read_text(encoding="utf-8"), harness_id)
    if harness_id not in BUILTIN_IDS:
        raise FoilError(f"foil: unknown harness '{harness_id}'")
    return _parse_preset(_builtin("harnesses", f"{harness_id}.toml"), harness_id)


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


def load_template(toplevel: Path, name: str) -> dict[str, Any]:
    if not SAFE_ID.fullmatch(name):
        raise FoilError(f"foil: unknown template '{shown(name)}'")
    path = foil_root(toplevel) / "templates" / f"{name}.toml"
    if path.is_symlink() or not path.is_file():
        raise FoilError(f"foil: unknown template '{name}'")
    raw = _load_toml(path.read_text(encoding="utf-8"), "template")
    harness = raw.get("harness")
    model = raw.get("model", "")
    persona = raw.get("persona", "")
    worktree = raw.get("worktree", False)
    permission = _permission_field(raw)
    if (
        set(raw) - _TEMPLATE_KEYS
        or not isinstance(harness, str)
        or not SAFE_ID.fullmatch(harness)
        or not isinstance(model, str)
        or model.startswith("-")
        or not isinstance(persona, str)
        or persona.startswith("-")
        or not isinstance(worktree, bool)
        or permission not in {"ask", "auto"}
    ):
        _fail("template")
    return {
        "name": name,
        "path": path,
        "harness": harness,
        "model": model,
        "persona": persona,
        "worktree": worktree,
        "permission": permission,
    }


def persona_text(template: dict[str, Any]) -> str:
    persona = template["persona"]
    if not persona or "\n" in persona or "\r" in persona or not persona.endswith(".md"):
        return persona
    relative = Path(persona)
    if relative.is_absolute() or ".." in relative.parts:
        raise FoilError(f"foil: persona path must stay inside .foil/templates: {persona}")
    base = template["path"].parent
    path = base / relative
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if not path.resolve().is_relative_to(base.resolve()):
        raise FoilError(f"foil: persona path must stay inside .foil/templates: {persona}")
    if path.is_file():
        return path.read_text(encoding="utf-8")
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


def preset_source(toplevel: Path, harness_id: str) -> str:
    try:
        load_preset(toplevel, harness_id)
    except (FoilError, OSError, UnicodeDecodeError):
        return "invalid"
    user = foil_root(toplevel) / "harnesses" / f"{harness_id}.toml"
    return "user" if user.is_file() else "builtin"


def persona_stems(directory: Path) -> list[str]:
    if directory.is_symlink() or not directory.is_dir():
        return []
    paths = directory.glob("*.md")
    return sorted(path.stem for path in paths if path.is_file() and not path.is_symlink())


def launch_command(
    toplevel: Path, name: str, *, prompt: str, session_id: str | None = None
) -> list[str]:
    template = load_template(toplevel, name)
    preset = load_preset(toplevel, template["harness"])
    return expand_argv(
        preset,
        model=template["model"] or None,
        prompt=prompt,
        session_id=session_id,
        permission=template["permission"],
    )


def _packaged_persona_names() -> list[str]:
    directory = files("foil").joinpath("defaults", "personas")
    return sorted(item.name for item in directory.iterdir() if item.name.endswith(".md"))


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

    ids = {name for name in BUILTIN_IDS if name != "fake"}
    directory = foil_root(toplevel) / "harnesses"
    if directory.is_dir() and not directory.is_symlink():
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
    found: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for harness_id in sorted(ids):
        try:
            preset = load_preset(toplevel, harness_id)
        except FoilError as exc:
            skipped.append(
                (f".foil/harnesses/{harness_id}.toml", str(exc).removeprefix("foil: "))
            )
            continue
        if shutil.which(preset["command"][0]):
            found.append(preset)
    return found, skipped


def installed_harness(toplevel: Path | None = None) -> str:
    found, _skipped = installed_presets(Path.cwd() if toplevel is None else toplevel)
    if not found:
        raise FoilError("foil: no harness installed")
    return found[0]["id"]


def write_default_templates(toplevel: Path) -> list[str]:
    """Replace the operator skill when its bytes differ. Return the names written."""

    updated: list[str] = []
    target = foil_root(toplevel) / "skills" / "operator.md"
    if target.is_symlink():
        raise FoilError("foil: refusing symlink")
    payload = _builtin("skills", "operator.md").encode()
    if not (target.is_file() and target.read_bytes() == payload):
        write_bytes(target, payload)
        updated.append("operator")
    directory = foil_root(toplevel) / "templates"
    personas = directory / "personas"
    for name in _packaged_persona_names():
        target = personas / name
        if not target.exists() and not target.is_symlink():
            with suppress(FileExistsError):
                create_exclusive(target, _builtin("personas", name).encode())
    missing = [
        role
        for role in _ROLES
        if not (directory / f"{role}.toml").is_symlink()
        and not (directory / f"{role}.toml").exists()
    ]
    if not missing:
        return updated
    found, _skipped = installed_presets(toplevel)
    if not found:
        raise FoilError("foil: no harness installed")
    first = found[0]["id"]
    second = found[1]["id"] if len(found) > 1 else first
    for role in missing:
        harness = second if role == "reviewer" else first
        worktree = "true" if role == "implementer" else "false"
        body = (
            f'harness = "{harness}"\n'
            f'persona = "personas/{role}.md"\n'
            f"worktree = {worktree}\n"
            'permission = "auto"\n'
        )
        target = directory / f"{role}.toml"
        if not target.exists() and not target.is_symlink():
            with suppress(FileExistsError):
                create_exclusive(target, body.encode())
    return updated
