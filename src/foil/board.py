"""Board directories and mail files."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

from foil.errors import FoilError
from foil.project import foil_root
from foil.store import SAFE_ID, actor, create_exclusive, find_seat, private_dir, scan


def notes_dir(toplevel: Path) -> Path:
    return foil_root(toplevel) / "board" / "notes"


def ensure_board(toplevel: Path) -> None:
    root = foil_root(toplevel) / "board"
    for name in ("mail", "notes", "tasks", "results"):
        private_dir(root / name)


def nudge(window_id: str, sender: str, mail_path: Path) -> None:
    del window_id, sender, mail_path


def _message(sender: str, to: str, when: str, text: str) -> str:
    body = text if text.endswith("\n") else f"{text}\n"
    return (
        "---\n"
        "contract: mail/v1\n"
        f"from: {sender}\n"
        f"to: {to}\n"
        f"time: {when}\n"
        "---\n"
        "\n"
        f"{body}"
    )


def send_mail(toplevel: Path, to: str, text: str) -> None:
    shown = to.replace("\n", "").replace("\r", "")
    if not SAFE_ID.fullmatch(to):
        raise FoilError(f"foil: unknown seat '{shown}'")
    seat = find_seat(toplevel, to)
    if seat is None:
        raise FoilError(f"foil: unknown seat '{shown}'")
    scan(text)
    sender = actor()
    when = datetime.now(UTC)
    rendered = _message(sender, to, when.strftime("%Y-%m-%dT%H:%M:%SZ"), text)
    scan(rendered)
    directory = foil_root(toplevel) / "board" / "mail" / to
    private_dir(directory)
    token = when.strftime("%Y%m%dT%H%M%SZ")
    payload = rendered.encode("utf-8")
    for _ in range(8):
        path = directory / f"{token}-{sender}-{secrets.token_hex(4)}.md"
        if path.exists() or path.is_symlink():
            continue
        try:
            create_exclusive(path, payload)
        except FileExistsError:
            continue
        if seat["window_id"]:
            nudge(seat["window_id"], sender, path.resolve())
        return
    raise FoilError("foil: could not write mail")
