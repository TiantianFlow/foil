"""Template and harness load/expand. User harness files override builtins."""

from __future__ import annotations

import shutil
import tomllib
import uuid
from contextlib import suppress
from importlib.resources import files
from pathlib import Path
from typing import Any

from foil.errors import FoilError, assert_no_secret
from foil.project import foil_root
from foil.store import SAFE_ID, create_exclusive

_PLACEHOLDERS = ("{model}", "{prompt}", "{session_id}")
_PRESET_KEYS = {"id", "command", "permission", "resume", "session_id", "env", "unverified"}
_TEMPLATE_KEYS = {"harness", "model", "persona", "worktree", "permission"}
_ROLES = ("lead", "implementer", "reviewer")
BUILTIN_IDS = ("claude", "codex", "gemini", "opencode", "grok", "fake")


def _fail(label: str) -> None:
    raise FoilError(f"foil: invalid {label}")


def _shown(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


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
        raise FoilError(f"foil: unknown harness '{_shown(harness_id)}'")
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
        raise FoilError(f"foil: unknown template '{_shown(name)}'")
    path = foil_root(toplevel) / "templates" / f"{name}.toml"
    if path.is_symlink() or not path.is_file():
        raise FoilError(f"foil: unknown template '{name}'")
    raw = _load_toml(path.read_text(encoding="utf-8"), "template")
    harness = raw.get("harness")
    model = raw.get("model", "")
    persona = raw.get("persona", "")
    worktree = raw.get("worktree", False)
    permission = raw.get("permission", "ask")
    if (
        set(raw) - _TEMPLATE_KEYS
        or not isinstance(harness, str)
        or not SAFE_ID.fullmatch(harness)
        or not isinstance(model, str)
        or not isinstance(persona, str)
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
    path = template["path"].parent / relative
    if path.is_symlink():
        raise FoilError("foil: refusing symlink")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    raise FoilError(f"foil: persona file not found: {persona}")


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


def write_default_templates(toplevel: Path) -> None:
    for name in ("operator.md", "lead.md", "worker.md"):
        target = foil_root(toplevel) / "skills" / name
        if not target.exists() and not target.is_symlink():
            with suppress(FileExistsError):
                create_exclusive(target, _builtin("skills", name).encode())
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
        return
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
            'permission = "ask"\n'
        )
        target = directory / f"{role}.toml"
        if not target.exists() and not target.is_symlink():
            with suppress(FileExistsError):
                create_exclusive(target, body.encode())
