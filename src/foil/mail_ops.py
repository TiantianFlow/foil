"""Mail reads: one mail file, or the current seat's mailbox."""

from __future__ import annotations

import json
import os
from pathlib import Path

from foil.board_ops import board_root, inside, read_text, split_front_matter
from foil.errors import FoilError
from foil.store import SAFE_ID


def _fields(text: str, shown: str) -> tuple[dict[str, str | list[str]], str]:
    fields, body = split_front_matter(text)
    if fields is None:
        raise FoilError(f"foil: not a mail file: {shown}")
    return fields, body


def mail_read(path_text: str, *, as_json: bool = False) -> None:
    if not Path(path_text).is_absolute():
        raise FoilError("foil: path must be absolute")
    text = read_text(inside(path_text, board_root() / "mail"), path_text)
    fields, body = _fields(text, path_text)
    if as_json:
        print(
            json.dumps(
                {
                    "contract": fields.get("contract", ""),
                    "from": fields.get("from", ""),
                    "to": fields.get("to", ""),
                    "time": fields.get("time", ""),
                    "re": fields.get("re"),
                    "body": body,
                }
            )
        )
    else:
        print(body, end="" if body.endswith("\n") else "\n")


def mail_list(*, as_json: bool = False) -> None:
    seat = os.environ.get("FOIL_SEAT_ID", "").strip()
    if not seat:
        raise FoilError("foil: FOIL_SEAT_ID not set (mail list needs a seat identity)")
    if SAFE_ID.fullmatch(seat) is None:
        raise FoilError("foil: invalid seat")
    mail_dir = board_root() / "mail"
    mails = []
    # A symlinked mail root resolves outside the board. Do not follow it.
    if not mail_dir.is_symlink():
        mail_root = mail_dir.resolve()
        directory = mail_root / seat
        if (
            not directory.is_symlink()
            and directory.is_dir()
            and directory.resolve().is_relative_to(mail_root)
        ):
            for path in directory.glob("*.md"):
                if path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(mail_root):
                    continue
                try:
                    fields, _ = _fields(path.read_text(encoding="utf-8"), str(path))
                except (OSError, UnicodeDecodeError, FoilError):
                    continue
                mails.append(
                    {
                        "from": fields.get("from", ""),
                        "to": fields.get("to", ""),
                        "time": fields.get("time", ""),
                        "path": str(resolved),
                    }
                )
    mails.sort(key=lambda m: (m["time"], m["path"]), reverse=True)
    if as_json:
        print(json.dumps(mails))
    else:
        for mail in mails:
            print(f"{mail['time']} {mail['from']} {mail['path']}")
