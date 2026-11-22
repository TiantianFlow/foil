"""Desk token, one-time codes, and device tokens. The files are the credentials."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from pathlib import Path
from typing import NamedTuple

from foil.bridge_cmd import bridge_root, read_desk_token, write_private

ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_RE = re.compile(rf"[{ALPHABET}]{{8}}")
DEVICE_RE = re.compile(r"[0-9a-f]{16}")
TTL = 300


class Identity(NamedTuple):
    kind: str
    id: str
    token: str


def _same(left: str, right: str) -> bool:
    try:
        return secrets.compare_digest(left, right)
    except (TypeError, ValueError):
        return False


def _codes() -> Path:
    return bridge_root() / "codes"


def _devices() -> Path:
    return bridge_root() / "devices"


def code_pending(code: str) -> bool:
    """True when the code file is still inside its 300 seconds and has not been spent."""

    if not CODE_RE.fullmatch(code or ""):
        return False
    path = _codes() / code
    if path.is_symlink() or not path.is_file():
        return False
    try:
        expiry = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return expiry >= time.time()


def consume(code: str) -> bool:
    """Spend a code. A second caller finds the file gone."""

    if not CODE_RE.fullmatch(code or ""):
        return False
    path = _codes() / code
    if path.is_symlink():
        return False
    try:
        expiry = int(path.read_text(encoding="utf-8").strip())
        path.unlink()
    except (OSError, ValueError):
        return False
    return expiry >= time.time()


def mint(node: tuple[str, str] | None) -> dict[str, object]:
    """A new code. ``url`` is the phone address when this machine's tailnet name is known."""

    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    expiry = int(time.time()) + TTL
    write_private(_codes() / code, f"{expiry}\n")
    url = f"https://{node[0]}/pair?code={code}" if node is not None else ""
    return {"code": code, "expires": expiry, "url": url}


def new_device() -> dict[str, str]:
    """A device token. The file keeps the hash and the time, not the secret."""

    identifier = secrets.token_hex(8)
    token = f"{identifier}.{secrets.token_urlsafe(32)}"
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    digest = hashlib.sha256(token.encode()).hexdigest()
    write_private(_devices() / identifier, f"{digest}\n{created}\n")
    return {"token": token, "id": identifier}


def identify(token: str) -> Identity | None:
    """The desk token, or a device token whose hash is on disk."""

    if not token or len(token) > 200 or any(ch in token for ch in "\r\n\x00"):
        return None
    desk = read_desk_token()
    if desk is not None and _same(token, desk):
        return Identity("desk", "", token)
    identifier, dot, _secret = token.partition(".")
    if not dot or not DEVICE_RE.fullmatch(identifier):
        return None
    path = _devices() / identifier
    if path.is_symlink() or not path.is_file():
        return None
    try:
        stored = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not stored:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not _same(digest, stored[0]):
        return None
    return Identity("device", identifier, token)


def device_rows() -> list[dict[str, str]]:
    folder = _devices()
    if folder.is_symlink() or not folder.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or not path.is_file() or not DEVICE_RE.fullmatch(path.name):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rows.append({"id": path.name, "created": lines[1] if len(lines) > 1 else ""})
    return rows


def revoke(actor: Identity, identifier: str) -> str:
    """``ok``, ``forbidden``, ``missing``, or ``bad``."""

    if not DEVICE_RE.fullmatch(identifier or ""):
        return "bad"
    if actor.kind == "device" and actor.id != identifier:
        return "forbidden"
    if actor.kind not in {"desk", "device"}:
        return "forbidden"
    path = _devices() / identifier
    if path.is_symlink() or not path.is_file():
        return "missing"
    try:
        path.unlink()
    except OSError:
        return "missing"
    return "ok"
