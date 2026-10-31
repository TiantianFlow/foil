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
    write_bytes,
)
from foil.tmux import TmuxController, TmuxError


def notes_dir(toplevel: Path) -> Path:
    return fleet_dir(toplevel) / "board" / "notes"


def ensure_board(toplevel: Path) -> None:
    root = fleet_dir(toplevel) / "board"
    for name in ("mail", "notes", "tasks", "results", "room"):
        private_dir(root / name)


def nudge(
    fleet_id: str,
    seat: str,
    session: str,
    window_id: str,
    sender: str,
    mail_path: Path,
) -> bool:
    try:
        controller = TmuxController()
        if not controller.matches_window(fleet_id, seat, session, window_id):
            return False
        return controller.nudge(window_id, f"{sender} {mail_path}")
    except TmuxError:
        return False


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


def write_mail(toplevel: Path, to: str, text: str) -> Path:
    if not SAFE_ID.fullmatch(to):
        raise FoilError(f"foil: unknown seat '{shown(to)}'")
    scan(text)
    sender = actor()
    when = datetime.now(UTC)
    rendered = _message(sender, to, when.strftime("%Y-%m-%dT%H:%M:%SZ"), text)
    scan(rendered)
    directory = fleet_dir(toplevel) / "board" / "mail" / to
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
        return path
    raise FoilError("foil: could not write mail")


def send_mail(toplevel: Path, to: str, text: str) -> None:
    if to == "user":
        # The human's mailbox. Only the lead writes it, and it has no pane to nudge.
        if actor() != "lead":
            raise FoilError("foil: only the lead may send to user")
        write_mail(toplevel, to, text)
        return
    seat = find_seat(toplevel, to)
    if seat is None:
        raise FoilError(f"foil: unknown seat '{shown(to)}'")
    path = write_mail(toplevel, to, text)
    if not seat["window_id"]:
        return
    registry = load_registry(toplevel)
    nudge(
        str(registry["fleet_id"]),
        to,
        str(registry.get("tmux_session") or ""),
        seat["window_id"],
        actor(),
        path.resolve(),
    )


def _author() -> str | None:
    seat = os.environ.get("FOIL_SEAT_ID", "").strip()
    return seat or None


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
    nudged: list[str],
    parent: Path | None,
    extra: tuple[str, ...] = (),
) -> str:
    lines = [
        "---",
        "contract: room/v1",
        f"from: {sender}",
        f"time: {when}",
    ]
    if parent is not None:
        lines.append(f"re: {parent.resolve()}")
    lines.extend(extra)
    lines.append("nudged: [" + ", ".join(nudged) + "]")
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
    ``room/v1`` files count, in time order with the room's first file first.
    The newest ``members`` list wins, and one ``closed: true`` closes the room.
    """

    posts = []
    for name, text in files:
        fields, body = split_front_matter(text)
        if fields and fields.get("contract") == "room/v1":
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
    registry = load_registry(toplevel)
    author = _author()
    fleet = str(registry["fleet_id"])
    session = str(registry.get("tmux_session") or "")
    controller = TmuxController()
    names: list[str] = []
    for name in sorted(registry["seats"]):
        if (author is not None and name == author) or (only is not None and name not in only):
            continue
        seat = registry["seats"][name]
        # Spawn leaves state empty. Alive is the window check below, the same
        # one mail uses. A stored dead or killed seat is omitted either way.
        if seat.get("state") in {"dead", "killed"}:
            continue
        window = seat.get("window_id") or ""
        if not window:
            continue
        try:
            passed = controller.matches_window(fleet, name, session, window)
        except TmuxError:
            passed = False
        if passed:
            names.append(name)
    return names


def _rewrite_nudged(path: Path, names: list[str]) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    fresh = "nudged: [" + ", ".join(names) + "]"
    for index, line in enumerate(lines):
        if line.startswith("nudged:"):
            lines[index] = fresh
            break
    rendered = "\n".join(lines)
    scan(rendered)
    write_bytes(path, rendered.encode())


def _fan_out(toplevel: Path, path: Path, planned: list[str]) -> None:
    if not planned:
        return
    registry = load_registry(toplevel)
    sender = actor()
    fleet = str(registry["fleet_id"])
    session = str(registry.get("tmux_session") or "")
    typed = [
        name
        for name in planned
        if nudge(
            fleet,
            name,
            session,
            registry["seats"][name]["window_id"],
            sender,
            path.resolve(),
        )
    ]
    if typed != planned:
        _rewrite_nudged(path, typed)


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
    toplevel: Path, sender: str, when: str, text: str, nudged: list[str], extra: tuple[str, ...]
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
        rendered = _room_document(sender, when, body, nudged, None, extra)
        if _exclusive(path, rendered):
            return path
    raise FoilError("foil: could not write room")


def _child(
    directory: Path,
    sender: str,
    now: datetime,
    body_for,
    nudged: list[str],
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
        rendered = _room_document(sender, when, body_for(path), nudged, parent, extra)
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
    sender = actor()
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
        names = _wake_names(toplevel, given)
        if given is None:
            extra = ("members: [" + ", ".join(sorted({*names, sender})) + "]",)
        path = _new_thread(toplevel, sender, when, text, names, extra)
        _fan_out(toplevel, path, names)
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

    path = _child(parent.parent, sender, datetime.now(UTC), waking, names, parent, extra)
    _fan_out(toplevel, path, names)
