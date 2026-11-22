"""Mail reads: one mail file, or the current seat's mailbox."""

from __future__ import annotations

import json
import os
from pathlib import Path

from foil.board import mail_from
from foil.board_ops import board_root, inside, read_text, split_front_matter
from foil.delivery import age_seconds, derived, latest_line
from foil.errors import FoilError
from foil.runner import load_record
from foil.store import actor


def _fields(text: str, shown: str) -> tuple[dict[str, str | list[str]], str]:
    fields, body = split_front_matter(text)
    if fields is None:
        raise FoilError(f"foil: not a mail file: {shown}")
    return fields, body


def mail_read_payload(path_text: str) -> dict[str, str | list[str] | None]:
    if not Path(path_text).is_absolute():
        raise FoilError("foil: path must be absolute")
    text = read_text(inside(path_text, board_root() / "mail"), path_text)
    fields, body = _fields(text, path_text)
    return {
        "contract": fields.get("contract", ""),
        "from": fields.get("from", ""),
        "to": fields.get("to", ""),
        "time": fields.get("time", ""),
        "re": fields.get("re"),
        "body": body,
    }


def show_mail_read(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload))
        return
    body = str(payload.get("body", ""))
    print(body, end="" if body.endswith("\n") else "\n")


def mail_read(path_text: str, *, as_json: bool = False) -> None:
    show_mail_read(mail_read_payload(path_text), as_json=as_json)


def mail_rows() -> list[dict[str, str]]:
    # Outside the fleet this is the joined lead's mailbox, or the human's.
    seat = actor()
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
                named = mail_from(board_root(), seat, path.name)
                if named is None or path.is_symlink() or not path.is_file():
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(mail_root):
                    continue
                try:
                    fields, _ = _fields(path.read_text(encoding="utf-8"), str(path))
                except (OSError, UnicodeDecodeError, FoilError):
                    continue
                if fields.get("contract") != "mail/v1":
                    continue
                # Not from front matter: a hand-written file could choose it (F30).
                when, sender = named
                mails.append({"from": sender, "to": seat, "time": when, "path": str(resolved)})
    mails.sort(key=lambda m: (m["time"], m["path"]), reverse=True)
    return mails


def show_mail_list(mails: list[dict[str, str]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(mails))
        return
    for mail in mails:
        print(f"{mail['time']} {mail['from']} {mail['path']}")


def _group_alive(pgid: object) -> bool:
    """The recorded turn's process group still exists."""

    if not isinstance(pgid, int) or pgid <= 1:
        return False
    try:
        os.kill(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _sent_state(board: Path, recipient: str, relative: str, when: str) -> str:
    item = latest_line(board.parent, recipient, relative)
    latest = str(item["state"]) if item is not None else None
    record = load_record(board, recipient)
    running = (
        record["turn_started"] is not None
        and relative in record["inflight"]
        and _group_alive(record["pgid"])
    )
    stamp = when
    if item is not None and isinstance(item.get("time"), str):
        stamp = item["time"]
    return derived(latest, age=age_seconds(stamp), running=running)


def sent_rows() -> list[dict[str, str]]:
    """Mail this caller sent, newest first, each with a state read from the log."""

    sender = actor()
    board = board_root()
    mail_dir = board / "mail"
    rows: list[dict[str, str]] = []
    if mail_dir.is_symlink() or not mail_dir.is_dir():
        return rows
    mail_root = mail_dir.resolve()
    for directory in mail_root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        if not directory.resolve().is_relative_to(mail_root):
            continue
        recipient = directory.name
        for path in directory.glob("*.md"):
            named = mail_from(board, recipient, path.name)
            if named is None or path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(mail_root):
                continue
            when, who = named
            if who != sender:
                continue
            try:
                fields, _body = _fields(path.read_text(encoding="utf-8"), str(path))
            except (OSError, UnicodeDecodeError, FoilError):
                continue
            if fields.get("contract") != "mail/v1":
                continue
            relative = path.relative_to(board).as_posix()
            rows.append(
                {
                    "from": sender,
                    "to": recipient,
                    "time": when,
                    "path": str(resolved),
                    "state": _sent_state(board, recipient, relative, when),
                }
            )
    rows.sort(key=lambda item: (item["time"], item["path"]), reverse=True)
    return rows


def undelivered(board: Path | None = None) -> list[dict[str, str]]:
    """Stuck mail in this fleet, oldest first. ``foil inbox`` reads this."""

    root = board if board is not None else board_root()
    mail_dir = root / "mail"
    found: list[dict[str, str]] = []
    if mail_dir.is_symlink() or not mail_dir.is_dir():
        return found
    mail_root = mail_dir.resolve()
    for directory in mail_root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        recipient = directory.name
        for path in directory.glob("*.md"):
            named = mail_from(root, recipient, path.name)
            if named is None or path.is_symlink() or not path.is_file():
                continue
            when, who = named
            try:
                fields, _body = _fields(path.read_text(encoding="utf-8"), str(path))
            except (OSError, UnicodeDecodeError, FoilError):
                continue
            if fields.get("contract") != "mail/v1":
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            state = _sent_state(root, recipient, relative, when)
            if state != "stuck":
                continue
            found.append(
                {
                    "kind": "undelivered",
                    "path": relative,
                    "from": who,
                    "to": recipient,
                    "time": when,
                }
            )
    found.sort(key=lambda item: (item["time"], item["path"]))
    return found


def show_sent(rows: list[dict[str, str]], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows))
        return
    for row in rows:
        print(f"{row['time']} {row['to']} {row['state']} {row['path']}")


def mail_list(*, as_json: bool = False, sent: bool = False) -> None:
    if sent:
        show_sent(sent_rows(), as_json=as_json)
        return
    show_mail_list(mail_rows(), as_json=as_json)
