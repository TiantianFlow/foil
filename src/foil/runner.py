"""A launched seat's runner: its turns, one at a time, from a plan (F43)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.board import items_for, write_mail_at
from foil.board_ops import body_for_inline, front_fields
from foil.delivery import append, fleet_paused, list_pointers, pending_pointers
from foil.delivery import skipped as log_skipped
from foil.errors import FoilError
from foil.journal import note, sent
from foil.store import MAX_BYTES, SAFE_ID, SCHEMA_VERSION, read_json, write_json

MAX_PLAN_BYTES = 256 * 1024
POLL = 1.0
COALESCE = 1.0
INLINE = 600
LISTED = 50
INLINE_TOTAL = 16 * 1024
CANNOT_RUN = 127
FIRST_INLINE = 64 * 1024
LEFTOVER_GRACE = 2.0
RESTARTED = "You were restarted. Check before you repeat a merge, push, post or send."
_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_FIELDS = {
    "start",
    "turn",
    "session_key",
    "session_id",
    "cwd",
    "env",
    "env_forward",
    "instruction",
    "system",
    "board",
    "seat",
    "reader",
    "worker",
    "turn_timeout",
    "retry",
    "first",
    "restarted",
}


class RunnerError(ValueError):
    """A runner plan is malformed or unsafe."""


def _argv(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item and "\x00" not in item for item in value)
        and "{prompt}" in value
    )


def load_plan(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RunnerError("runner plan must be a regular non-symlink file")
    if info.st_size > MAX_PLAN_BYTES:
        raise RunnerError("runner plan exceeds size limit")
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or set(plan) != _FIELDS:
        raise RunnerError("runner plan has invalid fields; run foil seat kill and spawn again")
    environment = plan["env"]
    if (
        not _argv(plan["start"])
        or not _argv(plan["turn"])
        or not all(isinstance(plan[key], str) for key in ("session_key", "session_id"))
        or not all(
            isinstance(plan[key], str) and Path(plan[key]).is_absolute()
            for key in ("cwd", "instruction", "board")
        )
        or not all(SAFE_ID.fullmatch(str(plan[key])) for key in ("seat", "reader"))
        or plan["first"] not in {"start", "turn"}
        or type(plan["turn_timeout"]) is not int
        or plan["turn_timeout"] < 1
        or not isinstance(plan["retry"], list)
        or not all(type(wait) in (int, float) and wait >= 0 for wait in plan["retry"])
        or not isinstance(plan["worker"], bool)
        or not isinstance(plan["system"], bool)
        or not isinstance(plan["restarted"], bool)
        or not isinstance(environment, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) and "\x00" not in key + value
            for key, value in environment.items()
        )
        or not isinstance(plan["env_forward"], list)
        or not all(
            isinstance(name, str) and _ENVIRONMENT_NAME.fullmatch(name)
            for name in plan["env_forward"]
        )
    ):
        raise RunnerError("runner plan is invalid")
    return plan


def record_path(board: Path, seat: str, suffix: str = ".json") -> Path:
    return board.parent / "run" / "turns" / f"{seat}{suffix}"


def empty_record() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": "",
        "delivered": [],
        "inflight": [],
        "turn_started": None,
        "last_exit": None,
        "failures": 0,
        "started": False,
        "paused": False,
        "pgid": 0,
        "sessions": [],
    }


def load_record(board: Path, seat: str) -> dict[str, Any]:
    """The runner's record. A missing or unreadable one is empty (F8).

    A symlink at ``run`` or ``turns`` is not followed.
    """

    record = empty_record()
    path = record_path(board, seat)
    try:
        if path.parent.is_symlink() or path.parent.parent.is_symlink():
            return record
        stored = read_json(path)
    except (FoilError, OSError):
        return record
    for key, default in record.items():
        value = stored.get(key, default)
        if key in ("delivered", "inflight"):
            value = value if isinstance(value, list) else []
            value = [item for item in value if isinstance(item, str)]
        elif key == "sessions":
            value = _sessions(value)
        elif key == "turn_started":
            value = value if isinstance(value, int | float) else None
        elif key == "last_exit":
            value = value if type(value) is int else None
        elif type(value) is not type(default):
            value = default
        record[key] = value
    return record


def _sessions(value: Any) -> list[dict[str, str]]:
    """Session rows already stored. A record written without the list loads empty."""

    if not isinstance(value, list):
        return []
    kept: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        session_id = item.get("id")
        harness = item.get("harness")
        model = item.get("model")
        started = item.get("started")
        if not all(isinstance(part, str) for part in (session_id, harness, model, started)):
            continue
        if not SAFE_ID.fullmatch(session_id):
            continue
        kept.append(
            {"id": session_id, "harness": harness, "model": model, "started": started}
        )
    return kept


def remember_session(
    record: dict[str, Any],
    *,
    session_id: str,
    harness: str,
    model: str,
    started: str | None = None,
) -> None:
    """Append one session id. The same id is not stored twice."""

    if not session_id or not SAFE_ID.fullmatch(session_id):
        return
    sessions = record.setdefault("sessions", [])
    if any(isinstance(item, dict) and item.get("id") == session_id for item in sessions):
        return
    when = started or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    sessions.append(
        {"id": session_id, "harness": harness, "model": model, "started": when}
    )


def _merged_sessions(*records: dict[str, Any]) -> list[dict[str, str]]:
    """Session rows from each record, first occurrence of an id kept."""

    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for record in records:
        for item in record.get("sessions") or []:
            if not isinstance(item, dict):
                continue
            session_id = item.get("id")
            if not isinstance(session_id, str) or session_id in seen:
                continue
            seen.add(session_id)
            merged.append(item)
    return merged


def prune_delivered(board: Path, record: dict[str, Any]) -> None:
    """Drop gone delivered paths, then the oldest if the record would exceed its size limit."""

    kept: list[str] = []
    for relative in record["delivered"]:
        path = board / relative
        try:
            if not path.is_symlink() and path.is_file():
                kept.append(relative)
        except OSError:
            continue
    record["delivered"] = sorted(kept)
    # Leave headroom for inflight and the other fields (N7's 1 MiB record cap).
    limit = MAX_BYTES - 64 * 1024
    while record["delivered"]:
        encoded = (
            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode()
        if len(encoded) <= limit:
            return
        drop = max(1, len(record["delivered"]) // 8)
        record["delivered"] = record["delivered"][drop:]


def save_record(board: Path, seat: str, record: dict[str, Any]) -> None:
    prune_delivered(board, record)
    path = record_path(board, seat)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, record)


def wake_prompt(
    plan: dict[str, Any],
    items: list[dict[str, str]],
    *,
    restarted: bool,
    pointers: list[dict[str, str]] | None = None,
) -> str:
    lines = [RESTARTED] if restarted else []
    listed = items[:LISTED]
    if items:
        lines.append(f"New for {plan['seat']} ({len(items)}):")
        lines += [f"{item['time']} {item['from']} {item['path']}" for item in listed]
        if len(items) > len(listed):
            lines.append(
                f"...and {len(items) - len(listed)} more: run foil mail list, "
                "and foil board list 'room/*/*.md' for posts."
            )
    else:
        lines.append(f"Nothing new for {plan['seat']}.")
    for item in pointers or []:
        lines.append(f"The human mailed {item['to']} directly: {item['time']} {item['path']}")
    inlined = set()
    budget = INLINE_TOTAL
    for item in listed:
        if not item["relative"].startswith("mail/"):
            continue
        limit = min(INLINE, budget)
        body = body_for_inline(Path(item["path"]), limit)
        if body is None:
            continue
        size = len(body.encode())
        budget -= size
        inlined.add(item["path"])
        lines += [f"--- {item['path']}", body.rstrip("\n")]
    unread = (
        "Read each item above that is not inlined: a path under board/mail/ with "
        "foil mail read, a path under board/room/ with foil board read."
        if len(inlined) < len(items)
        else ""
    )
    if plan["system"]:
        if unread:
            lines.append(unread)
    else:
        lines.append(f"Re-read {plan['instruction']} before you act." + (" " + unread if unread else ""))
    return "\n".join(lines) + "\n"


def first_prompt(plan: dict[str, Any]) -> str:
    if plan["system"]:
        return "Your role instructions are in your system instructions. Begin.\n"
    path = Path(plan["instruction"])
    try:
        if path.stat().st_size < FIRST_INLINE:
            return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    return f"Read {path} before anything else. It is your instruction file.\n"


def _wrote(board: Path, seat: str, since: float) -> bool:
    """Whether the seat wrote mail, a room file, or a result since ``since``."""

    def fresh(path: Path) -> bool:
        try:
            return not path.is_symlink() and path.is_file() and path.stat().st_mtime >= since
        except OSError:
            return False

    for path in board.glob(f"mail/*/*-{seat}-*.md"):
        if fresh(path):
            return True
    for path in board.glob("room/*/*.md"):
        if fresh(path):
            fields = front_fields(path)
            if fields and fields.get("from") == seat:
                return True
    return any(fresh(path) for path in board.glob("results/**/*"))


class Runner:
    def __init__(self, plan_path: Path):
        self.plan_path = plan_path
        self.plan = load_plan(plan_path)
        self.board = Path(self.plan["board"])
        self.seat = self.plan["seat"]
        self.record = load_record(self.board, self.seat)
        self.environment = dict(self.plan["env"])
        for name in self.plan["env_forward"]:
            value = os.environ.get(name)
            if value is not None and "\x00" not in value:
                self.environment[name] = value
        self.child: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def save(self) -> None:
        with self._lock:
            save_record(self.board, self.seat, self.record)

    def _pair(self) -> tuple[str, str]:
        """Harness and model for a session this runner creates."""

        sessions = self.record.get("sessions") or []
        if sessions and isinstance(sessions[-1], dict) and sessions[-1].get("harness"):
            return str(sessions[-1]["harness"]), str(sessions[-1].get("model") or "")
        try:
            stored = read_json(self.board.parent / "run" / "registry.json")
            seat = stored.get("seats", {}).get(self.seat, {})
        except (FoilError, OSError, AttributeError):
            return "", ""
        if not isinstance(seat, dict):
            return "", ""
        return str(seat.get("harness") or ""), str(seat.get("model") or "")

    def _remember_current(self) -> None:
        session = self.record["session_id"] or self.plan["session_id"]
        harness, model = self._pair()
        remember_session(self.record, session_id=session, harness=harness, model=model)

    def mail(self, to: str, text: str) -> None:
        """Best-effort notice. A journal or mail failure leaves the turn loop running."""

        try:
            path = write_mail_at(self.board, to, text, "foil")
            sent(self.board.parent, self.board, "foil", to, path)
        except (FoilError, OSError):
            return

    def stop(self, *_args: Any) -> None:
        """The window is closing: end the turn's process group, then exit."""

        if self.child is not None:
            self._end_group(self.child.pid)
        raise SystemExit(0)

    def _end_group(self, pgid: int) -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except OSError:
                return
            deadline = time.monotonic() + LEFTOVER_GRACE
            while time.monotonic() < deadline:
                try:
                    os.killpg(pgid, 0)
                except OSError:
                    return
                time.sleep(0.1)

    def turn(self, kind: str, prompt: str) -> int:
        """Run one turn. A signal is 128 plus its number, and -1 is a turn ended by turn_timeout."""

        session = self.record["session_id"] or self.plan["session_id"]
        values = {"{prompt}": prompt, "{session_id}": session}
        template = list(self.plan[kind])
        if any("{instruction_toml}" in token for token in template):
            text = json.dumps(self._instruction_text(), ensure_ascii=False)
            template = [token.replace("{instruction_toml}", text) for token in template]
        argv = [values.get(token, token) for token in template]
        learn = bool(self.plan["session_key"]) and not session
        print(f"foil: turn started ({kind})", flush=True)
        note(self.board.parent, "turn_start", seat=self.seat, kind=kind)
        code = execute_turn(
            argv,
            cwd=self.plan["cwd"],
            env=self.environment,
            timeout=self.plan["turn_timeout"],
            learn=learn,
            relay=self._relay,
            on_child=self._child,
            end_group=self._end_group,
        )
        note(self.board.parent, "turn_end", seat=self.seat, exit=code)
        return code

    def _instruction_text(self) -> str:
        try:
            return Path(self.plan["instruction"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return f"Read {self.plan['instruction']} before anything else."

    def _child(self, child: subprocess.Popen | None) -> None:
        self.child = child
        if child is None:
            self.record["pgid"] = 0
            return
        self.record["pgid"] = child.pid
        self.save()

    def _relay(self, child: subprocess.Popen) -> None:
        """Pass the output on as written; take the session id from its first line (F12)."""

        key = self.plan["session_key"]
        out = sys.stdout.buffer
        for raw in child.stdout:
            out.write(raw)
            out.flush()
            with contextlib.suppress(ValueError):
                line = json.loads(raw)
                value = line.get(key) if isinstance(line, dict) else None
                if not (isinstance(value, str) and SAFE_ID.fullmatch(value)):
                    continue
                with self._lock:
                    if self.record["session_id"]:
                        continue
                    self.record["session_id"] = value
                    self.record["started"] = True
                    harness, model = self._pair()
                    remember_session(
                        self.record, session_id=value, harness=harness, model=model
                    )
                    save_record(self.board, self.seat, self.record)

    def _new(self) -> list[dict[str, str]]:
        skip = set(self.record["delivered"]) | set(self.record["inflight"])
        return items_for(self.board, self.seat, skip)

    def _renew(self) -> None:
        """A renew request: take its items as delivered, then start over on the new plan (F44)."""

        marker = record_path(self.board, self.seat, ".renew")
        if marker.is_symlink() or not marker.is_file():
            return
        try:
            present = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            present = []
        delivered = set(self.record["delivered"]) | set(self.record["inflight"])
        delivered |= {item for item in present if isinstance(item, str)}
        fresh = empty_record()
        fresh["sessions"] = _merged_sessions(self.record, load_record(self.board, self.seat))
        fresh["delivered"] = sorted(delivered)
        save_record(self.board, self.seat, fresh)
        added = [
            path
            for path in fresh["delivered"]
            if path not in self.record["delivered"]
        ]
        log_skipped(self.board.parent, self.seat, added)
        marker.unlink()
        pending = record_path(self.board, self.seat, ".fresh")
        if pending.is_file() and not pending.is_symlink():
            with contextlib.suppress(OSError):
                pending.unlink()
        os.execv(sys.executable, [sys.executable, "-P", "-m", "foil.runner", str(self.plan_path)])

    def _paused(self) -> bool:
        if not self.record["paused"]:
            return False
        marker = record_path(self.board, self.seat, ".resume")
        if marker.is_symlink() or not marker.exists():
            return True
        marker.unlink()
        self.record["paused"] = False
        self.record["failures"] = 0
        self.save()
        return False

    def _batch(self, items: list[dict[str, str]]) -> list[dict[str, str]]:
        """The in-flight items still on the board, then the new ones."""

        known = {item["relative"]: item for item in items_for(self.board, self.seat, set())}
        carried = [known[path] for path in self.record["inflight"] if path in known]
        fresh = [item for item in items if item["relative"] not in self.record["inflight"]]
        return carried + fresh

    def loop(self) -> None:
        first = self.plan["first"]
        restarted = self.plan["restarted"]
        while True:
            self._renew()
            if self._paused():
                time.sleep(POLL)
                continue
            if fleet_paused(self.board.parent):
                time.sleep(POLL)
                continue
            items = self._new()
            if first is None and not self.record["inflight"] and not items:
                time.sleep(POLL)
                continue
            if items and first is None and not self.record["failures"]:
                time.sleep(COALESCE)
                items = self._new()
            batch = self._batch(items)
            kind = first or "turn"
            if kind == "turn" and "{session_id}" in self.plan["turn"] and not (
                self.record["session_id"] or self.plan["session_id"]
            ):
                kind = "start"
            pointers = pending_pointers(self.board.parent) if self.seat == "lead" else []
            if kind == "start":
                prompt = first_prompt(self.plan)
                # The instruction file says it already when the plan starts with start.
                said = restarted and not (self.plan["first"] == "start" and first == "start")
                if batch or said or pointers:
                    prompt += "\n" + wake_prompt(
                        self.plan, batch, restarted=said, pointers=pointers
                    )
            else:
                prompt = wake_prompt(self.plan, batch, restarted=restarted, pointers=pointers)
            if pointers:
                list_pointers(self.board.parent, [item["path"] for item in pointers])
            self.record["inflight"] = [item["relative"] for item in batch]
            self._remember_current()
            started = time.time()
            self.record["turn_started"] = started
            self.save()
            append(
                self.board.parent,
                self.seat,
                [(item["relative"], "inflight") for item in batch],
            )
            code = self.turn(kind, prompt)
            self.record["turn_started"] = None
            self.record["last_exit"] = code
            if code == 0:
                self.record["started"] = True
            if code == 0 or (code == -1 and self.record["started"]):
                already = set(self.record["delivered"])
                delivered = [
                    path
                    for path in self.record["inflight"]
                    if path.startswith("mail/") and path not in already
                ]
                self.record["delivered"] = sorted(already | set(self.record["inflight"]))
                append(
                    self.board.parent,
                    self.seat,
                    [(path, "delivered") for path in self.record["inflight"]],
                )
                self.record["inflight"] = []
                self.record["failures"] = 0
                first, restarted = None, False
                self.save()
                for path in delivered:
                    note(self.board.parent, "mail_delivered", seat=self.seat, path=path)
                self._after(code, started)
                continue
            if code == -1:
                # No session yet: keep the batch and count a failed turn (F43).
                self._after(code, started)
                first = "start"
            self.record["failures"] += 1
            if self.record["started"]:
                first = "turn"
            elif (
                not self.record["started"]
                and not self.plan["session_key"]
                and self.plan["session_id"]
            ):
                self.record["session_id"] = str(uuid.uuid4())
                self._remember_current()
                first = "start"
            waits = self.plan["retry"]
            if self.record["failures"] > len(waits):
                # The notice is on disk before paused is visible.
                self.mail(
                    self.plan["reader"],
                    f"{self.seat} paused after {self.record['failures']} failed turns "
                    f"(exit {code}). See foil seat peek {self.seat} --lines 200. "
                    f"foil seat resume {self.seat} retries.",
                )
                self.record["paused"] = True
                self.save()
                note(self.board.parent, "pause", seat=self.seat, exit=code)
                continue
            wait = waits[self.record["failures"] - 1]
            self.save()
            note(self.board.parent, "retry", seat=self.seat, exit=code, wait=wait)
            append(
                self.board.parent,
                self.seat,
                [(path, "retry") for path in self.record["inflight"]],
            )
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                self._renew()
                time.sleep(min(POLL, max(0.0, deadline - time.monotonic())))

    def _after(self, code: int, started: float) -> None:
        if code == -1:
            self.mail(
                self.plan["reader"],
                f"{self.seat}'s turn passed turn_timeout and was stopped. "
                f"See foil seat peek {self.seat} --lines 200.",
            )
        elif self.plan["worker"] and not _wrote(self.board, self.seat, started):
            self.mail(
                "lead",
                f"{self.seat} ended a turn without mail, a post, or a result. "
                f"See foil seat peek {self.seat} --lines 200.",
            )


def execute_turn(
    argv: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: int,
    learn: bool,
    relay,
    on_child,
    end_group,
) -> int:
    """Run one turn process. A signal is 128 plus its number, and -1 is a timeout.

    ``learn`` reads stdout for an output preset's session id. ``on_child`` is
    called with the process, then with None when it has ended.
    """

    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if learn else None,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"foil: could not run {argv[0]}: {exc.strerror or exc}", flush=True)
        print(f"foil: turn ended (exit {CANNOT_RUN})", flush=True)
        return CANNOT_RUN
    on_child(child)
    reader = None
    if learn:
        reader = threading.Thread(target=relay, args=(child,), daemon=True)
        reader.start()
    try:
        code = child.wait(timeout=timeout)
        code = 128 - code if code < 0 else code
    except subprocess.TimeoutExpired:
        end_group(child.pid)
        child.wait()
        code = -1
    end_group(child.pid)
    if reader is not None:
        reader.join(timeout=LEFTOVER_GRACE)
    on_child(None)
    print(f"foil: turn ended (exit {code})", flush=True)
    return code


def _master_plan(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(raw, dict) and raw.get("kind") == "master"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m foil.runner PLAN.json", file=sys.stderr)
        return 2
    path = Path(arguments[0])
    try:
        if _master_plan(path):
            from foil.master import MasterRunner

            runner = MasterRunner(path)
        else:
            runner = Runner(path)
        signal.signal(signal.SIGTERM, runner.stop)
        signal.signal(signal.SIGHUP, runner.stop)
        runner.loop()
    except (OSError, ValueError, FoilError) as exc:
        print(f"foil runner failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
