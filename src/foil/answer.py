"""Answer one closed question. The mail quotes the question id (section 12)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from foil.board import mail_name, send_mail
from foil.board_ops import split_front_matter
from foil.errors import FoilError, shown
from foil.inbox import _questions, _status, projects
from foil.store import SAFE_ID, actor, fleet_dir, fleet_ids, in_seat


def answer_question(root: Path, question_id: str, text: str) -> None:
    """Mail ``lead`` as ``user``. The first line is ``question: ID``."""

    if os.environ.get("FOIL_MASTER") == "1" and not in_seat():
        raise FoilError("foil: the master does not answer")
    if actor(root) != "user":
        raise FoilError("foil: answer is from user")
    if not SAFE_ID.fullmatch(question_id):
        raise FoilError(f"foil: unknown question '{shown(question_id)}'")
    body = text.strip()
    if not body:
        raise FoilError("foil: answer text is empty")
    questions = _open(root)
    match = next((item for item in questions if item["id"] == question_id), None)
    if match is None:
        raise FoilError(f"foil: unknown question '{shown(question_id)}'")
    if match["from"] == "master":
        raise FoilError("foil: that question is for the master")
    send_mail(root, "lead", f"question: {question_id}\n\n{body}\n")


def _open(root: Path) -> list[dict[str, Any]]:
    board = fleet_dir(root) / "board"
    fields, _body, raw = _status(board)
    parsed = _questions(fields, raw)
    return parsed or []


def quoted_answer(board: Path, question_id: str) -> str | None:
    """The newest mail from ``user`` whose first line quotes ``question_id``.

    None means no such mail. An empty string is a quote with no further text.
    """

    directory = board / "mail" / "lead"
    if directory.is_symlink() or not directory.is_dir():
        return None
    newest_name = ""
    newest: str | None = None
    prefix = f"question: {question_id}"
    for path in directory.glob("*.md"):
        named = mail_name(path.name)
        if named is None or named[1] != "user" or path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fields, mail_body = split_front_matter(text)
        if fields is None or fields.get("contract") != "mail/v1":
            continue
        content = mail_body.splitlines()
        filled = [line for line in content if line.strip()]
        if not filled or filled[0] != prefix:
            continue
        if path.name <= newest_name:
            continue
        newest_name = path.name
        rest = content[content.index(prefix) + 1 :]
        newest = "\n".join(rest).strip()
    return newest


def question_cards() -> list[dict[str, Any]]:
    """Open questions for the human, and whether mail from ``user`` already quotes them."""

    found = []
    for key, root in projects():
        for fleet in fleet_ids(root):
            board = fleet_dir(root, fleet) / "board"
            fields, _body, raw = _status(board)
            parsed = _questions(fields, raw)
            if not parsed:
                continue
            for question in parsed:
                if question["from"] == "master":
                    continue
                answer = quoted_answer(board, question["id"])
                found.append(
                    {
                        "project": key,
                        "fleet": fleet,
                        "id": question["id"],
                        "ask": question["title"],
                        "options": question["options"],
                        "recommended": question["recommended"],
                        "why": question["why"],
                        "evidence": question["evidence"],
                        "state": "answered" if answer is not None else "open",
                        "answer": answer or "",
                    }
                )
    return found
