"""One reader per transcript format id.

A reader takes a session id, the working directory, and turn times, and
returns normalized events. None means the file is missing or unreadable.
The caller masks and chooses a verbosity. This module does not print.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

LIMIT = 8 * 1024 * 1024
_WALK = 200

Event = dict[str, Any]


def read_session(
    format_id: str,
    session_id: str,
    cwd: str,
    root: Path,
    *,
    started: str = "",
    turn_times: list[str] | None = None,
) -> tuple[list[Event], bool] | None:
    """Events from one session file, and whether the file was cut at 8 MiB.

    None when this format has no reader or the file cannot be read. A file
    that yields no event is None too, so the caller can use the pane.
    """

    reader = _READERS.get(format_id)
    if reader is None or not session_id or "/" in session_id or "\\" in session_id:
        return None
    found = reader(session_id, cwd, root, started, turn_times or [])
    if found is None:
        return None
    events, truncated = found
    if not events:
        return None
    return events, truncated


def _event(
    role: str,
    kind: str,
    text: str = "",
    tool: str = "",
    usage: dict[str, int] | None = None,
) -> Event:
    return {"role": role, "kind": kind, "text": text, "tool": tool, "usage": usage}


def contained(root: Path, path: Path) -> bool:
    """True when ``path`` stays under ``root`` and no component is a symlink.

    The harness root itself may be a symlink. A directory or file under it may not.
    """

    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    if any(part in {"..", ""} for part in relative.parts):
        return False
    current = root
    for part in relative.parts:
        if part == ".":
            continue
        current = current / part
        try:
            if current.is_symlink():
                return False
        except OSError:
            return False
    try:
        current.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _load(path: Path, root: Path) -> tuple[str, bool] | None:
    if not contained(root, path) or path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            blob = handle.read(LIMIT + 1)
    except OSError:
        return None
    truncated = len(blob) > LIMIT
    if truncated:
        blob = blob[:LIMIT]
        head, sep, _rest = blob.rpartition(b"\n")
        if sep:
            blob = head
    return blob.decode("utf-8", errors="replace"), truncated


def _objects(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if "content" in value:
            return _text(value["content"])
    return ""


def _dump(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        return ""


def _ints(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    nested = raw.get("total_token_usage")
    if isinstance(nested, dict):
        found = _ints(nested)
        if found:
            return found
    left = raw.get("input_tokens", raw.get("input"))
    right = raw.get("output_tokens", raw.get("output"))
    if type(left) is int and type(right) is int:
        return {"input": left, "output": right}
    return None


def _usage_event(raw: Any) -> Event | None:
    usage = _ints(raw)
    if usage is None:
        return None
    return _event("assistant", "usage", usage=usage)


def _tool(name: str, body: str) -> Event:
    return _event("tool", "tool", text=body, tool=name or "tool")


def _dirs(directory: Path, *, depth: int, root: Path) -> list[Path]:
    if not contained(root, directory) or not directory.is_dir():
        return []
    found: list[Path] = []

    def walk(folder: Path, level: int) -> None:
        if len(found) >= _WALK or level > depth:
            return
        try:
            children = list(folder.iterdir())
        except OSError:
            return
        for child in children:
            if not contained(root, child) or not child.is_dir():
                continue
            found.append(child)
            if len(found) >= _WALK:
                return
            walk(child, level + 1)

    walk(directory, 1)
    return found


def _claude(session_id: str, cwd: str, root: Path, _started: str, _times: list[str]):
    encoded = "".join(char if char.isalnum() else "-" for char in cwd)
    direct = root / "projects" / encoded / f"{session_id}.jsonl"
    path = direct if contained(root, direct) and direct.is_file() else None
    if path is None:
        projects = root / "projects"
        if contained(root, projects) and projects.is_dir():
            for directory in _dirs(projects, depth=1, root=root):
                candidate = directory / f"{session_id}.jsonl"
                if contained(root, candidate) and candidate.is_file():
                    path = candidate
                    break
    if path is None:
        return None
    loaded = _load(path, root)
    if loaded is None:
        return None
    text, truncated = loaded
    events: list[Event] = []
    for row in _objects(text):
        if row.get("isMeta") or row.get("isSidechain"):
            continue
        kind = row.get("type")
        if kind == "summary" and isinstance(row.get("summary"), str):
            events.append(_event("system", "message", row["summary"]))
            continue
        if kind not in {"user", "assistant"}:
            continue
        message = row.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            blocks = content
        else:
            blocks = [{"type": "text", "text": _text(content)}]
        for block in blocks:
            if isinstance(block, str):
                if block:
                    who = "user" if kind == "user" else "assistant"
                    events.append(_event(who, "message", block))
                continue
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in {None, "text"} and kind == "user":
                piece = _text(block if block_type else content)
                if piece:
                    events.append(_event("user", "message", piece))
            elif block_type == "text":
                piece = _text(block)
                if piece:
                    events.append(_event("assistant", "message", piece))
            elif block_type == "thinking":
                piece = block.get("thinking") if isinstance(block.get("thinking"), str) else ""
                if piece:
                    events.append(_event("assistant", "thought", piece))
            elif block_type == "tool_use":
                name = block.get("name") if isinstance(block.get("name"), str) else "tool"
                events.append(_tool(name, _dump(block.get("input"))))
            elif block_type == "tool_result":
                events.append(_tool("result", _text(block.get("content"))))
        usage = _usage_event(message.get("usage"))
        if usage:
            events.append(usage)
    return events, truncated


def _codex_day(started: str, turn_times: list[str]) -> str:
    for value in (started, *turn_times):
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            return value[:10]
    return ""


def _codex(session_id: str, _cwd: str, root: Path, started: str, turn_times: list[str]):
    sessions = root / "sessions"
    path: Path | None = None
    day = _codex_day(started, turn_times)
    if day:
        year, month, date = day.split("-")
        folder = sessions / year / month / date
        if contained(root, folder) and folder.is_dir():
            path = _named(folder, session_id, root)
    if path is None:
        for directory in _dirs(sessions, depth=3, root=root):
            found = _named(directory, session_id, root)
            if found is not None:
                path = found
                break
    if path is None:
        return None
    loaded = _load(path, root)
    if loaded is None:
        return None
    text, truncated = loaded
    events: list[Event] = []
    saw_message = False
    saw_usage = False
    fallback: list[Event] = []
    fallback_usage: Event | None = None
    for row in _objects(text):
        kind = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if kind == "response_item":
            item = payload.get("type")
            if item == "message":
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                piece = ""
                content = payload.get("content")
                blocks = content if isinstance(content, list) else []
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") in {
                        "input_text",
                        "output_text",
                        "text",
                    }:
                        piece += _text(block)
                if piece:
                    events.append(_event(str(role), "message", piece))
                    saw_message = True
            elif item in {"function_call", "custom_tool_call"}:
                name = payload.get("name") if isinstance(payload.get("name"), str) else "tool"
                body = payload.get("arguments", payload.get("input"))
                events.append(_tool(name, _dump(body)))
            elif item == "function_call_output":
                events.append(_tool("result", _text(payload.get("output"))))
        elif kind == "token_usage_record":
            usage = _usage_event(payload)
            if usage:
                events.append(usage)
                saw_usage = True
        elif kind == "event_msg":
            inner = payload.get("type")
            if inner in {"user_message", "agent_message"}:
                piece = payload.get("message") if isinstance(payload.get("message"), str) else ""
                if piece:
                    role = "user" if inner == "user_message" else "assistant"
                    fallback.append(_event(role, "message", piece))
            elif inner == "token_count":
                info = payload.get("info")
                fallback_usage = _usage_event(info if isinstance(info, dict) else payload)
    if not saw_message:
        events = fallback + events
    if not saw_usage and fallback_usage:
        events.append(fallback_usage)
    return events, truncated


def _named(folder: Path, session_id: str, root: Path) -> Path | None:
    if not contained(root, folder) or not folder.is_dir():
        return None
    try:
        children = list(folder.iterdir())
    except OSError:
        return None
    suffix = f"-{session_id}.jsonl"
    for child in children:
        if not contained(root, child) or not child.is_file():
            continue
        if child.name.endswith(suffix) or child.name == f"{session_id}.jsonl":
            return child
    return None


def _opencode(session_id: str, _cwd: str, root: Path, _started: str, _times: list[str]):
    database = root / "opencode.db"
    if contained(root, database) and database.is_file():
        found = _opencode_db(database, session_id, root)
        if found is not None:
            return found
    folder = root / "storage"
    messages = _opencode_files(folder / "message" / session_id, root)
    if messages is None:
        return None
    events: list[Event] = []
    truncated = False
    ordered = sorted(messages, key=lambda item: (item[0], item[1]))
    for _stamp, _name, path in ordered:
        loaded = _load(path, root)
        if loaded is None:
            continue
        text, cut = loaded
        truncated = truncated or cut
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        message_id = message.get("id") if isinstance(message.get("id"), str) else path.stem
        parts = _opencode_files(folder / "part" / message_id, root)
        part_rows = []
        if parts:
            for stamp, name, part_path in sorted(parts, key=lambda item: (item[0], item[1])):
                del stamp, name
                part_loaded = _load(part_path, root)
                if part_loaded is None:
                    continue
                truncated = truncated or part_loaded[1]
                try:
                    part = json.loads(part_loaded[0])
                except json.JSONDecodeError:
                    continue
                if isinstance(part, dict):
                    part_rows.append(part)
        events.extend(_opencode_message(message, part_rows))
    return events, truncated


def _opencode_files(directory: Path, root: Path) -> list[tuple[int, str, Path]] | None:
    if not contained(root, directory) or not directory.is_dir():
        return None
    rows: list[tuple[int, str, Path]] = []
    try:
        children = list(directory.iterdir())
    except OSError:
        return None
    for child in children:
        if not contained(root, child) or not child.is_file() or child.suffix != ".json":
            continue
        try:
            stamp = child.stat().st_mtime_ns
        except OSError:
            continue
        rows.append((stamp, child.name, child))
    return rows


def _opencode_message(message: dict[str, Any], parts: list[dict[str, Any]]) -> list[Event]:
    role = message.get("role")
    if role not in {"user", "assistant"}:
        role = "assistant"
    events: list[Event] = []
    if not parts:
        piece = _text(message.get("text") or message.get("content"))
        if piece:
            events.append(_event(str(role), "message", piece))
    for part in parts:
        kind = part.get("type")
        if kind == "text":
            piece = part.get("text") if isinstance(part.get("text"), str) else ""
            if piece:
                events.append(_event(str(role), "message", piece))
        elif kind == "reasoning":
            piece = part.get("text") if isinstance(part.get("text"), str) else ""
            if piece:
                events.append(_event("assistant", "thought", piece))
        elif kind == "tool":
            name = part.get("tool") if isinstance(part.get("tool"), str) else "tool"
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            body = _dump(state.get("input"))
            output = state.get("output")
            if output is None:
                output = state.get("error")
            result = _text(output) if not isinstance(output, str) else output
            if result:
                body = f"{body}\n{result}" if body else result
            events.append(_tool(name, body))
    usage = _usage_event(message.get("tokens"))
    if usage:
        events.append(usage)
    return events


def _opencode_db(path: Path, session_id: str, root: Path) -> tuple[list[Event], bool] | None:
    if not contained(root, path):
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        try:
            present = connection.execute(
                "select 1 from session where id = ? limit 1", (session_id,)
            ).fetchone()
        except sqlite3.Error:
            return None
        if present is None:
            return None
        try:
            messages = connection.execute(
                "select id, data from message where session_id = ? order by time_created, id",
                (session_id,),
            ).fetchall()
            parts = connection.execute(
                "select message_id, data from part where session_id = ? "
                "order by time_created, id",
                (session_id,),
            ).fetchall()
        except sqlite3.Error:
            return None
    finally:
        connection.close()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for message_id, data in parts:
        part = _json_dict(data)
        if part is None:
            continue
        grouped.setdefault(str(message_id), []).append(part)
    events: list[Event] = []
    for message_id, data in messages:
        message = _json_dict(data)
        if message is None:
            continue
        if not isinstance(message.get("id"), str):
            message = {**message, "id": str(message_id)}
        events.extend(_opencode_message(message, grouped.get(str(message_id), [])))
    return events, False


def _json_dict(data: Any) -> dict[str, Any] | None:
    if isinstance(data, dict):
        return data
    if not isinstance(data, str):
        return None
    try:
        item = json.loads(data)
    except json.JSONDecodeError:
        return None
    return item if isinstance(item, dict) else None


def _grok(session_id: str, cwd: str, root: Path, _started: str, _times: list[str]):
    path = _grok_path(root, session_id, cwd)
    if path is None:
        return None
    loaded = _load(path, root)
    if loaded is None:
        return None
    text, truncated = loaded
    events: list[Event] = []
    pending: tuple[str, str] | None = None

    def flush() -> None:
        nonlocal pending
        if pending and pending[1]:
            role, piece = pending
            kind = "thought" if role == "thought" else "message"
            who = "assistant" if role != "user" else "user"
            events.append(_event(who, kind, piece))
        pending = None

    for row in _objects(text):
        params = row.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict):
            update = row.get("update") if isinstance(row.get("update"), dict) else row
        if not isinstance(update, dict):
            continue
        kind = update.get("sessionUpdate")
        if kind in {"user_message_chunk", "agent_message_chunk", "agent_thought_chunk"}:
            role = {
                "user_message_chunk": "user",
                "agent_thought_chunk": "thought",
            }.get(kind, "assistant")
            piece = _text(update.get("content"))
            if pending and pending[0] == role:
                pending = (role, pending[1] + piece)
            else:
                flush()
                pending = (role, piece)
            continue
        flush()
        if kind == "tool_call":
            name = update.get("title") if isinstance(update.get("title"), str) else ""
            if not name and isinstance(update.get("kind"), str):
                name = update["kind"]
            events.append(_tool(name or "tool", _dump(update.get("rawInput"))))
            call_id = update.get("toolCallId")
            if isinstance(call_id, str):
                events[-1]["call"] = call_id
        elif kind == "tool_call_update":
            result = _dump(update.get("rawOutput"))
            if not result:
                result = _text(update.get("content"))
            call_id = update.get("toolCallId")
            target = None
            if isinstance(call_id, str):
                for event in reversed(events):
                    if event.get("call") == call_id:
                        target = event
                        break
            if target is None:
                events.append(_tool("result", result))
            elif result:
                body = target["text"]
                target["text"] = f"{body}\n{result}" if body else result
    flush()
    for event in events:
        event.pop("call", None)
    return events, truncated


def _grok_path(root: Path, session_id: str, cwd: str) -> Path | None:
    direct = root / "sessions" / quote(cwd, safe="") / session_id / "updates.jsonl"
    if contained(root, direct) and direct.is_file():
        return direct
    sessions = root / "sessions"
    if not contained(root, sessions) or not sessions.is_dir():
        return None
    fallback: Path | None = None
    for group in _dirs(sessions, depth=1, root=root):
        candidate = group / session_id / "updates.jsonl"
        if not contained(root, candidate) or not candidate.is_file():
            continue
        marker = group / ".cwd"
        recorded = ""
        if contained(root, marker) and marker.is_file():
            try:
                recorded = marker.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                recorded = ""
        if not recorded:
            recorded = unquote(group.name)
        if recorded == cwd:
            return candidate
        fallback = fallback or candidate
    return fallback


def _fake(session_id: str, _cwd: str, root: Path, _started: str, _times: list[str]):
    path = root / "sessions" / f"{session_id}.jsonl"
    loaded = _load(path, root)
    if loaded is None:
        return None
    text, truncated = loaded
    events: list[Event] = []
    for row in _objects(text):
        role = row.get("role")
        kind = row.get("kind")
        if role not in {"user", "assistant", "tool", "system"}:
            continue
        if kind not in {"message", "tool", "usage", "thought"}:
            continue
        piece = row.get("text") if isinstance(row.get("text"), str) else ""
        tool = row.get("tool") if isinstance(row.get("tool"), str) else ""
        usage = _ints(row.get("usage"))
        events.append(_event(role, kind, piece, tool, usage))
    return events, truncated


_READERS = {
    "claude": _claude,
    "codex": _codex,
    "opencode": _opencode,
    "grok": _grok,
    "fake": _fake,
}
