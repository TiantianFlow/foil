"""Board reads: one file or a glob, plus the front matter helper mail shares."""

from __future__ import annotations

import json
from pathlib import Path

from foil.errors import FoilError


def board_root() -> Path:
    for candidate in (Path.cwd(), *Path.cwd().parents):
        board = candidate / ".foil" / "board"
        if board.is_dir():
            return board.resolve()
    raise FoilError("foil: not in a foil project")


def _flow_list(value: str) -> list[str] | None:
    if len(value) < 2 or value[0] != "[" or value[-1] != "]":
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [part.strip().strip("\"'") for part in inner.split(",")]


def split_front_matter(text: str) -> tuple[dict[str, str | list[str]] | None, str]:
    lines = text.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return None, text
    end = lines.index("---", 1)
    fields: dict[str, str | list[str]] = {}
    key: str | None = None
    listing = False
    block = False
    for line in lines[1:end]:
        if key is not None and (line[:1] in " \t" or line == ""):
            stripped = line.strip()
            if listing:
                items = fields[key]
                if stripped.startswith("-") and isinstance(items, list):
                    items.append(stripped[1:].strip())
                continue
            if not block and fields[key] == "" and stripped.startswith("-"):
                listing = True
                fields[key] = [stripped[1:].strip()]
                continue
            fields[key] = f"{fields[key]}\n{stripped}".strip()
            continue
        if ":" not in line:
            continue
        name, _, raw = line.partition(":")
        key = name.strip()
        value = raw.strip()
        block = value in ("|", ">")
        flow = None if block else _flow_list(value)
        listing = flow is not None
        if flow is not None:
            fields[key] = flow
        else:
            fields[key] = "" if block else value
    body = "\n".join(lines[end + 1 :])
    return fields, body[1:] if body.startswith("\n") else body


def inside(path_text: str, root: Path) -> Path:
    given = Path(path_text)
    if ".." in given.parts:
        raise FoilError("foil: path must not contain '..'")
    path = (given if given.is_absolute() else root / given).resolve()
    if not path.is_relative_to(root):
        raise FoilError(f"foil: path not inside {root.parent.name}/{root.name}")
    return path


def read_text(path: Path, shown: str) -> str:
    if not path.is_file():
        raise FoilError(f"foil: file not found: {shown}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise FoilError(f"foil: cannot read: {shown}") from None


def board_read(path_text: str, *, as_json: bool = False) -> None:
    text = read_text(inside(path_text, board_root()), path_text)
    if not as_json:
        print(text, end="" if text.endswith("\n") else "\n")
        return
    fields, body = split_front_matter(text)
    print(json.dumps({**fields, "body": body} if fields is not None else {"body": text}))


def board_list(pattern: str, *, as_json: bool = False) -> None:
    root = board_root()
    if ".." in Path(pattern).parts or Path(pattern).is_absolute():
        raise FoilError("foil: pattern must be relative and must not contain '..'")
    try:
        found = root.glob(pattern)
        files = sorted(str(p.relative_to(root)) for p in found if p.is_file())
    except (ValueError, NotImplementedError):
        raise FoilError(f"foil: invalid pattern: {pattern}") from None
    if as_json:
        print(json.dumps({"files": files}))
    else:
        for name in files:
            print(name)
