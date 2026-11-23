"""The master: one runner per store, above every fleet (section 13).

The process is the seat runner. Each turn is a new session. A wake is mail
in the store mailbox, or an inbox identity that was not in the last set.
"""

from __future__ import annotations

import hashlib
import json
import os
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
        "baselined": False,
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
        if key in {"delivered", "inflight", "seen_inbox"}:
            value = (
                [item for item in value if isinstance(item, str)]
                if isinstance(value, list)
                else []
            )
        elif key == "sessions":
            value = value if isinstance(value, list) else []
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


def charter_text() -> str:
    return files("foil").joinpath("defaults", "skills", "master.md").read_text(encoding="utf-8")


def seed(root: Path, mail: list[Path], new_ids: list[str]) -> str:
    """The whole prompt for one fresh session."""

    try:
        brief = format_brief(collect_brief_projects(), projects_named=True)
    except (FoilError, OSError):
        brief = ""
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
    if not mail and not new_ids:
        parts.append("(none)")
    for path in mail:
        parts.append(f"mail: {path.name}")
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
        self.environment = dict(self.plan["env"])
        for name in self.plan["env_forward"]:
            value = os.environ.get(name)
            if value is not None and "\x00" not in value:
                self.environment[name] = value
        self.child = None
        self._lock = threading.Lock()

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
            if not pending and not new_mail and not fresh_ids and not self.record["failures"]:
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
            self.save()
            prompt = seed(self.root, batch, fresh_ids)
            code = self._turn(prompt, session)
            self.record["turn_started"] = None
            self.record["last_exit"] = code
            if code == 0:
                already = set(self.record["delivered"])
                self.record["delivered"] = sorted(already | set(self.record["inflight"]))
                self.record["inflight"] = []
                self.record["failures"] = 0
                if current is not None:
                    self.record["seen_inbox"] = sorted(set(current))
                self.record["started"] = True
                self.save()
                continue
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
