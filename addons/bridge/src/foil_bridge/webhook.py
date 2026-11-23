"""A webhook beside Web Push. The body is a wake, not the fleet.

The POST is ``{"kind": "needs_you", "count": N}`` and nothing else. The
client that receives it pulls the detail through MCP.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from foil.bridge_cmd import bridge_root, write_private
from foil.errors import FoilError
from foil_bridge import push

MAX_URLS = 8
_LOCK = threading.RLock()


def _dir() -> Path:
    return bridge_root() / "webhook"


def _read_json(path: Path, limit: int = 8192) -> Any:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        if path.stat().st_size > limit:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _write(path: Path, payload: dict[str, Any]) -> None:
    write_private(path, json.dumps(payload, separators=(",", ":")) + "\n")


def _stored_urls(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for item in raw:
        if len(found) >= MAX_URLS:
            break
        if push._endpoint_ok(item) and item not in found:
            found.append(item)
    return found


def _posted_urls(raw: object) -> list[str]:
    if not isinstance(raw, list) or len(raw) > MAX_URLS:
        raise FoilError("foil: webhook setting is invalid")
    found: list[str] = []
    for item in raw:
        if not push._endpoint_ok(item) or item in found:
            raise FoilError("foil: webhook setting is invalid")
        found.append(item)
    return found


def settings() -> dict[str, Any]:
    stored = _read_json(_dir() / "settings")
    enabled = False
    urls: list[str] = []
    if isinstance(stored, dict):
        if type(stored.get("enabled")) is bool:
            enabled = stored["enabled"]
        urls = _stored_urls(stored.get("urls"))
    return {"enabled": enabled, "urls": urls}


def _view() -> dict[str, Any]:
    current = settings()
    return {"enabled": current["enabled"], "urls": list(current["urls"])}


def view() -> dict[str, Any]:
    with _LOCK:
        return _view()


def update(body: dict[str, Any]) -> dict[str, Any]:
    if not body or set(body) - {"enabled", "urls"}:
        raise FoilError("foil: webhook setting is invalid")
    if "enabled" in body and type(body["enabled"]) is not bool:
        raise FoilError("foil: webhook setting is invalid")
    urls = _posted_urls(body["urls"]) if "urls" in body else None
    with _LOCK:
        current = settings()
        enabling = body.get("enabled") is True and not current["enabled"]
        if "enabled" in body:
            current["enabled"] = body["enabled"]
        if urls is not None:
            current["urls"] = urls
        _write(_dir() / "settings", {"enabled": current["enabled"], "urls": current["urls"]})
        if enabling:
            _write(_dir() / "heartbeat", {"t": int(time.time())})
            state = _state()
            state["gap"] = False
            _write_state(state)
        return _view()


def _stamp(path: Path) -> int | None:
    stored = _read_json(path)
    if isinstance(stored, dict) and type(stored.get("t")) is int:
        return stored["t"]
    return None


def _state() -> dict[str, Any]:
    stored = _read_json(_dir() / "state", limit=4 * 1024 * 1024)
    sent: dict[str, list[str]] = {}
    last = -1
    gap = False
    if isinstance(stored, dict):
        raw = stored.get("sent")
        if isinstance(raw, dict):
            for url, cards in raw.items():
                if not isinstance(url, str) or not isinstance(cards, list):
                    continue
                kept = [item for item in cards if isinstance(item, str) and push._card_ok(item)]
                if kept:
                    sent[url] = kept[-500:]
        if type(stored.get("last")) is int:
            last = stored["last"]
        gap = stored.get("gap") is True
    return {"sent": sent, "last": last, "gap": gap}


def _write_state(state: dict[str, Any]) -> None:
    _write(
        _dir() / "state",
        {
            "sent": {url: cards[-500:] for url, cards in state["sent"].items() if cards},
            "last": state["last"],
            "gap": bool(state["gap"]),
        },
    )


def _note_gap(now: float) -> bool:
    path = _dir() / "heartbeat"
    previous = _stamp(path)
    state = _state()
    if previous is not None and now - previous > push.GAP:
        for url in list(state["sent"]):
            kept = [card for card in state["sent"][url] if card != "down/bridge"]
            if kept:
                state["sent"][url] = kept
            else:
                del state["sent"][url]
        state["gap"] = True
        _write_state(state)
    if previous is None or now - previous >= push.HEARTBEAT:
        _write(path, {"t": int(now)})
    return bool(state.get("gap"))


def _settled(urls: list[str], sent: dict[str, list[str]]) -> bool:
    return bool(urls) and all("down/bridge" in sent.get(url, []) for url in urls)


def _payload(count: int) -> bytes:
    return json.dumps({"kind": "needs_you", "count": count}, separators=(",", ":")).encode()


def deliver(now: float | None = None) -> dict[str, Any] | None:
    """POST at most one round. The same events are not posted again while they remain."""

    with _LOCK:
        return _deliver(time.time() if now is None else now)


def _deliver(now: float) -> dict[str, Any] | None:
    cfg = settings()
    if not cfg["enabled"]:
        return None
    gap = _note_gap(now)
    urls: list[str] = cfg["urls"]
    found = push.current_reasons(dict.fromkeys(push.KINDS, True), gap=gap)
    cards = list(dict.fromkeys(item.card for item in found))
    current = set(cards)
    state = _state()
    sent: dict[str, list[str]] = state["sent"]
    allowed = set(urls)
    for url in list(sent):
        if url not in allowed:
            del sent[url]
            continue
        kept = [card for card in sent[url] if card in current]
        if kept:
            sent[url] = kept
        else:
            del sent[url]
    if not urls:
        return None
    if state["last"] >= 0 and now - state["last"] < push.INTERVAL:
        _write_state(state)
        return None
    owing = [url for url in urls if any(card not in sent.get(url, []) for card in cards)]
    if not owing:
        if not state["gap"] or _settled(urls, sent):
            state["gap"] = False
        _write_state(state)
        return None
    body = _payload(len(cards))
    shown: dict[str, Any] | None = None
    for url in owing:
        status = push._post(
            url,
            body,
            {"Content-Type": "application/json", "User-Agent": "foil-bridge"},
        )
        if 200 <= status < 300:
            shown = json.loads(body)
            sent[url] = list(cards)
    state["last"] = int(now)
    if state["gap"] and _settled(urls, sent):
        state["gap"] = False
    _write_state(state)
    return shown
