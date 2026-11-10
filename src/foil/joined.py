"""The joined lead: `seat join`, `seat leave`, `mail wait`, and the new-items line (F41, F42)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from foil.board import items_for, write_mail
from foil.errors import FoilError
from foil.lifecycle import _state, _write_instruction, handoff_on_board
from foil.presets import load_preset, load_template
from foil.store import (
    SAFE_ID,
    SCHEMA_VERSION,
    SEAT_FIELDS,
    actor,
    ensure_registry,
    exclusive_lock,
    fleet_dir,
    fleet_ids,
    in_fleet,
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
BRIEF_BYTES = 2048
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
    reported = payload.get("reported", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "seen": [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else [],
        "quiet": payload.get("quiet", 0) if type(payload.get("quiet")) is int else 0,
        "reported": [item for item in reported if isinstance(item, str)]
        if isinstance(reported, list)
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


def _new_items(root: Path, name: str, seen: set[str]) -> list[dict[str, str]]:
    return items_for(fleet_dir(root) / "board", name, seen)


def _line(item: dict[str, str]) -> str:
    return f"{item['time']} {item['from']} {item['path']}"


def _states_now(root: Path) -> dict[str, str]:
    registry = load_registry(root)
    return {
        name: _state(registry, name, record) for name, record in registry["seats"].items()
    }


def _dead_check(root: Path, name: str, record: dict, *, force: bool = False) -> None:
    """Mail ``name`` from ``foil`` once for each seat that is dead now.

    A seat is reported again only after a check sees it alive. Killed, joined, and removed
    seats were stopped on purpose, so they get no mail.
    """

    now = time.time()
    if not force and now - record["checked"] < DEAD_EVERY:
        return
    states = _states_now(root)
    reported = {seat for seat in record["reported"] if states.get(seat) == "dead"}
    for seat in sorted(seat for seat, state in states.items() if state == "dead"):
        if seat in reported:
            continue
        write_mail(
            root,
            name,
            f"{seat} died. See foil seat peek {seat} --lines 200.",
            sender="foil",
        )
        reported.add(seat)
    record["reported"] = sorted(reported)
    record["checked"] = now


def _harness(given: str | None, session: str | None) -> tuple[str, str]:
    detected, found = "", ""
    for preset, variable in _HARNESS_ENV:
        value = os.environ.get(variable, "").strip()
        if value:
            detected, found = preset, value
            break
    harness = given if given is not None else detected
    # Another harness's session id would be wrong for this one (F42).
    if session is None:
        session = found if harness == detected else ""
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


def _wake_argv(root: Path, harness: str, session: str) -> list[str] | None:
    """The preset's wake argv, read from the global layer and the package only (F41)."""

    if not harness:
        return None
    try:
        argv = load_preset(root, harness, global_only=True).get("wake")
    except FoilError:
        return None
    if not argv or ("{session_id}" in argv and not session):
        return None
    return argv


def _route(root: Path, harness: str, session: str) -> str:
    rearm = (
        "On each wake, run `foil mail wait --timeout 0` and act on it, then re-arm. "
        "Stop after three quiet timeouts in a row."
    )
    if _wake_argv(root, harness, session):
        return f"Run `foil mail wait --wake` in a background terminal. {rearm}"
    if harness in ("claude", "grok"):
        return f"Run `foil mail wait` as a background command. Its exit wakes you. {rearm}"
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

    def refuse(registry: dict) -> None:
        existing = registry["seats"].get("lead")
        state = (existing or {}).get("state", "")
        if existing and state == "joined":
            if not take:
                raise FoilError("foil: lead is joined. Pass --take to take it")
        elif existing and state != "killed":
            shown = _state(registry, "lead", existing)
            raise FoilError(f"foil: lead is {shown}. Run foil seat kill lead first")

    # A failed join leaves the seat as it was (F42).
    refuse(load_registry(root))
    instruction = _write_instruction(root, "lead", template, restarted=False, joined=True)
    previous: dict = {}

    def apply(fresh: dict) -> None:
        refuse(fresh)
        previous["lead"] = fresh["seats"].get("lead")
        previous["pointer"] = fresh.get("lead")
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
    try:
        with _lock(root, "lead"):
            record = _load(root, "lead")
            record["joined"] = time.time()
            record["quiet"] = 0
            _dead_check(root, "lead", record, force=True)
            items = _new_items(root, "lead", set(record["seen"]))
            record["seen"] += [item["relative"] for item in items]
            _save(root, "lead", record)
    except BaseException:

        def restore(fresh: dict) -> None:
            if previous["lead"] is None:
                fresh["seats"].pop("lead", None)
            else:
                fresh["seats"]["lead"] = previous["lead"]
            if previous["pointer"] is None:
                fresh.pop("lead", None)
            else:
                fresh["lead"] = previous["pointer"]

        update_registry(root, restore)
        raise
    registry = load_registry(root)
    board = (fleet_dir(root) / "board").resolve()
    head = [
        f"You are seat lead in fleet {registry['fleet_id']} ({root.resolve()}).",
        f"Harness: {harness or 'none'}. Session: {session or 'none'}.",
        f"Read first: {instruction}",
    ]
    tail = [
        f"Hear from the fleet: {_route(root, harness, session)}",
        f"Board: {board}. Before your first spawn, write status.md there with your file "
        "tool. If your harness asks or refuses, allow that folder alone before you spawn.",
    ]
    if _dirty(root):
        tail.append(
            "Your checkout has uncommitted changes. Workers branch from HEAD and do not see "
            "them. Commit or set them aside before you spawn."
        )
    print(_brief(head, items, tail))


def _brief(head: list[str], items: list[dict[str, str]], tail: list[str]) -> str:
    """The brief, with its newline, fitting as many item lines as stay under BRIEF_BYTES."""

    if not items:
        return "\n".join([*head, "New for lead: none.", *tail])
    for shown in range(min(len(items), BRIEF_ITEMS), -1, -1):
        middle = [f"New for lead ({len(items)}):", *(_line(item) for item in items[:shown])]
        if shown < len(items):
            middle.append(f"... and {len(items) - shown} more. Run foil mail list.")
        text = "\n".join([*head, *middle, *tail])
        if len(text.encode()) + 1 < BRIEF_BYTES:
            break
    return text


def leave_lead(root: Path) -> None:
    if not _outside():
        raise FoilError("foil: not allowed")
    registry = load_registry(root)
    record = registry["seats"].get("lead")
    if not record or record.get("state") != "joined":
        raise FoilError("foil: no joined lead")
    handoff = handoff_on_board(root, "lead")
    joined_at = _load(root, "lead")["joined"]
    try:
        info = handoff.lstat() if handoff is not None else None
    except OSError:
        info = None
    if info is None or info.st_mtime < joined_at:
        raise FoilError("foil: write board/handoffs/lead.md first")

    def apply(fresh: dict) -> None:
        current = fresh["seats"].get("lead")
        if not current or current.get("state") != "joined":
            raise FoilError("foil: no joined lead")
        current["state"] = "killed"

    update_registry(root, apply)
    print("lead left. Mail to lead queues until a lead joins or is spawned.")


def _wake(argv: list[str], session: str, items: list[dict[str, str]]) -> None:
    listed = "; ".join(f"{item['from']} {item['path']}" for item in items)
    values = {"{session_id}": session, "{text}": f"foil: {len(items)} new: {listed}"}
    try:
        child = subprocess.Popen(
            [values.get(token, token) for token in argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _poll(root: Path, name: str, timeout: int) -> list[dict[str, str]]:
    """One check of one mailbox: its dead-seat check and its new items."""

    with _lock(root, name):
        record = _load(root, name)
        _dead_check(root, name, record)
        items = _new_items(root, name, set(record["seen"]))
        if items and timeout == 0:
            # A blocking wait leaves them new, so a lost wake is not lost mail (F41).
            record["seen"] += [item["relative"] for item in items]
        _save(root, name, record)
    return items


def _end(root: Path, name: str, *, quiet: bool) -> int:
    with _lock(root, name):
        record = _load(root, name)
        record["quiet"] = record["quiet"] + 1 if quiet else 0
        _save(root, name, record)
    return record["quiet"]


def mail_wait(root: Path, *, timeout: int, wake: bool, all_fleets: bool = False) -> None:
    if not _outside():
        raise FoilError("foil: mail wait is outside the fleet only")
    if timeout < 0:
        raise FoilError("foil: invalid timeout")
    if wake and all_fleets:
        raise FoilError("foil: --wake takes no --all-fleets")
    fleets = fleet_ids(root) if all_fleets else [None]
    wake_argv, session = None, ""
    if wake:
        lead = load_registry(root)["seats"].get("lead") or {}
        session = lead.get("session_id", "")
        if actor(root) == "lead":
            wake_argv = _wake_argv(root, lead.get("harness", ""), session)
        if wake_argv is None:
            raise FoilError("foil: no wake command for this lead")
    deadline = time.monotonic() + timeout
    first = True
    found_at: float | None = None
    while True:
        found: list[tuple[str | None, dict[str, str]]] = []
        for fleet in fleets:
            with in_fleet(fleet):
                items = _poll(root, actor(root), timeout)
            found += [(fleet, item) for item in items]
        expired = time.monotonic() >= deadline
        ready = bool(found) and (
            first or expired or (found_at is not None and time.monotonic() - found_at >= COALESCE)
        )
        if ready or expired:
            break
        if found and found_at is None:
            found_at = time.monotonic()
        first = False
        time.sleep(max(0.0, min(1.0, deadline - time.monotonic())))
    quiet = 0
    if found or timeout > 0:
        for fleet in fleets:
            with in_fleet(fleet):
                count = _end(root, actor(root), quiet=not found)
            quiet = max(quiet, count)
    if not found:
        print("no new mail" if quiet < 2 else f"no new mail ({quiet} quiet waits in a row)")
        return
    found.sort(key=lambda pair: (pair[1]["time"], pair[1]["path"]))
    for fleet, item in found:
        print(_line(item) if fleet is None else f"{fleet} {_line(item)}")
    sys.stdout.flush()
    if wake_argv:
        _wake(wake_argv, session, [item for _fleet, item in found])


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

