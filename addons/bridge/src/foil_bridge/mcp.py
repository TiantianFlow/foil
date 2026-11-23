"""MCP on stdio and on ``POST /mcp``. The tools call ``foil.api`` as ``user``.

Nothing here kills, archives, merges, or changes settings. Those stay cards
on the page. ``talk_to_master`` writes the same mailbox the home reads.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, TextIO

from foil.api import brief, inbox
from foil.board import mail_name
from foil.board_ops import split_front_matter
from foil.errors import FoilError, shown
from foil.inbox import collect_brief, projects
from foil.lifecycle import seat_rows
from foil.master import directory, load_record
from foil.store import FLEET_ID, fleet_ids, load_registry
from foil_bridge import __version__
from foil_bridge.home import post_answer, post_message
from foil_bridge.transcript import read as read_transcript

VERSIONS = ("2025-03-26", "2025-06-18", "2025-11-25")
CURRENT = VERSIONS[-1]
MAX_BODY = 64 * 1024
REPLY_CAP = 16 * 1024
WAIT = 600
_TALK = threading.Lock()
INSTRUCTIONS = (
    "This is the same master as the Foil app. talk_to_master writes to that "
    "conversation as the human and returns the master's reply. brief, inbox, "
    "fleet, and agent_transcript are reads. answer answers one closed question "
    "by id. Kill, archive, merge, and settings stay cards in the app."
)
_READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "talk_to_master",
        "description": (
            "Write a message to the master as the human and return the master's "
            "reply. The app shows the same conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "annotations": _WRITE,
    },
    {
        "name": "brief",
        "description": "Each fleet's goal, state, journal, questions, and stuck items.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "inbox",
        "description": "What needs the human, across projects, in the fixed order.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ,
    },
    {
        "name": "fleet",
        "description": "Fleets in the store: goal, state, and seats. Read only.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}, "fleet": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": _READ,
    },
    {
        "name": "agent_transcript",
        "description": "One agent's conversation. Summary by default, with secrets masked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seat": {"type": "string"},
                "project": {"type": "string"},
                "fleet": {"type": "string"},
                "level": {"type": "string", "enum": ["summary", "messages", "tools", "all"]},
            },
            "required": ["seat"],
            "additionalProperties": False,
        },
        "annotations": _READ,
    },
    {
        "name": "answer",
        "description": "Answer one closed question by id, as the human. The first answer stays.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "fleet": {"type": "string"},
                "id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["project", "fleet", "id", "text"],
            "additionalProperties": False,
        },
        "annotations": _WRITE,
    },
)
_NAMES = {tool["name"] for tool in _TOOLS}


def accepts(header: str) -> bool:
    """True when both MCP response types are listed."""

    found = {part.split(";", 1)[0].strip().lower() for part in header.split(",") if part.strip()}
    return "application/json" in found and "text/event-stream" in found


def _line(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: object, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _text(body: str, *, failed: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {"content": [{"type": "text", "text": body}]}
    if failed:
        value["isError"] = True
    return value


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _quiet(function):
    with contextlib.redirect_stdout(io.StringIO()):
        return function()


def _arguments(
    raw: object, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) - set(required) - set(optional):
        raise FoilError("foil-bridge: unexpected argument")
    found: dict[str, str] = {}
    for key in required:
        value = raw.get(key)
        if not isinstance(value, str):
            raise FoilError(f"foil-bridge: {key} is required")
        found[key] = value
    for key in optional:
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, str):
            raise FoilError("foil-bridge: unexpected argument")
        found[key] = value
    return found


def _cap(text: str) -> str:
    raw = text.encode()
    if len(raw) <= REPLY_CAP:
        return text
    return raw[:REPLY_CAP].decode(errors="ignore") + "\n... truncated\n"


def _master_body(path) -> str | None:
    named = mail_name(path.name)
    if named is None or named[1] != "master":
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    matter, body = split_front_matter(text)
    if matter is None or matter.get("contract") != "mail/v1":
        return None
    return body


def talk(text: str, stop: threading.Event) -> str:
    """Write as ``user``, then wait for the turn that reads that file.

    A cancel while this call waits behind another returns before the write.
    """

    while not _TALK.acquire(timeout=0.05):
        if stop.is_set():
            raise FoilError("foil-bridge: cancelled")
    try:
        return _talk(text, stop)
    finally:
        _TALK.release()


def _reply_names(record: dict, name: str) -> list[str] | None:
    """Master mail names from the finished turn that read ``name``.

    None means that turn has not finished. An empty list means it wrote none.
    """

    if name not in record.get("delivered", []):
        return None
    found: list[str] | None = None
    for item in record.get("reads", []):
        if not isinstance(item, dict) or item.get("open"):
            continue
        names = item.get("names")
        if not isinstance(names, list) or name not in names:
            continue
        wrote = item.get("wrote")
        found = [mail for mail in wrote if isinstance(mail, str)] if isinstance(wrote, list) else []
    return found


def _talk(text: str, stop: threading.Event) -> str:
    if stop.is_set():
        raise FoilError("foil-bridge: cancelled")
    name = post_message(text)
    deadline = time.monotonic() + WAIT
    root = directory()
    while True:
        wrote = _reply_names(load_record(root), name)
        if wrote is not None:
            bodies = []
            for mail in sorted(wrote):
                body = _master_body(root / "mail" / mail)
                if body is not None:
                    bodies.append(body if body.endswith("\n") else f"{body}\n")
            if not bodies:
                raise FoilError("foil-bridge: the master has not replied")
            return _cap("".join(bodies))
        if stop.is_set():
            raise FoilError("foil-bridge: cancelled")
        if time.monotonic() >= deadline:
            raise FoilError("foil-bridge: the master has not replied")
        stop.wait(0.05)


def _brief() -> str:
    records = _quiet(lambda: brief(None, "main", "user", all_projects=True, as_json=True))
    return _dump(records)


def _inbox() -> str:
    items = _quiet(lambda: inbox(None, "main", "user", all_projects=True, as_json=True))
    return _dump(items)


def _seats(root, fleet: str) -> list[dict[str, str]]:
    try:
        registry = load_registry(root, fleet)
        rows = seat_rows(root, registry, fleet=fleet)
    except (FoilError, OSError):
        return []
    return [
        {
            "name": row["name"],
            "template": row["template"],
            "state": row["state"],
            "branch": row["branch"],
        }
        for row in rows
    ]


def _fleet(arguments: dict[str, str]) -> str:
    project = arguments.get("project")
    fleet = arguments.get("fleet")
    if fleet is not None and project is None:
        raise FoilError("foil-bridge: fleet needs a project")
    if fleet is not None and not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    chosen = projects()
    if project is not None:
        chosen = [(key, root) for key, root in chosen if key == project]
        if not chosen:
            raise FoilError(f"foil: unknown project '{shown(project)}'")
    rows = []
    for key, root in chosen:
        present = fleet_ids(root)
        if fleet is not None and fleet not in present:
            raise FoilError(f"foil: unknown fleet '{shown(fleet)}'")
        ids = [fleet] if fleet is not None else present
        for fleet_id in ids:
            record = collect_brief(root, [fleet_id], key)[0]
            rows.append(
                {
                    "project": key,
                    "fleet": fleet_id,
                    "goal": record["goal"],
                    "state": record["state"],
                    "paused": record["paused"],
                    "seats": _seats(root, fleet_id),
                }
            )
    return _dump(rows)


def _transcript(arguments: dict[str, str]) -> str:
    try:
        payload = read_transcript(
            arguments["seat"],
            project=arguments.get("project"),
            fleet=arguments.get("fleet"),
            level=arguments.get("level", "summary"),
        )
    except FoilError as exc:
        text = str(exc)
        if text.startswith("foil:"):
            text = "foil-bridge:" + text[len("foil:") :]
        raise FoilError(text) from None
    return _dump(payload)


def _answer(arguments: dict[str, str]) -> str:
    _quiet(
        lambda: post_answer(
            arguments["project"], arguments["fleet"], arguments["id"], arguments["text"]
        )
    )
    return "answered"


def _invoke(name: str, arguments: object, stop: threading.Event) -> dict[str, Any]:
    if name not in _NAMES:
        raise KeyError(name)
    try:
        if name == "talk_to_master":
            body = talk(_arguments(arguments, ("text",))["text"], stop)
        elif name == "brief":
            _arguments(arguments, ())
            body = _brief()
        elif name == "inbox":
            _arguments(arguments, ())
            body = _inbox()
        elif name == "fleet":
            body = _fleet(_arguments(arguments, (), ("project", "fleet")))
        elif name == "agent_transcript":
            body = _transcript(_arguments(arguments, ("seat",), ("project", "fleet", "level")))
        else:
            body = _answer(_arguments(arguments, ("project", "fleet", "id", "text")))
    except FoilError as exc:
        return _text(str(exc), failed=True)
    return _text(body)


class Session:
    """One stdio client, or the stateless HTTP endpoint."""

    def __init__(self, *, strict: bool) -> None:
        self.strict = strict
        self.ready = not strict
        self._lock = threading.Lock()
        self._waiting: dict[object, threading.Event] = {}
        self._cancelled: set[object] = set()

    def cancel(self, request_id: object) -> None:
        if type(request_id) not in {str, int}:
            return
        with self._lock:
            self._cancelled.add(request_id)
            if len(self._cancelled) > 64:
                kept = set(self._waiting)
                kept.add(request_id)
                self._cancelled.intersection_update(kept)
            event = self._waiting.get(request_id)
        if event is not None:
            event.set()

    def _arm(self, request_id: object) -> threading.Event | None:
        event = threading.Event()
        with self._lock:
            if request_id in self._waiting:
                return None
            if request_id in self._cancelled:
                event.set()
            self._waiting[request_id] = event
        return event

    def _disarm(self, request_id: object, event: threading.Event) -> None:
        with self._lock:
            if self._waiting.get(request_id) is event:
                self._waiting.pop(request_id, None)
                self._cancelled.discard(request_id)

    def receive(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if not isinstance(method, str) or message.get("jsonrpc") != "2.0":
            return _error(None, -32600, "invalid request")
        if method.startswith("notifications/"):
            params = message.get("params")
            self._notify(method, params if isinstance(params, dict) else {})
            return None
        if "params" in message and not isinstance(message["params"], dict):
            request_id = message["id"] if type(message.get("id")) in {str, int} else None
            return _error(request_id, -32602, "invalid params")
        params = message.get("params") or {}
        request_id = message.get("id")
        if type(request_id) not in {str, int}:
            return _error(None, -32600, "invalid request")
        if self.strict and not self.ready and method not in {"initialize", "ping"}:
            return _error(request_id, -32600, "expected initialize")
        if method == "initialize":
            self.ready = True
            version = params.get("protocolVersion")
            chosen = version if isinstance(version, str) and version in VERSIONS else CURRENT
            return _result(
                request_id,
                {
                    "protocolVersion": chosen,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "foil-bridge", "version": __version__},
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [dict(tool) for tool in _TOOLS]})
        if method != "tools/call":
            return _error(request_id, -32601, "method not found")
        name = params.get("name")
        if not isinstance(name, str) or name not in _NAMES:
            return _error(request_id, -32602, "unknown tool")
        stop = self._arm(request_id)
        if stop is None:
            return _error(request_id, -32600, "request id is in use")
        try:
            try:
                value = _invoke(name, params.get("arguments", {}), stop)
            except Exception:
                return _error(request_id, -32603, "internal error")
        finally:
            self._disarm(request_id, stop)
        return _result(request_id, value)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/cancelled":
            self.cancel(params.get("requestId"))


_HTTP = Session(strict=False)


def http_post(raw: bytes) -> tuple[int, bytes]:
    """One HTTP JSON-RPC body. A notification is status 202 and an empty body."""

    try:
        message = json.loads(raw.decode() or "null")
    except (UnicodeError, json.JSONDecodeError):
        return 400, _line(_error(None, -32700, "parse error")).encode()
    if not isinstance(message, dict):
        return 400, _line(_error(None, -32600, "invalid request")).encode()
    response = _HTTP.receive(message)
    if response is None:
        return 202, b""
    return 200, _line(response).encode()


def _chunks(stdin: TextIO | None) -> Iterator[str]:
    if stdin is None:
        while True:
            line = sys.stdin.buffer.readline(MAX_BODY + 1)
            if not line:
                return
            yield line.decode("utf-8", "replace")
    while True:
        line = stdin.readline(MAX_BODY + 1)
        if not line:
            return
        yield line


def serve_stdio(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Speak MCP on stdin and stdout until stdin closes."""

    session = Session(strict=True)
    lock = threading.Lock()

    def emit(payload: dict[str, Any]) -> None:
        text = _line(payload) + "\n"
        with lock:
            if stdout is None:
                sys.stdout.buffer.write(text.encode())
                sys.stdout.buffer.flush()
            else:
                stdout.write(text)
                stdout.flush()

    def work(message: dict[str, Any]) -> None:
        try:
            response = session.receive(message)
        except Exception:
            request_id = message.get("id")
            if type(request_id) not in {str, int}:
                request_id = None
            response = _error(request_id, -32603, "internal error")
        if response is not None:
            try:
                emit(response)
            except (BrokenPipeError, OSError):
                return

    for line in _chunks(stdin):
        if len(line) > MAX_BODY:
            emit(_error(None, -32700, "parse error"))
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            emit(_error(None, -32700, "parse error"))
            continue
        if not isinstance(message, dict):
            emit(_error(None, -32600, "invalid request"))
            continue
        method = message.get("method")
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else ""
        if method == "tools/call" and name == "talk_to_master":
            threading.Thread(target=work, args=(message,), daemon=True).start()
            continue
        if isinstance(method, str) and method.startswith("notifications/"):
            session.receive(message)
            continue
        response = session.receive(message)
        if response is not None:
            try:
                emit(response)
            except (BrokenPipeError, OSError):
                return 0
    return 0
