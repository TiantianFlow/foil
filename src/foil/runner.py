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
from pathlib import Path
from typing import Any

from foil.board import items_for, write_mail_at
from foil.board_ops import body_for_inline, front_fields
from foil.errors import FoilError
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
    }


def load_record(board: Path, seat: str) -> dict[str, Any]:
    """The runner's record. A missing or unreadable one is empty (F8)."""

    record = empty_record()
    try:
        stored = read_json(record_path(board, seat))
    except FoilError:
        return record
    for key, default in record.items():
        value = stored.get(key, default)
        if key in ("delivered", "inflight"):
            value = value if isinstance(value, list) else []
            value = [item for item in value if isinstance(item, str)]
        elif key == "turn_started":
            value = value if isinstance(value, int | float) else None
        elif key == "last_exit":
            value = value if type(value) is int else None
        elif type(value) is not type(default):
            value = default
        record[key] = value
    return record


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


def wake_prompt(plan: dict[str, Any], items: list[dict[str, str]], *, restarted: bool) -> str:
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
    lines.append(
        f"Re-read {plan['instruction']} before you act."
        + (
            " Read each item above that is not inlined: a path under board/mail/ with "
            "foil mail read, a path under board/room/ with foil board read."
            if len(inlined) < len(items)
            else ""
        )
    )
    return "\n".join(lines) + "\n"


def first_prompt(plan: dict[str, Any]) -> str:
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

    def mail(self, to: str, text: str) -> None:
        with contextlib.suppress(FoilError, OSError):
            write_mail_at(self.board, to, text, "foil")

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
        argv = [values.get(token, token) for token in self.plan[kind]]
        learn = bool(self.plan["session_key"]) and not session
        print(f"foil: turn started ({kind})", flush=True)
        try:
            child = subprocess.Popen(
                argv,
                cwd=self.plan["cwd"],
                env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if learn else None,
                start_new_session=True,
            )
        except OSError as exc:
            print(f"foil: could not run {argv[0]}: {exc.strerror or exc}", flush=True)
            print(f"foil: turn ended (exit {CANNOT_RUN})", flush=True)
            return CANNOT_RUN
        self.child = child
        self.record["pgid"] = child.pid
        self.save()
        reader = None
        if learn:
            reader = threading.Thread(target=self._relay, args=(child,), daemon=True)
            reader.start()
        try:
            code = child.wait(timeout=self.plan["turn_timeout"])
            code = 128 - code if code < 0 else code
        except subprocess.TimeoutExpired:
            self._end_group(child.pid)
            child.wait()
            code = -1
        self._end_group(child.pid)
        if reader is not None:
            reader.join(timeout=LEFTOVER_GRACE)
        self.child = None
        self.record["pgid"] = 0
        print(f"foil: turn ended (exit {code})", flush=True)
        return code

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
        fresh["delivered"] = sorted(delivered)
        save_record(self.board, self.seat, fresh)
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
            if kind == "start":
                prompt = first_prompt(self.plan)
                # The instruction file says it already when the plan starts with start.
                said = restarted and not (self.plan["first"] == "start" and first == "start")
                if batch or said:
                    prompt += "\n" + wake_prompt(self.plan, batch, restarted=said)
            else:
                prompt = wake_prompt(self.plan, batch, restarted=restarted)
            self.record["inflight"] = [item["relative"] for item in batch]
            started = time.time()
            self.record["turn_started"] = started
            self.save()
            code = self.turn(kind, prompt)
            self.record["turn_started"] = None
            self.record["last_exit"] = code
            if code == 0:
                self.record["started"] = True
            if code == 0 or (code == -1 and self.record["started"]):
                self.record["delivered"] = sorted(
                    set(self.record["delivered"]) | set(self.record["inflight"])
                )
                self.record["inflight"] = []
                self.record["failures"] = 0
                first, restarted = None, False
                self.save()
                self._after(code, started)
                continue
            if code == -1:
                # Session never started: keep the batch and try start again.
                self.record["failures"] = 0
                first = "start"
                if not self.plan["session_key"] and self.plan["session_id"]:
                    self.record["session_id"] = str(uuid.uuid4())
                self.save()
                self._after(code, started)
                continue
            self.record["failures"] += 1
            if self.record["started"]:
                first = "turn"
            elif (
                not self.record["started"]
                and not self.plan["session_key"]
                and self.plan["session_id"]
            ):
                self.record["session_id"] = str(uuid.uuid4())
                first = "start"
            waits = self.plan["retry"]
            if self.record["failures"] > len(waits):
                self.record["paused"] = True
                self.save()
                self.mail(
                    self.plan["reader"],
                    f"{self.seat} paused after {self.record['failures']} failed turns "
                    f"(exit {code}). See foil seat peek {self.seat} --lines 200. "
                    f"foil seat resume {self.seat} retries.",
                )
                continue
            self.save()
            deadline = time.monotonic() + waits[self.record["failures"] - 1]
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


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m foil.runner PLAN.json", file=sys.stderr)
        return 2
    try:
        runner = Runner(Path(arguments[0]))
        signal.signal(signal.SIGTERM, runner.stop)
        signal.signal(signal.SIGHUP, runner.stop)
        runner.loop()
    except (OSError, ValueError, FoilError) as exc:
        print(f"foil runner failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
