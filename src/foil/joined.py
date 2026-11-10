"""The joined lead: `seat join`, `seat leave`, `mail wait`, and the new-items line (F41, F42)."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

from foil.board import write_mail
from foil.board_ops import split_front_matter
from foil.errors import FoilError
from foil.lifecycle import _state, _write_instruction
from foil.presets import load_template
from foil.store import (
    SAFE_ID,
    SCHEMA_VERSION,
    SEAT_FIELDS,
    actor,
    ensure_registry,
    exclusive_lock,
    fleet_dir,
    load_registry,
    private_dir,
    read_json,
    update_registry,
    write_json,
)
from foil.tmux import TmuxController, TmuxError

SANDBOX = "foil: run foil outside your harness sandbox and without a prompt"
DEAD_EVERY = 30
COALESCE = 2
WAKE_LIMIT = 10
BRIEF_ITEMS = 10
_MAIL_NAME = re.compile(r"^\d{8}T\d{6}Z-[A-Za-z0-9][A-Za-z0-9._-]{0,127}-[0-9a-f]{8}\.md$")
# Checked in this order (F42).
_HARNESS_ENV = (
    ("codex", "CODEX_THREAD_ID"),
    ("claude", "CLAUDE_CODE_SESSION_ID"),
    ("grok", "GROK_SESSION_ID"),
)


def _outside() -> bool:
    return not os.environ.get("FOIL_SEAT_ID", "").strip()


def _record_path(root: Path, name: str) -> Path:
    return fleet_dir(root) / "run" / "seats" / f"{name}.json"


def _load(root: Path, name: str) -> dict:
    path = _record_path(root, name)
    try:
        payload = read_json(path)
    except FoilError as exc:
        if str(exc) != "foil: missing record":
            raise
        payload = {}
    seen = payload.get("seen", [])
    alive = payload.get("alive", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "seen": [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else [],
        "quiet": payload.get("quiet", 0) if type(payload.get("quiet")) is int else 0,
        "alive": [item for item in alive if isinstance(item, str)]
        if isinstance(alive, list)
        else [],
        "checked": payload.get("checked", 0.0)
        if isinstance(payload.get("checked"), int | float)
        else 0.0,
        "joined": payload.get("joined", 0.0)
        if isinstance(payload.get("joined"), int | float)
        else 0.0,
    }


def _save(root: Path, name: str, record: dict) -> None:
    board = fleet_dir(root) / "board"
    # A file that is gone cannot be new again, so it need not stay seen.
    record["seen"] = sorted(item for item in set(record["seen"]) if (board / item).exists())
    path = _record_path(root, name)
    private_dir(path.parent)
    write_json(path, record)


def _lock(root: Path, name: str):
    return exclusive_lock(_record_path(root, name).with_suffix(".lock"))


def _fields(path: Path) -> dict | None:
    try:
        fields, _ = split_front_matter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return fields


def _new_items(root: Path, name: str, seen: set[str]) -> list[dict[str, str]]:
    """Unseen mail in ``name``'s mailbox and room files whose ``nudged`` names it."""

    board = fleet_dir(root) / "board"
    if board.is_symlink() or not board.is_dir():
        return []
    items = []
    candidates: list[tuple[Path, bool]] = []
    mailbox = board / "mail" / name
    if not (board / "mail").is_symlink() and not mailbox.is_symlink() and mailbox.is_dir():
        candidates += [(path, True) for path in mailbox.glob("*.md") if _MAIL_NAME.match(path.name)]
    room = board / "room"
    if not room.is_symlink() and room.is_dir():
        for directory in room.iterdir():
            if directory.is_symlink() or not directory.is_dir():
                continue
            candidates += [(path, False) for path in directory.glob("*.md")]
    for path, mail in candidates:
        relative = path.relative_to(board).as_posix()
        if relative in seen or path.is_symlink() or not path.is_file():
            continue
        fields = _fields(path)
        if fields is None:
            continue
        if mail:
            if fields.get("contract") != "mail/v1":
                continue
        else:
            nudged = fields.get("nudged")
            if fields.get("contract") != "room/v1" or not isinstance(nudged, list):
                continue
            if name not in nudged:
                continue
        items.append(
            {
                "time": str(fields.get("time", "")),
                "from": str(fields.get("from", "")),
                "path": str(path.resolve()),
                "relative": relative,
            }
        )
    items.sort(key=lambda item: (item["time"], item["path"]))
    return items


def _line(item: dict[str, str]) -> str:
    return f"{item['time']} {item['from']} {item['path']}"


def _states_now(root: Path) -> dict[str, str]:
    registry = load_registry(root)
    return {
        name: _state(registry, name, record) for name, record in registry["seats"].items()
    }


def _dead_check(root: Path, name: str, record: dict, *, force: bool = False) -> None:
    """Mail ``name`` from ``foil`` for each seat alive at the last check and dead now.

    A seat killed or removed since the last check was stopped on purpose, so it gets no mail.
    """

    now = time.time()
    if not force and now - record["checked"] < DEAD_EVERY:
        return
    states = _states_now(root)
    alive = {seat for seat, state in states.items() if state == "alive"}
    for seat in sorted(set(record["alive"])):
        if states.get(seat) != "dead":
            continue
        write_mail(
            root,
            name,
            f"{seat} died. See foil seat peek {seat} --lines 200.",
            sender="foil",
        )
    record["alive"] = sorted(alive)
    record["checked"] = now


def _harness(given: str | None, session: str | None) -> tuple[str, str]:
    harness, found = "", ""
    for preset, variable in _HARNESS_ENV:
        value = os.environ.get(variable, "").strip()
        if value:
            harness, found = preset, value
            break
    harness = given if given is not None else harness
    session = session if session is not None else found
    for value in (harness, session):
        if value and not SAFE_ID.fullmatch(value):
            raise FoilError("foil: invalid id")
    return harness, session


def _preflight(root: Path) -> None:
    run = fleet_dir(root) / "run"
    probe = run / f".join-{os.getpid()}"
    try:
        private_dir(run)
        probe.write_bytes(b"")
        probe.unlink()
        TmuxController().start_server()
    except (OSError, FoilError, TmuxError):
        raise FoilError(SANDBOX) from None


def _route(harness: str) -> str:
    if harness == "codex":
        return (
            "Run this in a background terminal and re-arm it after each wake: "
            "foil mail wait --wake 'codex queue --thread \"$CODEX_THREAD_ID\" "
            "--message \"$FOIL_WAKE_TEXT\" --resume'"
        )
    if harness in ("claude", "grok"):
        return (
            "Run `foil mail wait` as a background command. Its exit wakes you. "
            "Re-arm it after each wake, and stop after three quiet timeouts in a row."
        )
    return "Run `foil mail wait --timeout 0` at the start of each turn."


def _dirty(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def join_lead(root: Path, *, harness: str | None, session: str | None, take: bool) -> None:
    if not _outside():
        raise FoilError("foil: not allowed")
    harness, session = _harness(harness, session)
    _preflight(root)
    template = load_template(root, "lead")
    ensure_registry(root)

    def apply(fresh: dict) -> None:
        existing = fresh["seats"].get("lead")
        state = (existing or {}).get("state", "")
        if existing and state == "joined":
            if not take:
                raise FoilError("foil: lead is joined. Pass --take to take it")
        elif existing and state != "killed":
            shown = _state(fresh, "lead", existing)
            raise FoilError(f"foil: lead is {shown}. Run foil seat kill lead first")
        record = {key: "" for key in SEAT_FIELDS}
        record.update(
            {
                "name": "lead",
                "template": "lead",
                "harness": harness,
                "state": "joined",
                "session_id": session,
            }
        )
        fresh["seats"]["lead"] = record
        fresh["lead"] = "lead"

    update_registry(root, apply)
    instruction = _write_instruction(root, "lead", template, restarted=False, joined=True)
    with _lock(root, "lead"):
        record = _load(root, "lead")
        record["joined"] = time.time()
        record["quiet"] = 0
        _dead_check(root, "lead", record, force=True)
        items = _new_items(root, "lead", set(record["seen"]))
        record["seen"] += [item["relative"] for item in items]
        _save(root, "lead", record)
    registry = load_registry(root)
    lines = [
        f"You are seat lead in fleet {registry['fleet_id']} ({root.resolve()}).",
        f"Read first: {instruction}",
    ]
    if items:
        lines.append(f"New for lead ({len(items)}):")
        lines += [_line(item) for item in items[:BRIEF_ITEMS]]
        if len(items) > BRIEF_ITEMS:
            lines.append(f"... and {len(items) - BRIEF_ITEMS} more. Run foil mail list.")
    else:
        lines.append("New for lead: none.")
    lines.append(f"Hear from the fleet: {_route(harness)}")
    if _dirty(root):
        lines.append(
            "Your checkout has uncommitted changes. Workers branch from HEAD and do not see "
            "them. Commit or set them aside before you spawn."
        )
    print("\n".join(lines))


def leave_lead(root: Path) -> None:
    if not _outside():
        raise FoilError("foil: not allowed")
    registry = load_registry(root)
    record = registry["seats"].get("lead")
    if not record or record.get("state") != "joined":
        raise FoilError("foil: no joined lead")
    handoff = fleet_dir(root) / "board" / "handoffs" / "lead.md"
    joined_at = _load(root, "lead")["joined"]
    try:
        info = handoff.lstat()
    except OSError:
        info = None
    if (
        info is None
        or handoff.is_symlink()
        or not handoff.is_file()
        or info.st_mtime < joined_at
    ):
        raise FoilError("foil: write board/handoffs/lead.md first")

    def apply(fresh: dict) -> None:
        current = fresh["seats"].get("lead")
        if not current or current.get("state") != "joined":
            raise FoilError("foil: no joined lead")
        current["state"] = "killed"

    update_registry(root, apply)
    print("lead left. Mail to lead queues until a lead joins or is spawned.")


def _wake(command: str, items: list[dict[str, str]]) -> None:
    text = "; ".join(f"{item['from']} {item['path']}" for item in items)
    environment = dict(os.environ, FOIL_WAKE_TEXT=f"foil: {len(items)} new: {text}")
    try:
        child = subprocess.Popen(
            ["sh", "-c", command],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
    except OSError:
        return
    try:
        child.wait(timeout=WAKE_LIMIT)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def mail_wait(root: Path, *, timeout: int, wake: str | None) -> None:
    if not _outside():
        raise FoilError("foil: mail wait is outside the fleet only")
    if timeout < 0:
        raise FoilError("foil: invalid timeout")
    name = actor(root)
    deadline = time.monotonic() + timeout
    first = True
    quiet = 0
    found_at: float | None = None
    while True:
        with _lock(root, name):
            record = _load(root, name)
            _dead_check(root, name, record)
            items = _new_items(root, name, set(record["seen"]))
            ready = bool(items) and (
                first or (found_at is not None and time.monotonic() - found_at >= COALESCE)
            )
            expired = time.monotonic() >= deadline
            if ready or (items and expired):
                record["seen"] += [item["relative"] for item in items]
                record["quiet"] = 0
                _save(root, name, record)
                break
            if not items and expired:
                record["quiet"] += 1
                _save(root, name, record)
                quiet = record["quiet"]
                break
            _save(root, name, record)
        if items and found_at is None:
            found_at = time.monotonic()
        first = False
        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
    if not items:
        print("no new mail" if quiet < 2 else f"no new mail ({quiet} quiet waits in a row)")
        return
    for item in items:
        print(_line(item))
    sys.stdout.flush()
    if wake:
        _wake(wake, items)


def notice(root: Path) -> None:
    """The stderr line a joined lead's other commands end with (F41)."""

    if not _outside() or actor(root) != "lead":
        return
    with _lock(root, "lead"):
        record = _load(root, "lead")
        checked = record["checked"]
        _dead_check(root, "lead", record)
        if record["checked"] != checked:
            _save(root, "lead", record)
        count = len(_new_items(root, "lead", set(record["seen"])))
    if count:
        print(f"foil: {count} new for lead. Run foil mail wait --timeout 0.", file=sys.stderr)

