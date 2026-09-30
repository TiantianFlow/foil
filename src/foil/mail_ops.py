"""Mail read and list operations."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from foil.errors import FoilError
from foil.project import foil_root


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter and body from mail file.

    Returns (frontmatter_dict, body).
    Raises FoilError if format is invalid.
    """
    lines = content.split("\n")
    if not lines or lines[0] != "---":
        raise FoilError("foil: invalid mail format (missing frontmatter)")

    # Find end of frontmatter
    try:
        end_idx = lines.index("---", 1)
    except ValueError:
        raise FoilError("foil: invalid mail format (unclosed frontmatter)")

    # Parse frontmatter (simple YAML parsing for our known fields)
    frontmatter = {}
    for line in lines[1:end_idx]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ": " in line:
            key, value = line.split(": ", 1)
            frontmatter[key] = value

    # Body is everything after the closing ---
    body_lines = lines[end_idx + 1:]
    # Skip leading empty line if present
    if body_lines and body_lines[0] == "":
        body_lines = body_lines[1:]
    body = "\n".join(body_lines)

    return frontmatter, body


def _validate_mail_path(path_str: str) -> Path:
    """Validate that path is in .foil/board/mail/ and is safe.

    Returns resolved Path if valid.
    Raises FoilError if invalid.
    """
    try:
        path = Path(path_str).resolve()
    except (ValueError, OSError) as e:
        raise FoilError(f"foil: invalid path: {e}")

    # Check for .. components before resolution
    if ".." in Path(path_str).parts:
        raise FoilError("foil: path must not contain '..'")

    # Must be absolute
    if not path.is_absolute():
        raise FoilError("foil: mail path must be absolute")

    # Find board root - go up from path until we find .foil
    current = path
    board_mail = None
    for parent in [path] + list(path.parents):
        if (parent / ".foil" / "board" / "mail").exists():
            board_mail = parent / ".foil" / "board" / "mail"
            break

    if board_mail is None:
        raise FoilError("foil: path not in .foil/board/mail/")

    # Path must be under board_mail
    try:
        path.relative_to(board_mail)
    except ValueError:
        raise FoilError("foil: path not in .foil/board/mail/")

    return path


def mail_read(path_str: str, *, as_json: bool = False) -> None:
    """Read mail file at PATH.

    Human output: body only.
    JSON output: full contract + body.
    """
    path = _validate_mail_path(path_str)

    if not path.is_file():
        raise FoilError(f"foil: mail file not found: {path_str}")

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise FoilError(f"foil: cannot read mail: {e}")

    frontmatter, body = _parse_frontmatter(content)

    if as_json:
        output = {
            "from": frontmatter.get("from", ""),
            "to": frontmatter.get("to", ""),
            "time": frontmatter.get("time", ""),
            "re": frontmatter.get("re") if "re" in frontmatter else None,
            "body": body,
        }
        print(json.dumps(output, ensure_ascii=False))
    else:
        # Human output: body only
        print(body, end="" if body.endswith("\n") else "\n")


def mail_list(*, as_json: bool = False) -> None:
    """List mail for current seat (identified by FOIL_SEAT_ID).

    Human output: one line per mail with time, sender, and path.
    JSON output: array of mail metadata.
    """
    seat_id = os.environ.get("FOIL_SEAT_ID", "").strip()
    if not seat_id:
        raise FoilError("foil: FOIL_SEAT_ID not set (mail list needs a seat identity)")

    # Find project root (current dir or parent with .foil)
    cwd = Path.cwd()
    toplevel = None
    for candidate in [cwd] + list(cwd.parents):
        if (candidate / ".foil").is_dir():
            toplevel = candidate
            break

    if toplevel is None:
        raise FoilError("foil: not in a foil project")

    mail_dir = foil_root(toplevel) / "board" / "mail" / seat_id

    if not mail_dir.is_dir():
        # No mail directory means no mail - output empty list
        if as_json:
            print("[]")
        return

    # Collect all mail files
    mails = []
    for mail_file in mail_dir.glob("*.md"):
        if not mail_file.is_file():
            continue

        try:
            content = mail_file.read_text(encoding="utf-8")
            frontmatter, _ = _parse_frontmatter(content)

            mails.append({
                "time": frontmatter.get("time", ""),
                "from": frontmatter.get("from", ""),
                "to": frontmatter.get("to", ""),
                "path": str(mail_file.resolve()),
            })
        except (OSError, UnicodeDecodeError, FoilError):
            # Skip files we can't read or parse
            continue

    # Sort by time, newest first
    mails.sort(key=lambda m: m["time"], reverse=True)

    if as_json:
        print(json.dumps(mails, ensure_ascii=False))
    else:
        # Human output: <time> <from> <path>
        for mail in mails:
            print(f"{mail['time']} {mail['from']} {mail['path']}")
