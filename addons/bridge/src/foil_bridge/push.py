"""Web Push for the installed page. A payload is a label and a card id.

Message encryption is RFC 8291 (aes128gcm). The signing is RFC 8292 (VAPID).
The stdlib has neither, so this package uses ``cryptography``. Core does not.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from foil import master
from foil.answer import question_cards
from foil.bridge_cmd import bridge_root, write_private
from foil.errors import FoilError
from foil.inbox import collect_projects

KINDS = ("question", "seat", "delivery", "done", "down")
INTERVAL = 60
GAP = 120
HEARTBEAT = 30
TTL = "300"
RECORD = 4096
_SUB = "https://github.com/TiantianFlow/foil"
_WORD = {
    "question": ("decision", "decisions"),
    "seat": ("seat", "seats"),
    "delivery": ("delivery", "deliveries"),
    "done": ("goal", "goals"),
}
_LOCK = threading.RLock()
_DEVICE = re.compile(r"^(?:desk|[0-9a-f]{16})$")


@dataclass(frozen=True)
class Reason:
    kind: str
    project: str
    card: str


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _push_dir() -> Path:
    return bridge_root() / "push"


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


def owner_id(kind: str, identifier: str) -> str:
    return "desk" if kind == "desk" else identifier


def _private() -> ec.EllipticCurvePrivateKey:
    path = _push_dir() / "vapid"
    if path.is_symlink():
        raise FoilError("foil: push subscription is invalid")
    if path.exists():
        stored = _read_json(path)
        if isinstance(stored, dict) and isinstance(stored.get("d"), str):
            try:
                secret = _b64decode(stored["d"])
                if len(secret) == 32:
                    number = int.from_bytes(secret, "big")
                    if number:
                        return ec.derive_private_key(number, ec.SECP256R1())
            except (ValueError, TypeError):
                pass
        raise FoilError("foil: push subscription is invalid")
    key = ec.generate_private_key(ec.SECP256R1())
    secret = key.private_numbers().private_value.to_bytes(32, "big")
    _write(path, {"d": _b64(secret)})
    return key


def public_key() -> str:
    raw = _private().public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return _b64(raw)


def settings() -> dict[str, bool]:
    found = dict.fromkeys(KINDS, False)
    stored = _read_json(_push_dir() / "settings")
    if not isinstance(stored, dict):
        return found
    for kind in KINDS:
        if type(stored.get(kind)) is bool:
            found[kind] = stored[kind]
    return found


def update_settings(body: dict[str, Any]) -> dict[str, bool]:
    if not body or any(key not in KINDS or type(value) is not bool for key, value in body.items()):
        raise FoilError("foil: push setting is invalid")
    with _LOCK:
        current = settings()
        enabling_down = body.get("down") is True and not current["down"]
        current.update(body)
        _write(_push_dir() / "settings", current)
        if enabling_down:
            _write(_push_dir() / "heartbeat", {"t": int(time.time())})
            state = _state()
            state["gap"] = False
            _write_state(state)
        return current


def _subscription_path(owner: str) -> Path:
    return _push_dir() / "subscriptions" / owner


def _endpoint_ok(url: object) -> bool:
    if not isinstance(url, str) or not url or len(url) > 2000:
        return False
    if any(char in url for char in "\r\n\x00 "):
        return False
    parts = urlsplit(url)
    if parts.username or parts.password or not parts.hostname:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}


def _client_key(text: object, size: int) -> str | None:
    if not isinstance(text, str) or not text or len(text) > 200:
        return None
    try:
        raw = _b64decode(text)
    except (ValueError, TypeError):
        return None
    if len(raw) != size:
        return None
    if size == 65:
        if raw[0] != 4:
            return None
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError:
            return None
    return _b64(raw)


def save_subscription(owner: str, body: dict[str, Any]) -> dict[str, Any]:
    if not _DEVICE.fullmatch(owner) or set(body) != {"endpoint", "p256dh", "auth"}:
        raise FoilError("foil: push subscription is invalid")
    p256dh = _client_key(body.get("p256dh"), 65)
    auth = _client_key(body.get("auth"), 16)
    endpoint = body.get("endpoint")
    if not _endpoint_ok(endpoint) or p256dh is None or auth is None:
        raise FoilError("foil: push subscription is invalid")
    with _LOCK:
        if not _owner_ok(owner):
            raise FoilError("foil: push subscription is invalid")
        path = _subscription_path(owner)
        stored = _read_json(path)
        same = (
            isinstance(stored, dict)
            and stored.get("endpoint") == endpoint
            and stored.get("p256dh") == p256dh
            and stored.get("auth") == auth
        )
        _write(path, {"endpoint": endpoint, "p256dh": p256dh, "auth": auth})
        if not same:
            state = _state()
            state["sent"].pop(owner, None)
            _write_state(state)
        _private()
        return _view(owner)


def _owner_ok(owner: str) -> bool:
    if owner == "desk":
        return True
    path = bridge_root() / "devices" / owner
    return not path.is_symlink() and path.is_file()


def drop(owner: str) -> None:
    if not _DEVICE.fullmatch(owner):
        return
    with _LOCK:
        path = _subscription_path(owner)
        if path.is_symlink() or not path.is_file():
            return
        try:
            path.unlink()
        except OSError:
            return


def _subscriptions() -> list[tuple[str, dict[str, str]]]:
    folder = _push_dir() / "subscriptions"
    if folder.is_symlink() or not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.iterdir()):
        if path.is_symlink() or not path.is_file() or not _DEVICE.fullmatch(path.name):
            continue
        stored = _read_json(path)
        if (
            isinstance(stored, dict)
            and _endpoint_ok(stored.get("endpoint"))
            and _client_key(stored.get("p256dh"), 65)
            and _client_key(stored.get("auth"), 16)
        ):
            found.append((path.name, stored))
    return found


def _view(owner: str) -> dict[str, Any]:
    return {
        "publicKey": public_key(),
        "subscribed": _subscription_path(owner).is_file(),
        "kinds": settings(),
    }


def view(owner: str) -> dict[str, Any]:
    with _LOCK:
        return _view(owner)


def _card_ok(card: str) -> bool:
    parts = card.split("/")
    if (
        len(card) > 400
        or ".." in parts
        or any(not part or any(ord(char) < 32 for char in part) for part in parts)
    ):
        return False
    if card in {"down/master", "down/bridge"}:
        return True
    if len(parts) < 3:
        return False
    kind = parts[0]
    if kind == "g":
        return len(parts) == 3
    if kind in {"q", "s"}:
        return len(parts) == 4
    return kind == "d" and len(parts) >= 4


def short_name(project: str) -> str:
    head, sep, tail = project.rpartition("-")
    if sep and len(tail) == 6 and all(char in "0123456789abcdef" for char in tail):
        return head
    return project


def _label(group: list[Reason]) -> str:
    kind = group[0].kind
    if kind == "down":
        return "master down" if group[0].card == "down/master" else "bridge down"
    word = _WORD[kind][0 if len(group) == 1 else 1]
    return f"{short_name(group[0].project)}: {len(group)} {word}"


def _master_down() -> bool:
    root = master.directory()
    window = master._load_window(root)
    if window and not master._alive(window):
        return True
    return bool(master.load_record(root)["paused"])


def _stamp(path: Path) -> int | None:
    stored = _read_json(path)
    if isinstance(stored, dict) and type(stored.get("t")) is int:
        return stored["t"]
    return None


def _note_gap(now: float) -> bool:
    path = _push_dir() / "heartbeat"
    previous = _stamp(path)
    state = _state()
    if previous is not None and now - previous > GAP:
        for owner in list(state["sent"]):
            kept = [card for card in state["sent"][owner] if card != "down/bridge"]
            if kept:
                state["sent"][owner] = kept
            else:
                del state["sent"][owner]
        state["gap"] = True
        _write_state(state)
    if previous is None or now - previous >= HEARTBEAT:
        _write(path, {"t": int(now)})
    return bool(state.get("gap"))


def _reasons(enabled: dict[str, bool], gap: bool) -> list[Reason]:
    found: list[Reason] = []
    if enabled["question"]:
        for card in question_cards():
            if card.get("state") != "open":
                continue
            project, fleet, ident = card["project"], card["fleet"], card["id"]
            found.append(Reason("question", project, f"q/{project}/{fleet}/{ident}"))
    scan = enabled["seat"] or enabled["delivery"] or enabled["done"]
    if scan:
        for item in collect_projects():
            project, fleet = item["project"], item["fleet"]
            kind, subject = item["kind"], item["subject"]
            if kind == "seat" and enabled["seat"] and subject.startswith("seat:"):
                name = subject.removeprefix("seat:")
                found.append(Reason("seat", project, f"s/{project}/{fleet}/{name}"))
            elif kind == "undelivered" and enabled["delivery"] and subject.startswith("mail:"):
                path = subject.removeprefix("mail:")
                found.append(Reason("delivery", project, f"d/{project}/{fleet}/{path}"))
            elif kind == "done" and enabled["done"]:
                found.append(Reason("done", project, f"g/{project}/{fleet}"))
    if enabled["down"] and _master_down():
        found.append(Reason("down", "", "down/master"))
    if enabled["down"] and gap:
        found.append(Reason("down", "", "down/bridge"))
    return [item for item in found if _card_ok(item.card)]


def _state() -> dict[str, Any]:
    stored = _read_json(_push_dir() / "state", limit=4 * 1024 * 1024)
    sent: dict[str, list[str]] = {}
    last = -1
    gap = False
    until = 0
    if isinstance(stored, dict):
        raw = stored.get("sent")
        if isinstance(raw, dict):
            for owner, cards in raw.items():
                if not isinstance(owner, str) or not _DEVICE.fullmatch(owner):
                    continue
                if not isinstance(cards, list):
                    continue
                kept = [item for item in cards if isinstance(item, str) and _card_ok(item)]
                if kept:
                    sent[owner] = kept[-500:]
        if type(stored.get("last")) is int:
            last = stored["last"]
        gap = stored.get("gap") is True
        if type(stored.get("until")) is int:
            until = stored["until"]
    return {"sent": sent, "last": last, "gap": gap, "until": until}


def _write_state(state: dict[str, Any]) -> None:
    _write(
        _push_dir() / "state",
        {
            "sent": {
                owner: cards[-500:] for owner, cards in state["sent"].items() if cards
            },
            "last": state["last"],
            "gap": bool(state["gap"]),
            "until": state["until"],
        },
    )


def _owes(card: str, owners: list[str], sent: dict[str, list[str]]) -> list[str]:
    return [owner for owner in owners if card not in sent.get(owner, [])]


def _batch(reasons: list[Reason], owners: list[str], sent: dict[str, list[str]]) -> list[Reason]:
    pending = [item for item in reasons if _owes(item.card, owners, sent)]
    if not pending:
        return []
    first = pending[0]
    key: object = first.card if first.kind == "down" else (first.kind, first.project)
    group = []
    for item in pending:
        item_key: object = item.card if item.kind == "down" else (item.kind, item.project)
        if item_key == key:
            group.append(item)
    return group


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    default = 443 if parts.scheme == "https" else 80
    port = parts.port
    if port and port != default:
        return f"{parts.scheme}://{host}:{port}"
    return f"{parts.scheme}://{host}"


def _vapid(key: ec.EllipticCurvePrivateKey, origin: str, now: int) -> str:
    header = _b64(b'{"typ":"JWT","alg":"ES256"}')
    claims = {"aud": origin, "exp": now + 12 * 3600, "sub": _SUB}
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    der = key.sign(signing, ec.ECDSA(hashes.SHA256()))
    number_r, number_s = utils.decode_dss_signature(der)
    signature = _b64(number_r.to_bytes(32, "big") + number_s.to_bytes(32, "big"))
    public = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    token = f"{header}.{payload}.{signature}"
    return f"vapid t={token}, k={_b64(public)}"


def encrypt(payload: bytes, ua_public: bytes, auth: bytes) -> bytes:
    """One aes128gcm record. ``ua_public`` is the subscription's uncompressed key."""

    if len(ua_public) != 65 or ua_public[0] != 4 or len(auth) != 16:
        raise FoilError("foil: push subscription is invalid")
    if len(payload) + 17 > RECORD:
        raise FoilError("foil: push subscription is invalid")
    private = ec.generate_private_key(ec.SECP256R1())
    as_public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = private.exchange(ec.ECDH(), peer)
    info = b"WebPush: info\x00" + ua_public + as_public
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth, info=info).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=salt,
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=salt,
        info=b"Content-Encoding: nonce\x00",
    ).derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + RECORD.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


def _post(url: str, body: bytes, headers: dict[str, str]) -> int:
    if not _endpoint_ok(url):
        return 0
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        return 0
    connection: HTTPConnection
    if parts.scheme == "https":
        connection = HTTPSConnection(parts.hostname, port or 443, timeout=3)
    else:
        connection = HTTPConnection(parts.hostname, port or 80, timeout=3)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    try:
        connection.request("POST", target, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        response.read(65536)
    except OSError:
        return 0
    finally:
        connection.close()
    return status


def _payload(group: list[Reason]) -> bytes:
    text = {"label": _label(group), "card": group[0].card}
    return json.dumps(text, separators=(",", ":")).encode()


def describe_card(card: str) -> dict[str, str] | None:
    """The label for one current card. No question text and no mail body."""

    if not _card_ok(card):
        return None
    if card == "down/bridge":
        state = _state()
        if state["gap"] or int(time.time()) < state["until"]:
            return {"label": "bridge down", "card": card}
        return None
    if card == "down/master":
        if _master_down():
            return {"label": "master down", "card": card}
        return None
    kind = {"q": "question", "s": "seat", "d": "delivery", "g": "done"}.get(card.split("/", 1)[0])
    if kind is None:
        return None
    enabled = dict.fromkeys(KINDS, False)
    enabled[kind] = True
    for reason in _reasons(enabled, gap=False):
        if reason.card == card:
            return {"label": _label([reason]), "card": card}
    return None


def deliver(now: float | None = None) -> dict[str, str] | None:
    """Send at most one notification. The same card is not sent again while it remains."""

    with _LOCK:
        return _deliver(time.time() if now is None else now)


def _deliver(now: float) -> dict[str, str] | None:
    enabled = settings()
    if not any(enabled.values()):
        return None
    gap = _note_gap(now) if enabled["down"] else False
    subs = _subscriptions()
    if not subs:
        return None
    reasons = _reasons(enabled, gap)
    current = {item.card for item in reasons}
    state = _state()
    sent: dict[str, list[str]] = state["sent"]
    for owner in list(sent):
        kept = [card for card in sent[owner] if card in current]
        if kept:
            sent[owner] = kept
        else:
            del sent[owner]
    owners = [owner for owner, _sub in subs]
    if state["last"] >= 0 and now - state["last"] < INTERVAL:
        _write_state(state)
        return None
    group = _batch(reasons, owners, sent)
    if not group:
        if not gap:
            state["gap"] = False
        _write_state(state)
        return None
    key = _private()
    shown: dict[str, str] | None = None
    bridge = any(item.card == "down/bridge" for item in group)
    by_owner = {owner: sub for owner, sub in subs}
    for owner in _owes_group(group, owners, sent):
        mine = [item for item in group if item.card not in sent.get(owner, [])]
        if not mine:
            continue
        sub = by_owner[owner]
        try:
            body = _payload(mine)
            sealed = encrypt(body, _b64decode(sub["p256dh"]), _b64decode(sub["auth"]))
        except (FoilError, ValueError):
            continue
        status = _post(
            sub["endpoint"],
            sealed,
            {
                "Authorization": _vapid(key, _origin(sub["endpoint"]), int(now)),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": TTL,
                "User-Agent": "foil-bridge",
            },
        )
        if status in {404, 410}:
            drop(owner)
        elif 200 <= status < 300:
            if shown is None:
                shown = json.loads(body)
            remembered = list(sent.get(owner, []))
            for item in mine:
                if item.card not in remembered:
                    remembered.append(item.card)
            sent[owner] = remembered
    state["last"] = int(now)
    if shown is not None and bridge:
        state["until"] = int(now) + GAP
        live = [owner for owner, _sub in _subscriptions()]
        if not _owes("down/bridge", live, sent):
            state["gap"] = False
    _write_state(state)
    return shown


def _owes_group(group: list[Reason], owners: list[str], sent: dict[str, list[str]]) -> list[str]:
    cards = [item.card for item in group]
    return [owner for owner in owners if any(card not in sent.get(owner, []) for card in cards)]
