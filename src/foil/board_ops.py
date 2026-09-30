"""Board read and list operations."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from foil.errors import FoilError
from foil.project import foil_root


def _parse_frontmatter_optional(content: str) -> tuple[dict[str, str] | None, str]:
    """Parse YAML frontmatter if present, return (frontmatter_dict, body) or (None, full_content)."""
    lines = content.split("\n")
    if not lines or lines[0] != "---":
        # No frontmatter - return None and full content as body
        return None, content

    # Find end of frontmatter
    try:
        end_idx = lines.index("---", 1)
    except ValueError:
        # Unclosed frontmatter - treat as no frontmatter
        return None, content

    # Parse frontmatter (simple YAML parsing)
    frontmatter = {}
    for line in lines[1:end_idx]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ": " in line:
            key, value = line.split(": ", 1)
            frontmatter[key] = value
        elif ": |" in line or ": >" in line:
            # Multiline value (simplified - just save the key for now)
            key = line.split(":")[0].strip()
            frontmatter[key] = ""  # Multiline content would need more parsing

    # Body is everything after the closing ---
    body_lines = lines[end_idx + 1:]
    # Skip leading empty line if present
    if body_lines and body_lines[0] == "":
        body_lines = body_lines[1:]
    body = "\n".join(body_lines)

    return frontmatter, body


def _validate_board_path(path_str: str, board_root: Path) -> Path:
    """Validate that path is in .foil/board/ and is safe.

    Returns resolved Path if valid.
    Raises FoilError if invalid.
    """
    # Check for .. components
    if ".." in Path(path_str).parts:
        raise FoilError("foil: path must not contain '..'")

    # Convert relative paths to absolute (relative to board root)
    if not Path(path_str).is_absolute():
        path = (board_root / path_str).resolve()
    else:
        try:
            path = Path(path_str).resolve()
        except (ValueError, OSError) as e:
            raise FoilError(f"foil: invalid path: {e}")

    # Path must be under board_root
    try:
        path.relative_to(board_root)
    except ValueError:
        raise FoilError("foil: path not in .foil/board/")

    return path


def _find_board_root() -> Path:
    """Find .foil/board/ from current directory."""
    cwd = Path.cwd()
    for candidate in [cwd] + list(cwd.parents):
        board = candidate / ".foil" / "board"
        if board.is_dir():
            return board
    raise FoilError("foil: not in a foil project")


def board_read(path_str: str, *, as_json: bool = False) -> None:
    """Read board file at PATH.

    Human output: full file contents.
    JSON output: parsed frontmatter + body (or {"body": "..."} for non-contract files).
    """
    board_root = _find_board_root()
    path = _validate_board_path(path_str, board_root)

    if not path.is_file():
        raise FoilError(f"foil: file not found: {path_str}")

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise FoilError(f"foil: cannot read file: {e}")

    if as_json:
        frontmatter, body = _parse_frontmatter_optional(content)
        if frontmatter is not None:
            # Contract file - output frontmatter fields + body
            output = dict(frontmatter)
            output["body"] = body
        else:
            # Non-contract file - just body
            output = {"body": content}
        print(json.dumps(output, ensure_ascii=False))
    else:
        # Human output: full file
        print(content, end="" if content.endswith("\n") else "\n")


def board_list(pattern: str, *, as_json: bool = False) -> None:
    """List board files matching glob pattern.

    Pattern is relative to board root.
    Human output: one path per line (relative to board root).
    JSON output: {"files": [...]}.
    """
    board_root = _find_board_root()

    # Validate pattern doesn't try to escape
    if ".." in Path(pattern).parts:
        raise FoilError("foil: pattern must not contain '..'")

    # Glob from board root
    try:
        matches = list(board_root.glob(pattern))
    except (ValueError, OSError) as e:
        raise FoilError(f"foil: invalid pattern: {e}")

    # Filter to files only and get paths relative to board root
    files = []
    for match in matches:
        if match.is_file():
            try:
                rel_path = match.relative_to(board_root)
                files.append(str(rel_path))
            except ValueError:
                # Shouldn't happen with glob, but skip if it does
                continue

    # Sort alphabetically
    files.sort()

    if as_json:
        print(json.dumps({"files": files}, ensure_ascii=False))
    else:
        # Human output: one path per line
        for file in files:
            print(file)
