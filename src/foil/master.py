"""The master: one runner per store, above every fleet (section 13).

The process is the seat runner. Each turn is a new session. A wake is mail
in the store mailbox, an inbox identity that was not in the last set, a
removed watch, or a watch whose check time has arrived.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import signal
import stat
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

from foil.board import mail_name
from foil.errors import FoilError, shown
from foil.inbox import collect_brief_projects, collect_projects, format_brief
from foil.merge import finish_block
from foil.presets import (
    TURN_TIMEOUT,
    expand_argv,
    installed_harness,
    load_preset,
    model_warning,
)
from foil.project import checkout_for_key, common_dir, project_key, store_root
from foil.runner import (
    POLL,
    RunnerError,
    execute_turn,
    remember_session,
)
from foil.store import (
    SAFE_ID,
    actor,
    create_exclusive,
    exclusive_lock,
    fleet_id,
    in_seat,
    private_dir,
    read_json,
    scan,
    write_bytes,
    write_json,
)
from foil.tmux import TmuxController, TmuxError, TmuxTarget

MARKER_FLEET = "master"
MARKER_SEAT = "master"
BRIEF_CAP = 16 * 1024
FILE_CAP = 8 * 1024
LAST_MAIL = 20
BODY_CAP = 600
_PLAN_FIELDS = {
    "kind",
    "start",
    "session_key",
    "cwd",
    "env",
    "env_forward",
    "harness",
    "model",
    "turn_timeout",
    "retry",
    "root",
}
_RETRY = [30, 120, 480]
_WATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WATCH_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_ON_CAP = 200
_READ = object()
KINDS = ("nudge", "pause", "resume", "renew")


def directory() -> Path:
    """`<root>/master/`."""

    return store_root() / "master"


def session_name(root: Path | None = None) -> str:
    """One tmux session per store, so two stores do not share a window."""

    store = root if root is not None else store_root()
    digest = hashlib.sha256(str(store.resolve()).encode()).hexdigest()[:8]
    return f"foil-master-{digest}"


def _run(root: Path) -> Path:
    return root / "run"


def _ensure(root: Path) -> None:
    if root.is_symlink():
        raise FoilError("foil: refusing symlink")
    private_dir(root)
    private_dir(root / "mail")
    private_dir(_run(root))
    for name in ("log.md", "watches.md", "mandate.md"):
        path = root / name
        if path.is_symlink():
            raise FoilError("foil: refusing symlink")
        if not path.exists():
            write_bytes(path, b"")


def _split(spec: str) -> tuple[str, str]:
    harness, sep, model = spec.partition("/")
    if (
        not SAFE_ID.fullmatch(harness)
        or (model and (model.startswith("-") or "\x00" in model))
    ):
        raise FoilError(f"foil: unknown harness '{shown(spec)}'")
    if model:
        scan(model)
    return harness, model


def _preset(harness: str) -> dict[str, Any]:
    try:
        return load_preset(store_root(), harness, global_only=True)
    except FoilError as exc:
        if "unknown harness" in str(exc):
            raise FoilError(f"foil: unknown harness '{shown(harness)}'") from exc
        raise


def _installed() -> str:
    try:
        return installed_harness(store_root())
    except FoilError as exc:
        raise FoilError("foil: no harness installed") from exc


def _window_path(root: Path) -> Path:
    return _run(root) / "window.json"


def _load_window(root: Path) -> dict[str, Any]:
    path = _window_path(root)
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        stored = read_json(path)
    except FoilError:
        return {}
    if not isinstance(stored, dict):
        return {}
    return stored


def _save_window(root: Path, record: dict[str, Any]) -> None:
    write_json(_window_path(root), record)


def _target(record: dict[str, Any]) -> TmuxTarget | None:
    window = str(record.get("window_id") or "")
    if not window.startswith("@"):
        return None
    return TmuxTarget(
        session_name=str(record.get("session_name") or ""),
        window_name=MARKER_SEAT,
        session_id=str(record.get("session_id") or "") or None,
        window_id=window,
    )


def _alive(record: dict[str, Any]) -> bool:
    target = _target(record)
    if target is None:
        return False
    try:
        return TmuxController().matches_window(
            MARKER_FLEET, MARKER_SEAT, target.session_name, target.window_id or ""
        )
    except TmuxError:
        return False


def _drop_dead(record: dict[str, Any]) -> None:
    target = _target(record)
    if target is None:
        return
    try:
        TmuxController().remove_verified(MARKER_FLEET, MARKER_SEAT, target)
    except TmuxError:
        return


def record_path(root: Path) -> Path:
    return _run(root) / "record.json"


READS_KEPT = 32


def _reads(value: object) -> list[dict[str, Any]]:
    """Turn records: the mail names a turn was given, and the master mail it wrote."""

    if not isinstance(value, list):
        return []
    found = []
    for item in value:
        if not isinstance(item, dict) or type(item.get("open")) is not bool:
            continue
        names = item.get("names")
        wrote = item.get("wrote")
        if not isinstance(names, list) or not isinstance(wrote, list):
            continue
        if not all(isinstance(name, str) for name in names):
            continue
        if not all(isinstance(name, str) for name in wrote):
            continue
        found.append({"names": list(names), "wrote": list(wrote), "open": item["open"]})
    return found[-READS_KEPT:]


def master_mail_names(root: Path) -> set[str]:
    """Mail file names in ``root`` whose sender is ``master``."""

    found = set()
    for path in conversation(root):
        named = mail_name(path.name)
        if named is not None and named[1] == "master":
            found.add(path.name)
    return found


def open_read(record: dict[str, Any], names: list[str]) -> None:
    """Start a turn record. The last 32 are kept."""

    for item in record["reads"]:
        if item.get("open"):
            item["open"] = False
    record["reads"].append({"names": list(names), "wrote": [], "open": True})
    record["reads"] = record["reads"][-READS_KEPT:]


def close_read(record: dict[str, Any], wrote: list[str]) -> None:
    """Record the master mail names that appeared during the open turn."""

    for item in reversed(record["reads"]):
        if item.get("open"):
            item["open"] = False
            item["wrote"] = list(wrote)
            return


def empty_record() -> dict[str, Any]:
    return {
        "schema_version": 1,
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
        "seen_inbox": [],
        "seen_checks": [],
        "baselined": False,
        "reads": [],
    }


def load_record(root: Path) -> dict[str, Any]:
    record = empty_record()
    path = record_path(root)
    if path.is_symlink() or not path.is_file():
        return record
    try:
        stored = read_json(path)
    except FoilError:
        return record
    for key, default in record.items():
        value = stored.get(key, default)
        if key in {"delivered", "inflight", "seen_inbox", "seen_checks"}:
            value = (
                [item for item in value if isinstance(item, str)]
                if isinstance(value, list)
                else []
            )
        elif key == "sessions":
            value = value if isinstance(value, list) else []
        elif key == "reads":
            value = _reads(value)
        elif key == "turn_started":
            value = value if isinstance(value, int | float) else None
        elif key == "last_exit":
            value = value if type(value) is int else None
        elif type(value) is not type(default):
            value = default
        record[key] = value
    return record


def save_record(root: Path, record: dict[str, Any]) -> None:
    private_dir(_run(root))
    write_json(record_path(root), record)


def _group_alive(pgid: object) -> bool:
    """The recorded turn's process group still exists."""

    if not isinstance(pgid, int) or pgid <= 1:
        return False
    try:
        os.kill(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def turn_running(root: Path) -> bool:
    """A turn is running when the record says so and that process group exists."""

    record = load_record(root)
    return record["turn_started"] is not None and _group_alive(record["pgid"])


def queue_reason(root: Path) -> str:
    """Why a message that has not entered a turn is waiting."""

    record = load_record(root)
    if turn_running(root):
        return "a running turn"
    if record["paused"]:
        return "a paused master"
    if record["failures"]:
        return "a retry"
    return "the next turn"


def pane_tail(root: Path, lines: int = 40) -> str:
    """The last lines of the master's pane. A missing window is empty.

    The text is for display. It does not decide that a turn is running.
    """

    window = _load_window(root)
    if not _alive(window):
        return ""
    try:
        text = TmuxController().capture_pane(str(window.get("window_id") or ""), lines)
    except TmuxError:
        return ""
    raw = text.encode()
    if len(raw) <= FILE_CAP:
        return text
    return raw[:FILE_CAP].decode(errors="ignore")


def load_plan(path: Path) -> dict[str, Any]:
    """The master plan. A seat plan is refused here."""

    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RunnerError("runner plan must be a regular non-symlink file")
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RunnerError("runner plan is invalid") from exc
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS or plan["kind"] != "master":
        raise RunnerError("runner plan is invalid")
    environment = plan["env"]
    if (
        not isinstance(plan["start"], list)
        or not plan["start"]
        or not all(isinstance(item, str) and item and "\x00" not in item for item in plan["start"])
        or "{prompt}" not in plan["start"]
        or not isinstance(plan["session_key"], str)
        or not Path(plan["cwd"]).is_absolute()
        or not Path(plan["root"]).is_absolute()
        or not isinstance(environment, dict)
        or not all(
            isinstance(key, str) and isinstance(value, str) and "\x00" not in key + value
            for key, value in environment.items()
        )
        or not isinstance(plan["env_forward"], list)
        or not all(isinstance(name, str) for name in plan["env_forward"])
        or not isinstance(plan["harness"], str)
        or not SAFE_ID.fullmatch(plan["harness"])
        or not isinstance(plan["model"], str)
        or type(plan["turn_timeout"]) is not int
        or plan["turn_timeout"] < 1
        or not isinstance(plan["retry"], list)
        or not all(type(wait) in (int, float) and wait >= 0 for wait in plan["retry"])
    ):
        raise RunnerError("runner plan is invalid")
    return plan


def _pair_label(harness: str, model: str) -> str:
    return f"{harness}/{model}" if model else harness


def _choose(root: Path, spec: str | None, *, keep: bool) -> tuple[str, str, dict[str, Any]]:
    window = _load_window(root)
    recorded = (
        str(window.get("harness") or ""),
        str(window.get("model") or ""),
    )
    if spec:
        harness, model = _split(spec)
    elif keep and recorded[0]:
        harness, model = recorded
    else:
        harness, model = _installed(), ""
    preset = _preset(harness)
    if model:
        warning = model_warning(preset, model)
        if warning:
            print(warning, file=sys.stderr)
    return harness, model, preset


def _plan_env(preset: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    forward = list(preset["env"])
    for name in ("PATH", "HOME"):
        if name not in forward:
            forward.append(name)
    environment = {**preset.get("set_env", {}), "FOIL_MASTER": "1"}
    for name in ("FOIL_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment, forward


def _write_plan(root: Path, harness: str, model: str, preset: dict[str, Any]) -> Path:
    environment, forward = _plan_env(preset)
    plan = {
        "kind": "master",
        "start": expand_argv(
            preset, model=model or None, session_id=None, permission="ask", turn=False
        ),
        "session_key": preset["session_key"],
        "cwd": str(root.resolve()),
        "env": environment,
        "env_forward": forward,
        "harness": harness,
        "model": model,
        "turn_timeout": TURN_TIMEOUT,
        "retry": list(_RETRY),
        "root": str(root.resolve()),
    }
    scan(plan)
    path = _run(root) / "plan.json"
    write_bytes(path, json.dumps(plan, sort_keys=True).encode())
    return path


def _runner_environment() -> dict[str, str]:
    """What the runner process itself needs in order to see this store."""

    found = {}
    for name in ("PATH", "HOME", "FOIL_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            found[name] = value
    return found


def _launch(root: Path, plan_path: Path) -> TmuxTarget:
    try:
        target = TmuxController().launch(
            fleet_id=MARKER_FLEET,
            seat_id=MARKER_SEAT,
            session_name=session_name(),
            window_name=MARKER_SEAT,
            working_directory=root.resolve(),
            runner_argv=[sys.executable, "-P", "-m", "foil.runner", str(plan_path)],
            environment=_runner_environment(),
        )
    except TmuxError as exc:
        raise FoilError("foil: could not launch master") from exc
    if not target.window_id:
        TmuxController().abandon_window(target)
        raise FoilError("foil: could not launch master")
    return target


def start(spec: str | None) -> None:
    root = directory()
    _ensure(root)
    with exclusive_lock(_run(root) / "lock"):
        window = _load_window(root)
        if _alive(window):
            record = load_record(root)
            if record["paused"]:
                write_bytes(_run(root) / "resume", b"")
                print("foil: master resumed")
                return
            raise FoilError("foil: master is running")
        _drop_dead(window)
        for name in ("renew", "resume"):
            marker = _run(root) / name
            if marker.is_file() and not marker.is_symlink():
                marker.unlink()
        # A dead window does not resume itself. A paused record would
        # otherwise sleep until another start, which already thinks it launched.
        record = load_record(root)
        if record["paused"] or record["failures"]:
            record["paused"] = False
            record["failures"] = 0
            save_record(root, record)
        harness, model, preset = _choose(root, spec, keep=spec is None)
        plan_path = _write_plan(root, harness, model, preset)
        target = _launch(root, plan_path)
        try:
            _save_window(
                root,
                {
                    "schema_version": 1,
                    "session_name": target.session_name,
                    "session_id": target.session_id or "",
                    "window_id": target.window_id or "",
                    "harness": harness,
                    "model": model,
                },
            )
        except Exception:
            TmuxController().abandon_window(target)
            raise
        print(f"foil: master started ({_pair_label(harness, model)})")


def stop() -> None:
    root = directory()
    if not root.is_dir():
        raise FoilError("foil: master is not running")
    with exclusive_lock(_run(root) / "lock"):
        window = _load_window(root)
        if not _alive(window):
            raise FoilError("foil: master is not running")
        target = _target(window)
        try:
            stopped = TmuxController().stop_verified(MARKER_FLEET, MARKER_SEAT, target)
        except TmuxError as exc:
            raise FoilError("foil: could not stop master") from exc
        if not stopped:
            raise FoilError("foil: master is not running")
        print("foil: master stopped")


def renew(spec: str | None) -> None:
    root = directory()
    _ensure(root)
    with exclusive_lock(_run(root) / "lock"):
        harness, model, preset = _choose(root, spec, keep=spec is None)
        _write_plan(root, harness, model, preset)
        window = _load_window(root)
        window.update({"harness": harness, "model": model, "schema_version": 1})
        if window.get("window_id"):
            _save_window(root, window)
        else:
            _save_window(
                root,
                {
                    "schema_version": 1,
                    "session_name": "",
                    "session_id": "",
                    "window_id": "",
                    "harness": harness,
                    "model": model,
                },
            )
        if _alive(window):
            write_bytes(_run(root) / "renew", b"")
        print(f"foil: master harness {_pair_label(harness, model)}")


def _document(sender: str, when: str, text: str, project: str, fleet: str) -> str:
    body = text if text.endswith("\n") else f"{text}\n"
    lines = [
        "---",
        "contract: mail/v1",
        f"from: {sender}",
        "to: master",
        f"time: {when}",
    ]
    if project:
        lines.append(f"project: {project}")
    if fleet:
        lines.append(f"fleet: {fleet}")
    lines += ["---", "", body]
    return "\n".join(lines)


def deliver(text: str, project: Path | None) -> Path:
    """Append one message to the store mailbox. A worker is refused."""

    if os.environ.get("FOIL_MASTER") == "1" and not in_seat():
        sender, key, fleet = "master", "", ""
    else:
        sender = actor(project)
        if sender != "lead" and (in_seat() or sender != "user"):
            raise FoilError("foil: only a lead may mail master")
        if sender == "lead" and project is not None:
            key, fleet = project_key(common_dir(project)), fleet_id()
        else:
            key, fleet = "", ""
    scan(text)
    root = directory()
    _ensure(root)
    when = datetime.now(UTC)
    rendered = _document(sender, when.strftime("%Y-%m-%dT%H:%M:%SZ"), text, key, fleet)
    scan(rendered)
    token = when.strftime("%Y%m%dT%H%M%SZ")
    payload = rendered.encode("utf-8")
    folder = root / "mail"
    for _ in range(8):
        path = folder / f"{token}-{sender}-{secrets.token_hex(4)}.md"
        if path.exists() or path.is_symlink():
            continue
        try:
            create_exclusive(path, payload)
        except FileExistsError:
            continue
        except OSError:
            if path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
            raise
        return path
    raise FoilError("foil: could not write mail")


def identities() -> list[str] | None:
    """Inbox identities across projects. One is `project`, `fleet`, and `subject`.

    None means the read failed. An empty list is a real empty inbox. A failure
    must not be stored as the seen set.
    """

    found = []
    try:
        items = collect_projects()
    except (FoilError, OSError):
        return None
    for item in items:
        found.append("\x1f".join((item["project"], item["fleet"], item["subject"])))
    return found


def conversation(root: Path) -> list[Path]:
    folder = root / "mail"
    if folder.is_symlink() or not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.md"):
        if path.is_symlink() or not path.is_file() or mail_name(path.name) is None:
            continue
        found.append(path)
    found.sort(key=lambda path: path.name)
    return found


def _cap(text: str, limit: int) -> str:
    raw = text.encode()
    if len(raw) <= limit:
        return text
    kept = raw[:limit].decode(errors="ignore")
    return kept + "\n... truncated\n"


def _file(root: Path, name: str) -> str:
    path = root / name
    if path.is_symlink() or not path.is_file():
        return "(missing)\n"
    try:
        return _cap(path.read_text(encoding="utf-8"), FILE_CAP)
    except (OSError, UnicodeDecodeError):
        return "(unreadable)\n"


def _watch_line(line: str) -> dict[str, str] | None:
    if not line.startswith("- "):
        return None
    ident, sep, tail = line[2:].partition(" ")
    if not sep or not _WATCH_ID.fullmatch(ident):
        return None
    when, sep, on = tail.partition(" ")
    if not sep or not _WATCH_TIME.fullmatch(when):
        return None
    text = on.strip()
    if not text or len(text) > _ON_CAP or any(ord(char) < 32 for char in text):
        return None
    return {"id": ident, "check": when, "on": text}


def watch_items(root: Path) -> list[dict[str, str]]:
    """Watch lines, first id wins. A missing or unreadable file is none."""

    path = root / "watches.md"
    if path.is_symlink() or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    found = []
    seen: set[str] = set()
    for line in text.splitlines():
        item = _watch_line(line)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        found.append(item)
    return found


def due_checks(root: Path, now: datetime | None = None) -> list[dict[str, str]]:
    """Watch lines whose check time has arrived. An unreadable file is none."""

    moment = now or datetime.now(UTC)
    found = []
    for item in watch_items(root):
        try:
            when = datetime.strptime(item["check"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if when <= moment:
            found.append(item)
    return found


def check_key(item: dict[str, str]) -> str:
    return f"{item['id']}\x1f{item['check']}"


def remember_checks(root: Path, record: dict[str, Any], prompted: list[dict[str, str]]) -> None:
    """Keep due checks a successful turn was given, while the line is unchanged."""

    current = {item["id"]: item["check"] for item in watch_items(root)}
    keys = set(record.get("seen_checks") or [])
    keys.update(check_key(item) for item in prompted)
    record["seen_checks"] = sorted(
        key for key in keys if current.get(key.partition("\x1f")[0]) == key.partition("\x1f")[2]
    )


def _act_kinds(text: str) -> set[str]:
    found: set[str] = set()
    section = False
    for line in text.splitlines():
        if line.startswith("## "):
            section = line == "## Act"
            continue
        if section and line.startswith("- "):
            kind = line[2:].strip()
            if kind in KINDS:
                found.add(kind)
    return found


def acting(root: Path | None = None) -> set[str]:
    """Kinds the human switched to act. A missing or unreadable file is none."""

    path = (root or directory()) / "mandate.md"
    if path.is_symlink() or not path.is_file():
        return set()
    try:
        return _act_kinds(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return set()


def _render_act(text: str, kinds: set[str]) -> str:
    kept: list[str] = []
    index = 0
    lines = text.splitlines()
    while index < len(lines):
        if lines[index] == "## Act":
            index += 1
            while index < len(lines) and not lines[index].startswith("## "):
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    while kept and not kept[-1]:
        kept.pop()
    block = ["## Act", ""]
    block.extend(f"- {kind}" for kind in KINDS if kind in kinds)
    body = "\n".join(kept + ["", *block] if kept else block)
    return body + "\n"


def set_mode(kind: str, mode: str) -> None:
    """Record one proactive kind as act or observe. The human's other words stay."""

    if os.environ.get("FOIL_MASTER") == "1" and not in_seat():
        raise FoilError("foil: the master does not set the mode")
    if kind not in KINDS or mode not in {"observe", "act"}:
        raise FoilError(f"foil: unknown mode '{shown(kind if kind not in KINDS else mode)}'")
    root = directory()
    _ensure(root)
    path = root / "mandate.md"
    with exclusive_lock(_run(root) / "mandate.lock"):
        if path.is_symlink():
            raise FoilError("foil: refusing symlink")
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError) as exc:
            raise FoilError("foil: mandate cannot be read") from exc
        kinds = _act_kinds(text)
        if mode == "act":
            kinds.add(kind)
        else:
            kinds.discard(kind)
        write_bytes(path, _render_act(text, kinds).encode())
    print(f"foil: {kind} {mode}")


def observe(kind: str) -> None:
    """Refuse a proactive command while the master has not been switched to act."""

    if os.environ.get("FOIL_MASTER") != "1" or in_seat():
        return
    if kind in acting():
        return
    raise FoilError(f"foil: observe {kind}")


def removed_path(root: Path) -> Path:
    return _run(root) / "removed.json"


def read_removed(root: Path) -> list[dict[str, str]] | None:
    """Removals not yet given to a successful turn.

    None means the file is there and is not that list. A missing file is empty.
    """

    path = removed_path(root)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    found = []
    for item in data:
        if not isinstance(item, dict):
            return None
        ident, when, on = item.get("id"), item.get("check"), item.get("on")
        if not all(isinstance(value, str) for value in (ident, when, on)):
            return None
        parsed = _watch_line(f"- {ident} {when} {on}")
        if parsed is None:
            return None
        found.append(parsed)
    return found


def _write_removed(root: Path, items: list[dict[str, str]]) -> None:
    payload = (json.dumps(items, ensure_ascii=False, sort_keys=True) + "\n").encode()
    write_bytes(removed_path(root), payload)


def drop_removed(root: Path, ids: list[str]) -> None:
    """Drop the removals a successful turn was given. Later ones stay."""

    with exclusive_lock(_run(root) / "watches.lock"):
        current = read_removed(root)
        if not current:
            return
        gone = set(ids)
        _write_removed(root, [item for item in current if item["id"] not in gone])


def unwatch(watch_id: str) -> None:
    """Remove one watch and record it for the master's next turn."""

    if not _WATCH_ID.fullmatch(watch_id):
        raise FoilError(f"foil: unknown watch '{shown(watch_id)}'")
    root = directory()
    _ensure(root)
    with exclusive_lock(_run(root) / "watches.lock"):
        path = root / "watches.md"
        if path.is_symlink():
            raise FoilError("foil: refusing symlink")
        try:
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError) as exc:
            raise FoilError("foil: watches cannot be read") from exc
        kept: list[str] = []
        removed: dict[str, str] | None = None
        for line in text.splitlines():
            item = _watch_line(line)
            if item is not None and item["id"] == watch_id:
                if removed is None:
                    removed = item
                continue
            kept.append(line)
        if removed is None:
            raise FoilError(f"foil: unknown watch '{shown(watch_id)}'")
        body = ("\n".join(kept) + "\n") if kept else ""
        write_bytes(path, body.encode())
        current = read_removed(root) or []
        current = [item for item in current if item["id"] != watch_id]
        current.append(removed)
        _write_removed(root, current)
    print(f"foil: removed watch {watch_id}")


def _removed_block(items: list[dict[str, str]] | None) -> str:
    if items is None:
        return "(unreadable)"
    if not items:
        return "(none)"
    return "\n".join(f"- {item['id']} {item['check']} {item['on']}" for item in items)


def charter_text() -> str:
    return files("foil").joinpath("defaults", "skills", "master.md").read_text(encoding="utf-8")


def seed(
    root: Path,
    mail: list[Path],
    new_ids: list[str],
    removed: Any = _READ,
    checks: list[dict[str, str]] | None = None,
) -> str:
    """The whole prompt for one fresh session."""

    try:
        brief = format_brief(collect_brief_projects(), projects_named=True)
    except (FoilError, OSError):
        brief = ""
    if removed is _READ:
        removed = read_removed(root)
    parts = [
        "# Charter",
        charter_text().rstrip("\n"),
        "",
        "# Brief",
        _cap(brief, BRIEF_CAP).rstrip("\n") if brief else "(none)",
        "",
        "# Log",
        _file(root, "log.md").rstrip("\n"),
        "",
        "# Watches",
        _file(root, "watches.md").rstrip("\n"),
        "",
        "# Removed watches",
        _removed_block(removed),
        "",
        "# Mandate",
        _file(root, "mandate.md").rstrip("\n"),
        "",
        "# Conversation",
    ]
    latest = conversation(root)[-LAST_MAIL:]
    if not latest:
        parts.append("(none)")
    for path in latest:
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            body = ""
        parts += [f"--- {path.name}", _cap(body, BODY_CAP).rstrip("\n")]
    parts += ["", "# Wake"]
    due = checks or []
    if not mail and not new_ids and not due:
        parts.append("(none)")
    for path in mail:
        parts.append(f"mail: {path.name}")
    for item in due:
        parts.append(f"check: {item['id']} {item['check']} {item['on']}")
    for item in new_ids:
        project, fleet, subject = (item.split("\x1f") + ["", ""])[:3]
        parts.append(f"inbox: {project} {fleet} {subject}")
        if subject != "goal" or not project or not fleet:
            continue
        try:
            block = finish_block(checkout_for_key(project), fleet, project)
        except (FoilError, OSError):
            continue
        parts += ["finish:", block]
    return "\n".join(parts) + "\n"


class MasterRunner:
    """The seat runner's loop for the master plan: fresh session, one-second poll."""

    def __init__(self, plan_path: Path):
        self.plan_path = plan_path
        self.plan = load_plan(plan_path)
        self.root = Path(self.plan["root"])
        self.record = load_record(self.root)
        self._mail_before: set[str] = set()
        self._removed_ids: list[str] = []
        self._lock = threading.Lock()
        if any(item.get("open") for item in self.record["reads"]):
            close_read(self.record, [])
            self.save()
        self.environment = dict(self.plan["env"])
        for name in self.plan["env_forward"]:
            value = os.environ.get(name)
            if value is not None and "\x00" not in value:
                self.environment[name] = value
        self.child = None

    def save(self) -> None:
        with self._lock:
            save_record(self.root, self.record)

    def stop(self, *_args: Any) -> None:
        if self.child is not None and self.child.pid:
            _end_group(self.child.pid)
        raise SystemExit(0)

    def _child(self, child) -> None:
        self.child = child
        if child is None:
            self.record["pgid"] = 0
            return
        self.record["pgid"] = child.pid
        self.save()

    def _relay(self, child) -> None:
        key = self.plan["session_key"]
        out = sys.stdout.buffer
        if child.stdout is None:
            return
        for raw in child.stdout:
            out.write(raw)
            out.flush()
            try:
                line = json.loads(raw)
            except ValueError:
                continue
            value = line.get(key) if isinstance(line, dict) else None
            if not (isinstance(value, str) and SAFE_ID.fullmatch(value)):
                continue
            with self._lock:
                if self.record["session_id"]:
                    continue
                self.record["session_id"] = value
                remember_session(
                    self.record,
                    session_id=value,
                    harness=self.plan["harness"],
                    model=self.plan["model"],
                )
                save_record(self.root, self.record)

    def _renew(self) -> None:
        marker = _run(self.root) / "renew"
        if marker.is_symlink() or not marker.is_file():
            return
        marker.unlink()
        os.execv(sys.executable, [sys.executable, "-P", "-m", "foil.runner", str(self.plan_path)])

    def _paused(self) -> bool:
        if not self.record["paused"]:
            return False
        marker = _run(self.root) / "resume"
        if marker.is_symlink() or not marker.is_file():
            return True
        marker.unlink()
        self.record["paused"] = False
        self.record["failures"] = 0
        self.save()
        return False

    def _turn(self, prompt: str, session: str) -> int:
        values = {"{prompt}": prompt, "{session_id}": session}
        argv = [values.get(token, token) for token in self.plan["start"]]
        learn = bool(self.plan["session_key"]) and not session
        print("foil: turn started (start)", flush=True)
        return execute_turn(
            argv,
            cwd=self.plan["cwd"],
            env=self.environment,
            timeout=self.plan["turn_timeout"],
            learn=learn,
            relay=self._relay,
            on_child=self._child,
            end_group=_end_group,
        )

    def loop(self) -> None:
        while True:
            self._renew()
            if self._paused():
                time.sleep(POLL)
                continue
            if not self.record["baselined"]:
                found = identities()
                if found is not None:
                    self.record["seen_inbox"] = sorted(set(found))
                    self.record["baselined"] = True
                    self.save()
                current = found
            else:
                current = identities()
            seen = set(self.record["seen_inbox"])
            fresh_ids = [] if current is None else [item for item in current if item not in seen]
            known = {path.name: path for path in conversation(self.root)}
            pending = [
                known[name]
                for name in self.record["inflight"]
                if name in known
            ]
            delivered = set(self.record["delivered"]) | set(self.record["inflight"])
            new_mail = [
                path
                for path in conversation(self.root)
                if path.name not in delivered
            ]
            removed = read_removed(self.root)
            wake_removed = removed if removed else []
            seen_checks = set(self.record["seen_checks"])
            fresh_checks = [
                item for item in due_checks(self.root) if check_key(item) not in seen_checks
            ]
            if (
                not pending
                and not new_mail
                and not fresh_ids
                and not self.record["failures"]
                and not wake_removed
                and not fresh_checks
            ):
                if current is not None and set(current) != seen:
                    self.record["seen_inbox"] = sorted(set(current))
                    self.save()
                time.sleep(POLL)
                continue
            batch = pending + [path for path in new_mail if path not in pending]
            session = str(uuid.uuid4()) if not self.plan["session_key"] else ""
            self.record["session_id"] = session
            if session:
                remember_session(
                    self.record,
                    session_id=session,
                    harness=self.plan["harness"],
                    model=self.plan["model"],
                )
            self.record["inflight"] = [path.name for path in batch]
            self.record["turn_started"] = time.time()
            self._mail_before = master_mail_names(self.root)
            open_read(self.record, [path.name for path in batch])
            self.save()
            prompt = seed(self.root, batch, fresh_ids, removed, fresh_checks)
            self._removed_ids = [item["id"] for item in wake_removed]
            code = self._turn(prompt, session)
            self.record["turn_started"] = None
            self.record["last_exit"] = code
            wrote = sorted(master_mail_names(self.root) - self._mail_before)
            close_read(self.record, wrote)
            # `wrote` is delivered mail, including when this turn exits non-zero.
            delivered = set(self.record["delivered"]) | set(wrote)
            if code == 0:
                delivered |= set(self.record["inflight"])
                self.record["delivered"] = sorted(delivered)
                self.record["inflight"] = []
                self.record["failures"] = 0
                if current is not None:
                    self.record["seen_inbox"] = sorted(set(current))
                remember_checks(self.root, self.record, fresh_checks)
                self.record["started"] = True
                self.save()
                drop_removed(self.root, self._removed_ids)
                continue
            self.record["delivered"] = sorted(delivered)
            self.record["failures"] += 1
            waits = self.plan["retry"]
            if self.record["failures"] > len(waits):
                self.record["paused"] = True
                self.save()
                continue
            self.save()
            deadline = time.monotonic() + waits[self.record["failures"] - 1]
            while time.monotonic() < deadline:
                self._renew()
                time.sleep(min(POLL, max(0.0, deadline - time.monotonic())))


def _end_group(pgid: int) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except OSError:
                return
            time.sleep(0.1)
