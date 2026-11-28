import json
import sqlite3
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

OPENCODE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS session (id TEXT PRIMARY KEY);"
    "CREATE TABLE IF NOT EXISTS message (id TEXT PRIMARY KEY, session_id TEXT,"
    " time_created INTEGER, data TEXT);"
    "CREATE TABLE IF NOT EXISTS part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT,"
    " time_created INTEGER, data TEXT);"
)


def append(path: Path, *rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("".join(json.dumps(row) + "\n" for row in rows))


def location(name: str, home: Path, cwd: str, session: str, day: str) -> Path | None:
    if name == "foil-fake":
        return home / ".foil-fake" / "sessions" / f"{session}.jsonl"
    if name == "claude":
        encoded = "".join(char if char.isalnum() else "-" for char in cwd)
        return home / ".claude" / "projects" / encoded / f"{session}.jsonl"
    if name == "codex":
        return home / ".codex" / "sessions" / day / f"rollout-{session}.jsonl"
    if name == "opencode":
        return home / ".local" / "share" / "opencode" / "opencode.db"
    if name == "grok":
        return home / ".grok" / "sessions" / quote(cwd, safe="") / session / "updates.jsonl"
    return None


def _codex_row(kind, payload):
    return {"timestamp": datetime.now(UTC).isoformat(), "type": kind, "payload": payload}


def _grok_row(update_kind, **payload):
    return {"method": "session/update", "params": {
        "update": {"sessionUpdate": update_kind, **payload},
    }}


def _fake_row(role, kind="message", text="", tool="", usage=None):
    return {"role": role, "kind": kind, "text": text, "tool": tool, "usage": usage}


def _opencode_row(path, session, role, text, tools):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(OPENCODE_SCHEMA)
        db.execute("INSERT OR IGNORE INTO session VALUES (?)", (session,))
        last = db.execute(
            "SELECT MAX(time_created) FROM part WHERE session_id = ?", (session,)
        ).fetchone()[0] or 0
        timestamp = max(time.time_ns() // 1_000_000, last + 1)
        message_id = uuid.uuid4().hex
        message = {"id": message_id, "role": role}
        if role == "assistant":
            message["tokens"] = {"input": 1, "output": 1}
        db.execute("INSERT INTO message VALUES (?, ?, ?, ?)",
                   (message_id, session, timestamp, json.dumps(message)))
        parts = [{"type": "text", "text": text}, *(
            {"type": "tool", "tool": tool, "state": {"input": args}} for tool, args in tools
        )]
        db.executemany("INSERT INTO part VALUES (?, ?, ?, ?, ?)", (
            (uuid.uuid4().hex, message_id, session, timestamp + index, json.dumps(part))
            for index, part in enumerate(parts)
        ))


def write_turn(name, path, session, cwd, role, text, tools=(), *, first=False):
    if path is None:
        return
    assistant = role == "assistant"
    if name == "foil-fake":
        rows = [_fake_row(role, text=text)]
        if assistant:
            rows.extend(_fake_row("tool", "tool", str(next(iter(args.values()))), tool)
                        for tool, args in tools)
            rows.append(_fake_row("assistant", "usage", usage={"input": 1, "output": 1}))
    elif name == "claude":
        message = {"role": role, "content": text}
        if assistant:
            message["content"] = [{"type": "text", "text": text}, *(
                {"type": "tool_use", "name": tool, "input": args} for tool, args in tools
            )]
            message["usage"] = {"input_tokens": 1, "output_tokens": 1}
        row = {"type": role, "message": message}
        if not assistant:
            row["sessionId"] = session
        rows = [row]
    elif name == "codex":
        rows = [_codex_row("session_meta", {"id": session, "cwd": cwd})] if first else []
        rows.append(_codex_row("response_item", {"type": "message", "role": role, "content": [
            {"type": "output_text" if assistant else "input_text", "text": text}
        ]}))
        rows.extend(_codex_row("response_item", {
            "type": "function_call", "name": tool, "arguments": json.dumps(args),
        }) for tool, args in tools)
        if assistant:
            rows.append(_codex_row("token_usage_record", {"input_tokens": 1, "output_tokens": 1}))
    elif name == "grok":
        rows = [_grok_row("agent_message_chunk" if assistant else "user_message_chunk",
                          content={"type": "text", "text": text})]
        rows.extend(_grok_row("tool_call", title=tool, kind="execute", rawInput=args)
                    for tool, args in tools)
        if assistant:
            rows.append(_grok_row("usage_update", used=2, size=1000))
    else:
        _opencode_row(path, session, role, text, tools)
        return
    append(path, *rows)
