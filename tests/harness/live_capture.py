"""Private raw relay, followed by a fail-closed export of just the probe session."""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from foil_bridge.readers import contained
from foil_bridge.transcript import data_roots

from .argv import parse
from .recordings import (
    GENERATED_ID,
    MAX_CAPTURE,
    TURNS,
    argv_shape,
    bounded,
    id_class,
    json_at,
    opencode_projection,
    require,
    session_rows,
    stdout,
)
from .streams import SESSION_KEYS

_DISCRIMINATORS = {
    "type", "event", "role", "kind", "state", "status", "step_type", "method", "sessionUpdate",
    "reason", "name", "tool", "title", "originator", "source", "mode",
}
_PROTOCOL = {
    "init", "step_update", "user_input", "assistant_output", "model_response", "tool_call",
    "usage_update", "user", "assistant", "system", "developer", "message", "text", "input_text",
    "output_text", "plain_text", "tool_use", "tool_result", "function_call", "function_call_output",
    "reasoning", "command_execution", "agent_message", "thread.started", "turn.started",
    "item.started", "item.updated", "item.completed", "turn.completed", "turn.failed",
    "session_meta",
    "response_item", "event_msg", "token_usage_record", "token_count", "task_started",
    "task_complete", "agent_message_chunk", "user_message_chunk", "session/update",
    "tool_call_update", "execute", "result", "success", "DONE", "RUNNING", "PENDING", "running",
    "completed", "in_progress", "pending", "failed", "error", "interrupted", "step_start",
    "step_finish", "start", "stop", "end_turn", "end", "resume", "auto", "default", "tool-calls",
    "finish", "tool", "bash", "Bash", "shell", "shell_command", "exec_command", "input", "output",
    "read", "write", "command", "codex_exec", "cli", "user_message", "assistant_message", "summary",
    "stop_sequence", "text-delta", "step-finish", "step-start",
}
_PRIVATE = {
    "text", "content", "input", "output", "arguments", "rawInput", "rawOutput", "prompt", "command",
    "aggregated_output", "cwd", "home", "path", "base_instructions", "instructions", "description",
    "message", "git", "directory", "slug", "project_id", "user_id", "model", "modelID",
    "providerID",
    "model_provider", "cli_version", "input_text", "thinking", "summary", "workdir", "agent",
    "permission", "share_url", "url", "snapshot", "patch", "filePath", "parentID", "session_id",
    "message_id", "sessionId", "sessionID", "thread_id", "conversation_id", "id", "callID",
    "call_id", "messageID", "uuid", "parentUuid", "requestId", "version", "projectID", "value",
}
_FIELDS = _DISCRIMINATORS | _PRIVATE | {
    "session_meta", "response_item", "init", "step_update", "item", "part", "params", "update",
    "payload", "usage", "tokens", "timestamp", "time_created", "time_updated", "time_deleted",
    "time_completed", "time", "time_archived", "ts", "step_index", "input_tokens", "output_tokens",
    "cached_input_tokens", "reasoning_output_tokens", "total_tokens", "cache_read_input_tokens",
    "cache_creation_input_tokens", "inputTokens", "outputTokens", "reasoning", "cache", "read",
    "write", "completion_tokens", "prompt_tokens", "size", "used", "exit_code", "code", "cost",
    "isError", "isMeta", "isSidechain", "stop_reason", "stop_sequence", "workingDirectory",
    "started_at", "duration", "duration_ms", "duration_api_ms", "num_turns", "total_cost_usd",
    "sessionInfo", "permissionMode", "reply", "metadata", "session", "modelUsage", "service_tier",
    "speed", "shell", "ref", "provider", "branch", "repo", "dirty", "commit_hash", "outputFormat",
    "request_id", "start", "end", "finished", "first", "last", "current", "start_time",
    "end_time", "created", "updated", "finish",
}


def _sanitize(value, at, *, field="", ids=None):
    if ids is None:
        ids = {}
    if isinstance(value, dict):
        require(set(value) <= _FIELDS, at, "unrecognized field; raw capture was not exported")
        return {key: _sanitize(item, at, field=key, ids=ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, at, field=field, ids=ids) for item in value]
    if isinstance(value, str):
        if field in _DISCRIMINATORS:
            require(value in _PROTOCOL, at, "unrecognized protocol value; raw capture not exported")
            return value
        if field in ("timestamp", "created", "updated"):
            require(re.fullmatch(r"[0-9T:.+Z-]+", value), at, "unrecognized timestamp format")
            return value
        if field in ("cwd", "home"):
            return f"<{field}>"
        if field in {"sessionId", "sessionID", "session_id", "thread_id", "conversation_id", "id"}:
            kind = id_class(value)
            if kind != "literal":
                index = ids.setdefault(value, len(ids))
                return f"00000000-0000-4000-8000-{index:012x}" if kind == "uuid" else f"ses_{index}"
        if field in {"id", "callID", "messageID"} and GENERATED_ID.fullmatch(value):
            return value.partition("_")[0] + "_0"
        if field == "arguments":
            return json.dumps(_sanitize(json_at(value, at), at, field=field, ids=ids))
        require(field in _PRIVATE, at, "unclassified text; raw capture was not exported")
        return "<text>"
    if field in _PRIVATE and type(value) in (int, float):
        return type(value)(0)
    return value


def relay(harness, binary, directory, args):
    """Copy stdout unchanged and store at most MAX_CAPTURE bytes per turn."""
    directory = Path(directory)
    try:
        _, _, first = parse(harness, args)
    except SystemExit:
        os.execv(binary, [binary, *args])
    slot = "start" if first else "resume"
    if (directory / f"{slot}.used").exists():
        os.execv(binary, [binary, *args])
    (directory / f"{slot}.used").touch(mode=0o600, exist_ok=False)
    with (directory / f"{slot}.stdout").open("wb") as raw, subprocess.Popen(
        [binary, *args], stdout=subprocess.PIPE,
    ) as process:
        assert process.stdout is not None
        left = MAX_CAPTURE + 1
        while data := process.stdout.read1(64 * 1024):
            if left:
                left -= raw.write(data[:left])
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        code = process.wait()
    if code < 0:
        if -code not in (signal.SIGKILL, signal.SIGSTOP):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    if code == 0:
        (directory / f"{slot}.argv.json").write_text(json.dumps(args), encoding="utf-8")
        (directory / f"{slot}.done").touch(mode=0o600)
    return code


def install(wrapper, binary, directory):
    source = str(Path(__file__).resolve().parents[2])
    entry = (
        f"import sys; sys.path.insert(0, {source!r}); "
        "from tests.harness.live_capture import relay; "
        "sys.exit(relay(*sys.argv[1:4], sys.argv[4:]))"
    )
    args = shlex.join([sys.executable, "-c", entry, wrapper.name, binary, str(directory)])
    wrapper.write_text(f'#!/bin/sh\nexec {args} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)


def _session_file(harness, root, cwd, session):
    if harness == "claude":
        encoded = "".join(char if char.isalnum() else "-" for char in cwd)
        return root / "projects" / encoded / f"{session}.jsonl", "projects/fixture/session.jsonl"
    if harness == "grok":
        path = root / "sessions" / quote(cwd, safe="") / session / "updates.jsonl"
        return path, "sessions/fixture/session/updates.jsonl"
    folders = (root / "sessions").glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]")
    seen = []
    for index, directory in enumerate(folders):
        require(index < 1000, root, "date directory scan exceeded its bound")
        if contained(root, directory):
            seen.extend(path for path in directory.glob(f"rollout-*{session}.jsonl")
                        if contained(root, path))
    require(len(seen) == 1, root, "could not identify one fixture rollout")
    return seen[0], "sessions/2000/01/01/rollout-session.jsonl"


def _write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_session(harness, destination, cwd, session, ids):
    destination.mkdir()
    if harness == "agy":
        _write(destination / "pane-only.json", {"reader": None, "source": "pane"})
        return
    root = data_roots()[harness]
    if harness == "opencode":
        path = root / "opencode.db"
        require(contained(root, path) and path.is_file(), path, "missing reader-backed database")
        projection = opencode_projection(path, session)
        for table in projection.values():
            require(all(name in _FIELDS and re.fullmatch(r"[A-Z0-9 (),]*", typ)
                        for name, typ in table["columns"]), path, "unrecognized SQLite columns")
            table["rows"] = [_sanitize(row, path, ids=ids) for row in table["rows"]]
        _write(destination / "opencode.db.json", projection)
        return
    path, relative = _session_file(harness, root, cwd, session)
    require(contained(root, path), path, "session file is outside its reader root")
    rows = session_rows(path)
    target = destination / relative
    target.parent.mkdir(parents=True)
    target.write_text("".join(json.dumps(_sanitize(row, path, ids=ids)) + "\n" for row in rows),
                      encoding="utf-8")


def export(harness, raw, destination, *, session, cwd, cli_version):
    require(re.fullmatch(r"[A-Za-z0-9_-]+", session), destination, "unsafe fixture session id")
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:rc|beta|alpha)[.0-9]*)?", cli_version),
            destination, "no safe CLI version found; capture was not exported")
    destination.parent.mkdir(parents=True, exist_ok=True)
    ids = {}
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        prepared = Path(staging) / "new"
        prepared.mkdir()
        for turn in TURNS:
            at = raw / f"{turn}.argv.json"
            args = json_at(bounded(at), at)
            _write(prepared / at.name, argv_shape(harness, [harness, *args]))
            at = raw / f"{turn}.stdout"
            events, plain = stdout(bounded(at), at)
            if harness in SESSION_KEYS:
                require(events, at, "missing NDJSON events")
                if turn == "start":
                    key = SESSION_KEYS[harness]
                    require(any(event.get(key) == session for event in events), at,
                            "no top-level id for the fixture session")
            else:
                require(not events and plain, at, "expected plain assistant text")
            clean = [json.dumps(_sanitize(event, at, ids=ids)) for event in events]
            if plain:
                clean.append("<assistant text>")
            (prepared / at.name).write_text("\n".join(clean) + "\n", encoding="utf-8")
        _save_session(harness, prepared / "session", cwd, session, ids)
        _write(prepared / "manifest.json", {"schema_version": 1, "harness": harness,
                                           "cli_version": cli_version,
                                           "captured_at": datetime.now(UTC).isoformat()})
        previous = Path(staging) / "previous"
        if destination.exists():
            require(destination.is_dir() and not destination.is_symlink(), destination,
                    "recording destination is not a directory")
            destination.rename(previous)
        try:
            prepared.rename(destination)
        except OSError:
            if previous.exists():
                shutil.move(previous, destination)
            raise
