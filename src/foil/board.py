"""Board directories, mail files, and room threads."""

from __future__ import annotations

import os
import re
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

from foil.board_ops import split_front_matter
from foil.errors import FoilError, shown
from foil.store import (
    SAFE_ID,
    actor,
    create_exclusive,
    find_seat,
    fleet_dir,
    load_registry,
    private_dir,
    scan,
)

ROOM_CONTRACTS = ("room/v1", "room/v2")
_ROOM_ID = re.compile(r"^[0-9a-f]{16}$")
_ROOM_CHILD = re.compile(r"^\d{8}T\d{12}Z-[A-Za-z0-9][A-Za-z0-9._-]{0,127}-[0-9a-f]{8}\.md$")
_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_MAIL_NAME = re.compile(
    r"^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)(\d\d)Z-([A-Za-z0-9][A-Za-z0-9._-]{0,127})-[0-9a-f]{8}\.md$"
)


def mail_name(name: str) -> tuple[str, str] | None:
    """The time and sender of a mail file name that ``write_mail`` could have written."""

    found = _MAIL_NAME.match(name)
    if found is None:
        return None
    year, month, day, hour, minute, second, sender = found.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z", sender


def _foil_record(board: Path, mailbox: str, name: str) -> Path:
    return board.parent / "run" / "foil" / mailbox / name


def mail_from(board: Path, mailbox: str, name: str) -> tuple[str, str] | None:
    """``mail_name``, except that mail from ``foil`` needs Foil's record under ``run/`` (F14)."""

    named = mail_name(name)
    if named is None or named[1] != "foil":
        return named
    record = _foil_record(board, mailbox, name)
    if record.is_symlink() or not record.is_file():
        return None
    return named


def items_for(board: Path, name: str, skip: set[str]) -> list[dict[str, str]]:
    """Mail in ``name``'s mailbox and ``room/v2`` files whose ``wakes`` names it, minus ``skip``.

    Oldest first. The sender and time reach a prompt or a wake text, so neither is
    taken from text a hand-written file could choose.
    """

    if board.is_symlink() or not board.is_dir():
        return []
    candidates: list[tuple[Path, bool]] = []
    mailbox = board / "mail" / name
    if not (board / "mail").is_symlink() and not mailbox.is_symlink() and mailbox.is_dir():
        candidates += [(path, True) for path in mailbox.glob("*.md")]
    room = board / "room"
    if not room.is_symlink() and room.is_dir():
        for directory in room.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            if not _ROOM_ID.match(directory.name):
                continue
            candidates += [
                (path, False)
                for path in directory.glob("*.md")
                if path.name == f"{directory.name}.md" or _ROOM_CHILD.match(path.name)
            ]
    items = []
    for path, mail in candidates:
        relative = path.relative_to(board).as_posix()
        if relative in skip or path.is_symlink() or not path.is_file():
            continue
        if mail and mail_from(board, name, path.name) is None:
            continue
        try:
            fields, _ = split_front_matter(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if fields is None:
            continue
        if mail:
            if fields.get("contract") != "mail/v1":
                continue
            when, sender = mail_name(path.name)
        else:
            wakes = fields.get("wakes")
            if fields.get("contract") != "room/v2" or not isinstance(wakes, list):
                continue
            if name not in wakes:
                continue
            sender, when = str(fields.get("from", "")), str(fields.get("time", ""))
            if not SAFE_ID.fullmatch(sender) or not _TIME.match(when):
                continue
        items.append(
            {"time": when, "from": sender, "path": str(path.resolve()), "relative": relative}
        )
    items.sort(key=lambda item: (item["time"], item["path"]))
    return items


def notes_dir(toplevel: Path) -> Path:
    return fleet_dir(toplevel) / "board" / "notes"


def ensure_board(toplevel: Path) -> None:
    root = fleet_dir(toplevel) / "board"
    for name in ("mail", "notes", "tasks", "results", "room"):
        private_dir(root / name)


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


def write_mail(toplevel: Path, to: str, text: str, *, sender: str | None = None) -> Path:
    return write_mail_at(fleet_dir(toplevel) / "board", to, text, sender or actor(toplevel))


def write_mail_at(board: Path, to: str, text: str, sender: str) -> Path:
    if not SAFE_ID.fullmatch(to):
        raise FoilError(f"foil: unknown seat '{shown(to)}'")
    scan(text)
    when = datetime.now(UTC)
    rendered = _message(sender, to, when.strftime("%Y-%m-%dT%H:%M:%SZ"), text)
    scan(rendered)
    directory = board / "mail" / to
    private_dir(directory)
    token = when.strftime("%Y%m%dT%H%M%SZ")
    payload = rendered.encode("utf-8")
    for _ in range(8):
        path = directory / f"{token}-{sender}-{secrets.token_hex(4)}.md"
        if path.exists() or path.is_symlink():
            continue
        if sender == "foil":
            # Before the mail, so the mail never counts without it.
            record = _foil_record(board, to, path.name)
            private_dir(record.parent)
            try:
                create_exclusive(record, b"")
            except FileExistsError:
                continue
        try:
            create_exclusive(path, payload)
        except FileExistsError:
            continue
        return path
    raise FoilError("foil: could not write mail")


def send_mail(toplevel: Path, to: str, text: str) -> None:
    sender = actor(toplevel)
    if to == "user":
        # The human's mailbox. Only a launched lead writes it.
        if sender == "lead" and not os.environ.get("FOIL_SEAT_ID", "").strip():
            raise FoilError("foil: a joined lead asks in its own chat")
        if sender != "lead":
            raise FoilError("foil: only the lead may send to user")
        write_mail(toplevel, to, text, sender=sender)
        return
    if find_seat(toplevel, to) is None:
        raise FoilError(f"foil: unknown seat '{shown(to)}'")
    write_mail(toplevel, to, text, sender=sender)


def _listing(directory: Path) -> int:
    return sum(1 for _name in directory.iterdir())


def _preamble(path: Path, count: int) -> str:
    target = path.resolve()
    return (
        f"Reply with `foil post --re {target}` only to add a view the thread "
        "does not contain or to object to a close proposal. "
        f"Otherwise `foil post --re {target} --decline`. "
        "Do not open a new `foil post` to continue this thread. "
        "A reply wakes this room's members and the seats it names as @name, "
        "except a member whose last file here is a decline. "
        f"This directory has {count} files, and {count} does not refuse the write.\n"
    )


_CLOSED = "This room is closed. Foil refuses a reply here.\n"


def _room_document(
    sender: str,
    when: str,
    body: str,
    wakes: list[str],
    parent: Path | None,
    extra: tuple[str, ...] = (),
) -> str:
    lines = [
        "---",
        "contract: room/v2",
        f"from: {sender}",
        f"time: {when}",
    ]
    if parent is not None:
        lines.append(f"re: {parent.resolve()}")
    lines.extend(extra)
    lines.append("wakes: [" + ", ".join(wakes) + "]")
    lines.append("---")
    text = body if body.endswith("\n") else f"{body}\n"
    return "\n".join(lines) + "\n\n" + text


def _room_parent(toplevel: Path, text: str) -> Path:
    given = Path(text)
    if not given.is_absolute():
        raise FoilError("foil: room path must be absolute")
    if ".." in given.parts:
        raise FoilError("foil: path must not contain '..'")
    try:
        info = given.lstat()
    except OSError:
        raise FoilError("foil: file not found") from None
    if stat.S_ISLNK(info.st_mode):
        raise FoilError("foil: refusing symlink")
    if not stat.S_ISREG(info.st_mode):
        raise FoilError("foil: room path must be a file")
    room = (fleet_dir(toplevel) / "board" / "room").resolve()
    resolved = given.resolve()
    if not resolved.is_relative_to(room):
        raise FoilError("foil: room path must be under board/room")
    relative = resolved.relative_to(room)
    if len(relative.parts) != 2:
        raise FoilError("foil: room path must be under board/room")
    return resolved


_MENTION = re.compile(r"(?<![\w.@-])@([A-Za-z0-9][A-Za-z0-9._-]{0,127})")


def _mentions(text: str) -> set[str]:
    found = set()
    for match in _MENTION.finditer(text):
        found |= {match.group(1), match.group(1).rstrip("._-")}
    return found


def room_state(room: str, files: list[tuple[str, str]]) -> dict:
    """A room's members, whether it is closed, its purpose, and who declined last.

    ``files`` are (file name, text) pairs from one ``board/room/<room>/``. Only
    ``room/v1`` and ``room/v2`` files count, in time order with the room's first file first.
    The newest ``members`` list wins, and one ``closed: true`` closes the room.
    """

    posts = []
    for name, text in files:
        fields, body = split_front_matter(text)
        if fields and fields.get("contract") in ROOM_CONTRACTS:
            posts.append((str(fields.get("time", "")), name != f"{room}.md", name, fields, body))
    posts.sort(key=lambda post: post[:3])
    state: dict = {"members": [], "closed": False, "purpose": "", "declined": set()}
    for _time, later, _name, fields, body in posts:
        if not later:
            text = body.partition("\n\n")[2] or body
            state["purpose"] = next((line for line in text.splitlines() if line.strip()), "")
        if isinstance(fields.get("members"), list):
            state["members"] = [str(name) for name in fields["members"]]
        if fields.get("closed") == "true":
            state["closed"] = True
        author = str(fields.get("from", ""))
        state["declined"].discard(author)
        if body.strip() == "decline":
            state["declined"].add(author)
    return state


def _read_room(directory: Path) -> dict:
    files = []
    for path in directory.glob("*.md"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            files.append((path.name, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    return room_state(directory.name, files)


def _member_list(toplevel: Path, text: str) -> set[str]:
    names = {part.strip() for part in text.split(",") if part.strip()}
    if not names:
        raise FoilError("foil: --members names no seat")
    seats = load_registry(toplevel)["seats"]
    for name in sorted(names):
        if not SAFE_ID.fullmatch(name) or (name not in seats and name != "user"):
            raise FoilError(f"foil: unknown seat '{shown(name)}'")
    return names


def _wake_names(toplevel: Path, only: set[str] | None = None) -> list[str]:
    """Seats a post wakes: not killed, not the caller. Dead and paused seats read it next turn."""

    seats = load_registry(toplevel)["seats"]
    author = actor(toplevel)
    return [
        name
        for name in sorted(seats)
        if not (name == author and author != "user")
        and (only is None or name in only)
        and seats[name].get("state") != "killed"
    ]


def _caller_text(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def _exclusive(path: Path, rendered: str) -> bool:
    scan(rendered)
    if path.exists() or path.is_symlink():
        return False
    try:
        create_exclusive(path, rendered.encode())
    except FileExistsError:
        return False
    return True


def _new_thread(
    toplevel: Path, sender: str, when: str, text: str, wakes: list[str], extra: tuple[str, ...]
) -> Path:
    room = fleet_dir(toplevel) / "board" / "room"
    private_dir(room)
    caller = _caller_text(text)
    for _ in range(8):
        thread = secrets.token_hex(8)
        if not SAFE_ID.fullmatch(thread):
            continue
        directory = room / thread
        if directory.exists() or directory.is_symlink():
            continue
        private_dir(directory)
        path = directory / f"{thread}.md"
        body = _preamble(path, _listing(directory) + 1) + "\n" + caller
        rendered = _room_document(sender, when, body, wakes, None, extra)
        if _exclusive(path, rendered):
            return path
    raise FoilError("foil: could not write room")


def _child(
    directory: Path,
    sender: str,
    now: datetime,
    body_for,
    wakes: list[str],
    parent: Path,
    extra: tuple[str, ...] = (),
) -> Path:
    private_dir(directory)
    # `time` and the name come from one clock reading. room_state sorts by `time`,
    # then by name, so the microseconds put posts in one second in write order.
    when = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    token = now.strftime("%Y%m%dT%H%M%S%fZ")
    for _ in range(8):
        path = directory / f"{token}-{sender}-{secrets.token_hex(4)}.md"
        rendered = _room_document(sender, when, body_for(path), wakes, parent, extra)
        if _exclusive(path, rendered):
            return path
    raise FoilError("foil: could not write room")


def _open_room(toplevel: Path, reply: str) -> tuple[Path, dict]:
    parent = _room_parent(toplevel, reply)
    state = _read_room(parent.parent)
    if state["closed"]:
        raise FoilError("foil: room is closed")
    return parent, state


def post_room(
    toplevel: Path,
    text: str,
    *,
    reply: str | None = None,
    decline: bool = False,
    members: str | None = None,
    close: bool = False,
) -> None:
    sender = actor(toplevel)
    if decline:
        if not reply:
            raise FoilError("foil: --decline requires --re")
        if text.strip():
            raise FoilError("foil: --decline takes no text")
        if members is not None or close:
            raise FoilError("foil: --decline takes no --members or --close")
        parent, _state = _open_room(toplevel, reply)
        _child(parent.parent, sender, datetime.now(UTC), lambda _path: "decline\n", [], parent)
        return
    # The lead and the human scope the rooms. A worker replies in its rooms.
    steering = sender in {"lead", "user"}
    if reply is None and not steering:
        raise FoilError("foil: only the lead opens a room")
    if (members is not None or close) and not steering:
        raise FoilError("foil: only the lead sets members or closes a room")
    if close and (reply is None or members is not None):
        raise FoilError("foil: --close needs --re and takes no --members")
    if not text.strip():
        raise FoilError("foil: empty text")
    scan(text)
    given = None if members is None else _member_list(toplevel, members) | {sender}
    named = _mentions(text)
    extra: tuple[str, ...] = ()
    if given is not None:
        extra = ("members: [" + ", ".join(sorted(given)) + "]",)
    if reply is None:
        when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        names = _wake_names(toplevel, None if given is None else given | named)
        if given is None:
            extra = ("members: [" + ", ".join(sorted({*names, sender})) + "]",)
        _new_thread(toplevel, sender, when, text, names, extra)
        return
    parent, state = _open_room(toplevel, reply)
    current = set(state["members"])
    if close:
        wake = named
        extra = ("closed: true",)
    elif given is not None:
        wake = (given - current) | named
    else:
        wake = (current - state["declined"]) | named
    names = _wake_names(toplevel, wake)
    caller = _caller_text(text)

    def waking(path: Path, directory: Path = parent.parent) -> str:
        if close:
            return _CLOSED + "\n" + caller
        return _preamble(path, _listing(directory) + 1) + "\n" + caller

    _child(parent.parent, sender, datetime.now(UTC), waking, names, parent, extra)
