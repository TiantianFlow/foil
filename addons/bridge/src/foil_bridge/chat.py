"""A chat with one seat: mail from the human, and the delivery state of each message.

Nothing here is kept. Sending calls ``foil.api`` as ``user``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from foil.answer import question_cards
from foil.api import send
from foil.board import mail_name
from foil.board_ops import split_front_matter
from foil.errors import FoilError, shown
from foil.mail_ops import sent_state
from foil.project import checkout_for_key
from foil.store import FLEET_ID, RESERVED, SAFE_ID, fleet_dir, load_registry
from foil_bridge.home import links_in

WAITING = "lands when its session is waiting"
_BODY_CAP = 8 * 1024
_TEXT_CAP = 16 * 1024
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,200}")


def _cap(text: str) -> str:
    raw = text.encode()
    if len(raw) <= _BODY_CAP:
        return text
    return raw[:_BODY_CAP].decode(errors="ignore") + "\n... truncated\n"


def _checkout(project: str, fleet: str, seat: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(project, str) or not _PROJECT.fullmatch(project):
        raise FoilError(f"foil: unknown project '{shown(project)}'")
    if not isinstance(fleet, str) or not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    if not isinstance(seat, str) or not SAFE_ID.fullmatch(seat) or seat in RESERVED:
        raise FoilError(f"foil: unknown seat '{shown(seat)}'")
    checkout = checkout_for_key(project)
    registry = load_registry(checkout, fleet)
    row = registry["seats"].get(seat)
    if not isinstance(row, dict):
        raise FoilError(f"foil: unknown seat '{shown(seat)}'")
    return checkout, row


def _mail(board: Path, mailbox: str, sender: str) -> list[tuple[str, str, str]]:
    """Mail files `mail list --sent` would see. A symlink is not followed."""

    mail_dir = board / "mail"
    if mail_dir.is_symlink() or not mail_dir.is_dir():
        return []
    mail_root = mail_dir.resolve()
    directory = mail_root / mailbox
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or not directory.resolve().is_relative_to(mail_root)
    ):
        return []
    found: list[tuple[str, str, str, Path]] = []
    for path in directory.glob("*.md"):
        named = mail_name(path.name)
        if named is None or named[1] != sender or path.is_symlink() or not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(mail_root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fields, body = split_front_matter(text)
        if fields is None or fields.get("contract") != "mail/v1":
            continue
        found.append((named[0], path.name, body))
    return found


def _label(board: Path, seat: str, name: str, when: str, joined: bool) -> str:
    # The delivery log keys the file as mail/<seat>/<name>, under the resolved board.
    state = sent_state(board, seat, f"mail/{seat}/{name}", when)
    if joined and state not in {"delivered", "skipped"}:
        return WAITING
    return state


def thread(project: str, fleet: str, seat: str) -> dict[str, Any]:
    """Mail between ``user`` and this seat, plus that fleet's questions when it is the lead."""

    checkout, row = _checkout(project, fleet, seat)
    board = fleet_dir(checkout, fleet) / "board"
    joined = row.get("state") == "joined"
    messages = []
    for when, name, body in _mail(board, seat, "user"):
        messages.append(
            {
                "id": name,
                "time": when,
                "from": "user",
                "body": _cap(body.strip()),
                "state": _label(board, seat, name, when, joined),
            }
        )
    for when, name, body in _mail(board, "user", seat):
        messages.append(
            {
                "id": name,
                "time": when,
                "from": seat,
                "body": _cap(body.strip()),
                "state": "",
            }
        )
    messages.sort(key=lambda item: (item["time"], item["id"]))
    questions = []
    if seat == "lead":
        for card in question_cards():
            if card["project"] != project or card["fleet"] != fleet:
                continue
            evidence = card.get("evidence") or ""
            card = dict(card)
            card["links"] = []
            short = isinstance(evidence, str) and evidence and "\n" not in evidence
            if short and len(evidence) <= 240:
                linked = links_in(evidence, project=project, fleet=fleet)
                card["links"] = linked or links_in(evidence)
            questions.append(card)
    return {
        "seat": seat,
        "joined": joined,
        "notice": WAITING if joined else "",
        "messages": messages,
        "questions": questions,
    }


def post(project: str, fleet: str, seat: str, text: str) -> None:
    """Mail this seat as ``user``. A worker's lead gets one pointer, and nobody is woken for it."""

    if not isinstance(text, str) or len(text.encode()) > _TEXT_CAP:
        raise FoilError("foil: message text is too long")
    if not text.strip():
        raise FoilError("foil: message text is empty")
    checkout, _row = _checkout(project, fleet, seat)
    send(checkout, fleet, "user", seat, text)
