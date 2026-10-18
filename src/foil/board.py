"""Board directories, mail files, and room threads."""

from __future__ import annotations

import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path

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
        f"This directory has {count} files, and {count} does not refuse the write.\n"
    )


def _room_document(
    sender: str,
    when: str,
    body: str,
    nudged: list[str],
    parent: Path | None,
) -> str:
    lines = [
        "---",
        "contract: room/v1",
        f"from: {sender}",
        f"time: {when}",
    ]
    if parent is not None:
        lines.append(f"re: {parent.resolve()}")
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


def _wake_names(toplevel: Path) -> list[str]:
    registry = load_registry(toplevel)
    author = _author()
    fleet = str(registry["fleet_id"])
    session = str(registry.get("tmux_session") or "")
    controller = TmuxController()
    names: list[str] = []
    for name in sorted(registry["seats"]):
        if author is not None and name == author:
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


def _new_thread(toplevel: Path, sender: str, when: str, text: str, nudged: list[str]) -> Path:
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
        rendered = _room_document(sender, when, body, nudged, None)
        if _exclusive(path, rendered):
            return path
    raise FoilError("foil: could not write room")


def _child(
    directory: Path,
    sender: str,
    when: str,
    body_for,
    nudged: list[str],
    parent: Path,
) -> Path:
    private_dir(directory)
    token = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%dT%H%M%SZ")
    for _ in range(8):
        path = directory / f"{token}-{sender}-{secrets.token_hex(4)}.md"
        rendered = _room_document(sender, when, body_for(path), nudged, parent)
        if _exclusive(path, rendered):
            return path
    raise FoilError("foil: could not write room")


def post_room(
    toplevel: Path,
    text: str,
    *,
    reply: str | None = None,
    decline: bool = False,
) -> None:
    sender = actor()
    if decline:
        if not reply:
            raise FoilError("foil: --decline requires --re")
        if text.strip():
            raise FoilError("foil: --decline takes no text")
        parent = _room_parent(toplevel, reply)
        when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        _child(parent.parent, sender, when, lambda _path: "decline\n", [], parent)
        return
    if not text.strip():
        raise FoilError("foil: empty text")
    scan(text)
    parent = None if reply is None else _room_parent(toplevel, reply)
    names = _wake_names(toplevel)
    when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if parent is None:
        path = _new_thread(toplevel, sender, when, text, names)
    else:
        caller = _caller_text(text)

        def waking(path: Path, directory: Path = parent.parent, caller: str = caller) -> str:
            return _preamble(path, _listing(directory) + 1) + "\n" + caller

        path = _child(parent.parent, sender, when, waking, names, parent)
    _fan_out(toplevel, path, names)
