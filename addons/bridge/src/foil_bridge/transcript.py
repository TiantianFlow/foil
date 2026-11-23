"""``foil-bridge transcript``: one seat's conversation, for display.

Core does not import this module. The readers parse harness files. Nothing
parsed here decides a wake, a state, or progress.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from foil.errors import (
    _SECRET_KEY_PARTS,
    _SECRET_VALUE_PREFIXES,
    FoilError,
    shown,
)
from foil.journal import LIMIT as JOURNAL_LIMIT
from foil.lifecycle import peek_text
from foil.master import directory, load_record
from foil.presets import load_preset
from foil.project import checkout_for_key, discover_project, foil_root
from foil.runner import MAX_PLAN_BYTES
from foil.runner import load_record as load_turns
from foil.store import FLEET_ID, SAFE_ID, fleet_dir, load_registry
from foil.tmux import TmuxController, TmuxError, TmuxTarget
from foil_bridge.readers import contained, read_session

PANE_LINES = 200
SUMMARY = 20
SUMMARY_CUT = 200
LEVELS = ("summary", "messages", "tools", "all")
NOTE = "Secrets are masked as a best effort."
_ASSIGN = re.compile(r"([A-Za-z0-9_.-]{1,80})(\s*[:=]\s*)(\S+)")
_JSON_VALUE = re.compile(r'"([A-Za-z0-9_.-]{1,80})"(\s*:\s*)("(?:[^"\\]|\\.)*"|[^,}\s]+)')
_USERINFO = re.compile(r"(://)[^/\s@]+@")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_TOKEN = re.compile(r"\S+")
_PREFIXES = tuple(prefix for prefix in _SECRET_VALUE_PREFIXES if not prefix.endswith(" "))


class UsageError(FoilError):
    """A bad invocation. The program exits 2."""


def user_home() -> Path:
    return Path.home()


def data_roots(home: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Path]:
    """Where each format id keeps its own session files."""

    base = user_home() if home is None else home
    names = os.environ if env is None else env
    xdg = names.get("XDG_DATA_HOME", "").strip()
    share = Path(xdg).expanduser() if xdg else base / ".local" / "share"
    claude = names.get("CLAUDE_CONFIG_DIR", "").strip()
    codex = names.get("CODEX_HOME", "").strip()
    gemini = names.get("GEMINI_DIR", "").strip()
    grok = names.get("GROK_HOME", "").strip()
    return {
        "claude": Path(claude).expanduser() if claude else base / ".claude",
        "codex": Path(codex).expanduser() if codex else base / ".codex",
        "gemini": Path(gemini).expanduser() if gemini else base / ".gemini",
        "grok": Path(grok).expanduser() if grok else base / ".grok",
        "opencode": share / "opencode",
        "fake": base / ".foil-fake",
    }


def _secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _hide_assignments(text: str) -> str:
    """A secret assignment hides the rest of its line. Any other assignment is one token."""

    pieces: list[str] = []
    last = 0
    for match in _ASSIGN.finditer(text):
        if match.start() < last:
            continue
        if _secret_key(match.group(1)):
            end = text.find("\n", match.start())
            end = len(text) if end < 0 else end
            pieces.append(text[last : match.start()])
            pieces.append(f"{match.group(1)}{match.group(2)}[hidden]")
            last = end
        else:
            pieces.append(text[last : match.end()])
            last = match.end()
    pieces.append(text[last:])
    return "".join(pieces)


def _prefixed(word: str) -> bool:
    """True when a token, or the value side of one, starts with a secret prefix."""

    pieces = re.split(r"[:=,;()\[\]{}<>]", word.strip("\"'`+-"))
    for piece in pieces:
        bare = piece.strip("\"'`+-")
        if bare and any(bare.lower().startswith(prefix) for prefix in _PREFIXES):
            return True
    return False


def mask(text: str) -> str:
    """Hide credential-shaped text. Best effort, same shapes the board refuses."""

    def json_value(match: re.Match[str]) -> str:
        key = match.group(1)
        if _secret_key(key):
            raw = match.group(3)
            hidden = '"[hidden]"' if raw.startswith('"') else "[hidden]"
            return f'"{key}"{match.group(2)}{hidden}'
        return match.group(0)

    def token(match: re.Match[str]) -> str:
        word = match.group(0)
        if _prefixed(word):
            return "[hidden]"
        return word

    text = _USERINFO.sub(r"\1[hidden]@", text)
    text = _BEARER.sub("[hidden]", text)
    text = _hide_assignments(text)
    text = _JSON_VALUE.sub(json_value, text)
    return _TOKEN.sub(token, text)


def command(argv: list[str]) -> int:
    """Run ``transcript``. Usage mistakes exit 2. A missing transcript exits 1."""

    try:
        seat, project, fleet, level, as_json = _parse(argv)
        payload = read(seat, project=project, fleet=fleet, level=level)
    except UsageError as exc:
        print(str(exc), file=_err())
        return 2
    except FoilError as exc:
        print(_bridge(exc), file=_err())
        return 1
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    print(_human(payload), end="")
    return 0


def read(
    seat: str,
    *,
    project: str | None = None,
    fleet: str | None = None,
    level: str = "summary",
    home: Path | None = None,
    env: dict[str, str] | None = None,
    pane: bool = True,
) -> dict[str, Any]:
    """The masked transcript at ``level``. Raises ``FoilError`` when nothing can be read."""

    if level not in LEVELS:
        raise UsageError("foil-bridge: --level is summary, messages, tools, or all")
    if not SAFE_ID.fullmatch(seat):
        raise FoilError(f"foil: unknown seat '{shown(seat)}'")
    if fleet is not None and not FLEET_ID.fullmatch(fleet):
        raise FoilError(f"foil: invalid fleet '{shown(fleet)}'")
    roots = data_roots(home, env)
    context = _master() if seat == "master" else _seat(seat, project, fleet)
    events, truncated, formats, source = _events(context, roots, pane=pane)
    if source == "pane":
        shown_events = [_mask_event(events[0])]
        omitted = 0
        totals = {"input": 0, "output": 0}
    else:
        masked = [_mask_event(event) for event in events]
        totals = _tokens(masked)
        shown_events, omitted = _select(masked, level)
    used = formats[-1] if formats else context["format"] or "unknown"
    return {
        "seat": seat,
        "project": context["project"],
        "fleet": context["fleet"],
        "format": used,
        "source": source,
        "level": level,
        "masked": True,
        "note": NOTE,
        "truncated": truncated,
        "omitted": omitted,
        "tokens": totals,
        "events": shown_events,
    }


def _parse(argv: list[str]) -> tuple[str, str | None, str | None, str, bool]:
    seat: str | None = None
    project: str | None = None
    fleet: str | None = None
    level = "summary"
    as_json = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--json":
            as_json = True
        elif arg in {"--project", "--fleet", "--level"}:
            index += 1
            if index >= len(argv) or argv[index] in {"--json", "--project", "--fleet", "--level"}:
                raise UsageError("foil-bridge: unexpected argument")
            if arg == "--project":
                project = argv[index]
            elif arg == "--fleet":
                fleet = argv[index]
            else:
                level = argv[index]
        elif arg.startswith("-"):
            raise UsageError("foil-bridge: unexpected argument")
        elif seat is None:
            seat = arg
        else:
            raise UsageError("foil-bridge: unexpected argument")
        index += 1
    if seat is None:
        raise UsageError(
            "foil-bridge: usage: transcript SEAT [--project KEY] [--fleet ID] "
            "[--level LEVEL] [--json]"
        )
    if level not in LEVELS:
        raise UsageError("foil-bridge: --level is summary, messages, tools, or all")
    return seat, project, fleet, level, as_json


def _master() -> dict[str, Any]:
    root = directory()
    plan = _json(root / "run" / "plan.json", root)
    record = load_record(root)
    harness = plan.get("harness") if isinstance(plan.get("harness"), str) else ""
    model = plan.get("model") if isinstance(plan.get("model"), str) else ""
    cwd = plan.get("cwd") if isinstance(plan.get("cwd"), str) else str(root)
    sessions = _sessions(record.get("sessions"), record.get("session_id"), harness, model)
    return {
        "name": "master",
        "project": "",
        "fleet": "",
        "format": _format(Path.cwd(), harness, master=True),
        "cwd": cwd,
        "sessions": sessions,
        "checkout": None,
        "fleet_path": None,
        "master": root,
        "window": root / "run" / "window.json",
    }


def _seat(name: str, project: str | None, fleet: str | None) -> dict[str, Any]:
    checkout = checkout_for_key(project) if project else discover_project()
    registry = load_registry(checkout, fleet)
    record = registry["seats"].get(name)
    if record is None:
        raise FoilError(f"foil: unknown seat '{shown(name)}'")
    fleet_path = fleet_dir(checkout, fleet)
    plan = _json(fleet_path / "run" / "plans" / f"{name}.json", fleet_path)
    raw_cwd = plan.get("cwd")
    cwd = raw_cwd if isinstance(raw_cwd, str) and Path(raw_cwd).is_absolute() else ""
    if not cwd:
        worktree = record.get("worktree") or ""
        cwd = worktree if Path(worktree).is_absolute() else str(checkout.resolve())
    turns = load_turns(fleet_path / "board", name)
    harness = record.get("harness") or ""
    sessions = _sessions(
        turns.get("sessions"), record.get("session_id"), harness, record.get("model") or ""
    )
    key = foil_root(checkout).name
    chosen = fleet or _fleet_name(fleet_path)
    return {
        "name": name,
        "project": key,
        "fleet": chosen,
        "format": _format(checkout, harness, master=False),
        "cwd": cwd,
        "sessions": sessions,
        "checkout": checkout,
        "fleet_path": fleet_path,
        "master": None,
        "window": None,
    }


def _fleet_name(path: Path) -> str:
    return path.name if FLEET_ID.fullmatch(path.name) else "main"


def _sessions(raw: Any, current: Any, harness: str, model: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            session_id = item.get("id")
            if (
                not isinstance(session_id, str)
                or not SAFE_ID.fullmatch(session_id)
                or session_id in seen
            ):
                continue
            seen.add(session_id)
            item_harness = item.get("harness") if isinstance(item.get("harness"), str) else harness
            item_model = item.get("model") if isinstance(item.get("model"), str) else model
            started = item.get("started") if isinstance(item.get("started"), str) else ""
            rows.append(
                {"id": session_id, "harness": item_harness, "model": item_model, "started": started}
            )
    if isinstance(current, str) and SAFE_ID.fullmatch(current) and current not in seen:
        rows.append({"id": current, "harness": harness, "model": model, "started": ""})
    return rows


def _format(checkout: Path, harness: str, *, master: bool) -> str:
    if not harness or not SAFE_ID.fullmatch(harness):
        return ""
    try:
        preset = load_preset(checkout, harness, global_only=master)
    except (FoilError, OSError, UnicodeDecodeError):
        return ""
    transcript = preset.get("transcript") or ""
    return transcript if isinstance(transcript, str) else ""


def _events(
    context: dict[str, Any], roots: dict[str, Path], *, pane: bool = True
) -> tuple[list[dict[str, Any]], bool, list[str], str]:
    times = _turn_times(context.get("fleet_path"), context.get("name"))
    events: list[dict[str, Any]] = []
    formats: list[str] = []
    truncated = False
    checkout = context["checkout"] if context["checkout"] is not None else Path.cwd()
    master = context["master"] is not None
    for session in context["sessions"]:
        fmt = _format(checkout, session["harness"], master=master) or context["format"]
        root = roots.get(fmt)
        if root is None:
            continue
        found = read_session(
            fmt,
            session["id"],
            context["cwd"],
            root,
            started=session["started"],
            turn_times=times,
        )
        if found is None:
            continue
        parsed, cut = found
        truncated = truncated or cut
        formats.append(fmt)
        events.extend(parsed)
    if events:
        return events, truncated, formats, "file"
    if not pane:
        raise FoilError(f"foil: no transcript for '{shown(context.get('name') or 'master')}'")
    text = _pane(context)
    if text is None:
        raise FoilError(f"foil: no transcript for '{shown(context.get('name') or 'master')}'")
    return [_pane_event(text)], False, formats, "pane"


def _turn_times(fleet: Path | None, seat: str | None) -> list[str]:
    if fleet is None or not seat:
        return []
    times: list[str] = []
    for name in ("journal.log.1", "journal.log"):
        path = fleet / "run" / name
        if path.is_symlink() or not path.is_file():
            continue
        if not contained(fleet, path):
            continue
        try:
            if path.stat().st_size > JOURNAL_LIMIT:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("seat") == seat and event.get("event") in {"turn_start", "turn_end"}:
                when = event.get("time")
                if isinstance(when, str):
                    times.append(when)
    return times


def _pane(context: dict[str, Any]) -> str | None:
    if context["master"] is not None:
        return _master_pane(context["master"], context["window"])
    checkout = context["checkout"]
    try:
        return peek_text(
            checkout, context["name"], lines=PANE_LINES, fleet=context["fleet"] or None
        )
    except (FoilError, OSError, UnicodeDecodeError):
        return None


def _master_pane(root: Path, path: Path) -> str | None:
    record = _json(path, root)
    window = str(record.get("window_id") or "")
    if not window.startswith("@"):
        return None
    target = TmuxTarget(
        session_name=str(record.get("session_name") or ""),
        window_name="master",
        session_id=str(record.get("session_id") or "") or None,
        window_id=window,
    )
    try:
        probe = TmuxController().probe("master", "master", target)
        observed = probe.observed.window_id if probe.identity_matches and probe.observed else ""
        if not observed:
            return None
        return TmuxController().capture_pane(observed, PANE_LINES)
    except (TmuxError, OSError, UnicodeDecodeError):
        return None


def _pane_event(text: str) -> dict[str, Any]:
    return {"role": "pane", "kind": "pane", "text": text, "tool": "", "usage": None}


def _json(path: Path, root: Path) -> dict[str, Any]:
    if not contained(root, path) or path.is_symlink() or not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_PLAN_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _mask_event(event: dict[str, Any]) -> dict[str, Any]:
    usage = event.get("usage")
    return {
        "role": event.get("role") or "",
        "kind": event.get("kind") or "",
        "text": mask(event.get("text") or ""),
        "tool": mask(event.get("tool") or ""),
        "usage": usage if isinstance(usage, dict) else None,
    }


def _select(events: list[dict[str, Any]], level: str) -> tuple[list[dict[str, Any]], int]:
    messages = [
        event
        for event in events
        if event["kind"] == "message" and event["role"] in {"user", "assistant"}
    ]
    if level == "summary":
        omitted = max(0, len(messages) - SUMMARY)
        chosen = []
        for event in messages[-SUMMARY:]:
            text = event["text"]
            if len(text) > SUMMARY_CUT:
                text = text[:SUMMARY_CUT] + "…"
            chosen.append({**event, "text": text})
        return chosen, omitted
    kept: list[dict[str, Any]] = []
    for event in events:
        kind = event["kind"]
        if kind == "message" and event["role"] in {"user", "assistant"}:
            kept.append(event)
        elif level == "messages":
            continue
        elif kind == "tool" and level in {"tools", "all"}:
            kept.append({**event, "text": ""} if level == "tools" else event)
        elif level == "all" and kind in {"usage", "thought", "message"}:
            kept.append(event)
    return kept, 0


def _human(payload: dict[str, Any]) -> str:
    head = f"{payload['seat']}\t{payload['format']}\t{payload['source']}"
    if payload["truncated"]:
        head += "\ttruncated"
    lines = [head]
    if payload["source"] == "pane":
        lines.append(payload["events"][0]["text"])
    else:
        for event in payload["events"]:
            lines.append(_line(event))
        if payload["omitted"]:
            lines.append(f"omitted {payload['omitted']}")
    lines.append(NOTE)
    return "\n".join(lines) + "\n"


def event_line(event: dict[str, Any]) -> str:
    """One display line. The same line the command prints for this event."""

    return _line(event)


def _tokens(events: list[dict[str, Any]]) -> dict[str, int]:
    """Sum of usage events. The level filter has not dropped them yet."""

    incoming = outgoing = 0
    for event in events:
        usage = event.get("usage")
        if event.get("kind") != "usage" or not isinstance(usage, dict):
            continue
        left, right = usage.get("input"), usage.get("output")
        if type(left) is int:
            incoming += left
        if type(right) is int:
            outgoing += right
    return {"input": incoming, "output": outgoing}


def _line(event: dict[str, Any]) -> str:
    text = " ".join((event.get("text") or "").split())
    kind = event["kind"]
    if kind == "tool":
        name = event.get("tool") or "tool"
        return f"tool: {name}: {text}" if text else f"tool: {name}"
    if kind == "usage":
        usage = event.get("usage") or {}
        return f"usage: in {usage.get('input', 0)} out {usage.get('output', 0)}"
    if kind == "thought":
        return f"thought: {text}"
    return f"{event['role']}: {text}"


def _bridge(exc: FoilError) -> str:
    text = str(exc)
    if text.startswith("foil-bridge:"):
        return text
    if text.startswith("foil:"):
        return "foil-bridge:" + text[len("foil:") :]
    return f"foil-bridge: {text}"


def _err():
    import sys

    return sys.stderr
