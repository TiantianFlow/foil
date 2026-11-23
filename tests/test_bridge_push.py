"""Web Push is a label and a card id. The fleet's text stays on the machine."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from foil_bridge import push
from foil_bridge.pages import asset
from foil_bridge.server import open_server
from playwright.sync_api import Browser, expect, sync_playwright

from foil import master
from foil.bridge_cmd import bridge_root, read_desk_token
from foil.cli import main
from foil.errors import FoilError
from foil.project import foil_root
from foil.tmux import TmuxController
from tests.test_answer import _lead_mail, _question
from tests.test_bridge_home import _key, _master_mail, _short_repo
from tests.test_bridge_http import _origin, _request, _serving

ASK = "Which reading?"
WHY = "The note says keep."
GOAL = "the reading is chosen."
MASTER_Q = "For the master?"
EVIDENCE = "board/status.md"
CHAT = "CHAT-REPLY-PHRASE-9f3a"
MAIL = "MAIL-BODY-PHRASE-9f3a"
OTHER = "OTHER-ASK-PHRASE-9f3a"
STUCK = "STUCK-BODY-PHRASE-9f3a"
T0 = 1_700_000_000
_SUB = "https://github.com/TiantianFlow/foil"


class _Capture(ThreadingHTTPServer):
    rows: list[tuple[dict[str, str], bytes]]
    code: int
    codes: list[int]


class _Posted(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        headers = {key.lower(): value for key, value in self.headers.items()}
        server: _Capture = self.server  # type: ignore[assignment]
        server.rows.append((headers, body))
        code = server.codes.pop(0) if server.codes else server.code
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _endpoint() -> Iterator[_Capture]:
    server = _Capture(("127.0.0.1", 0), _Posted)
    server.rows = []
    server.code = 201
    server.codes = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def _subscribe(endpoint: str) -> tuple[ec.EllipticCurvePrivateKey, bytes, bytes]:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    push.save_subscription(
        "desk",
        {"endpoint": endpoint, "p256dh": _b64(public), "auth": _b64(auth)},
    )
    return private, public, auth


def _decrypt(blob: bytes, private: ec.EllipticCurvePrivateKey, public: bytes, auth: bytes) -> bytes:
    assert int.from_bytes(blob[16:20], "big") == 4096
    length = blob[20]
    assert length == 65
    ephemeral = blob[21 : 21 + length]
    assert ephemeral[0] == 4
    peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ephemeral)
    shared = private.exchange(ec.ECDH(), peer)
    info = b"WebPush: info\x00" + public + ephemeral
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth, info=info).derive(shared)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=blob[:16],
        info=b"Content-Encoding: aes128gcm\x00",
    ).derive(ikm)
    nonce = HKDF(
        algorithm=hashes.SHA256(),
        length=12,
        salt=blob[:16],
        info=b"Content-Encoding: nonce\x00",
    ).derive(ikm)
    plain = AESGCM(key).decrypt(nonce, blob[21 + length :], None)
    plain = plain.rstrip(b"\x00")
    assert plain.endswith(b"\x02")
    return plain[:-1]


def _claims(headers: dict[str, str], origin: str, when: int = T0) -> None:
    authorization = headers["authorization"]
    assert authorization.startswith("vapid t=")
    token, key = authorization.removeprefix("vapid t=").split(", k=")
    header, payload, signature = token.split(".")
    assert json.loads(_b64decode(header)) == {"typ": "JWT", "alg": "ES256"}
    raw = _b64decode(signature)
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), _b64decode(key))
    public.verify(der, f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256()))
    claims = json.loads(_b64decode(payload))
    assert claims["aud"] == origin
    assert claims["sub"] == _SUB
    assert claims["exp"] == int(when) + 12 * 3600
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["ttl"] == "300"
    assert headers["user-agent"] == "foil-bridge"


def _absent(blob: bytes, phrases: list[str]) -> None:
    text = blob.decode("utf-8", "replace")
    for phrase in phrases:
        assert phrase not in text


def _one(
    server: _Capture,
    private: ec.EllipticCurvePrivateKey,
    public: bytes,
    auth: bytes,
    *,
    origin: str,
    phrases: list[str],
    check_claims: bool = True,
    when: int = T0,
) -> dict[str, str]:
    assert len(server.rows) == 1
    headers, body = server.rows[0]
    _absent(body, phrases)
    if check_claims:
        _claims(headers, origin, when)
    plain = _decrypt(body, private, public, auth)
    _absent(plain, phrases)
    parsed = json.loads(plain)
    assert set(parsed) == {"label", "card"}
    secret = json.loads((bridge_root() / "push" / "vapid").read_text(encoding="utf-8"))["d"]
    assert secret not in body.decode("utf-8", "replace")
    assert secret not in plain.decode()
    return parsed


def _fleet(repo: Path) -> Path:
    directory = foil_root(repo) / "fleets" / "main"
    (directory / "run").mkdir(parents=True, exist_ok=True)
    (directory / "board").mkdir(exist_ok=True)
    (directory / "run" / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "fleet_id": "main",
                "tmux_session": "",
                "lead": "",
                "seats": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return directory


def _status(board: Path, text: str) -> None:
    (board / "status.md").write_text(text, encoding="utf-8")


def _mail(board: Path, mailbox: str, name: str, body: str) -> None:
    folder = board / "mail" / mailbox
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(
        "---\ncontract: mail/v1\nfrom: user\nto: "
        + mailbox
        + "\ntime: 2020-01-01T00:00:00Z\n---\n\n"
        + body
        + "\n",
        encoding="utf-8",
    )


def test_a_question_push_is_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    """One question, a chat reply, and unread mail: the wire sees the label only."""

    phrases = [ASK, WHY, GOAL, MASTER_Q, EVIDENCE, CHAT, MAIL]
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        _mail(board, "user", "20261122T120000Z-master-0123abcd.md", MAIL)
        _master_mail("master", CHAT)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        assert push.deliver(now=T0) is None and server.rows == []
        push.update_settings({"question": True})
        push.deliver(now=T0)
        key = _key(repo)
        parsed = _one(server, private, public, auth, origin=origin, phrases=phrases)
        assert parsed["label"] == f"{push.short_name(key)}: 1 decision"
        assert parsed["card"] == f"q/{key}/main/reading"
        push.deliver(now=T0 + 60)
        assert len(server.rows) == 1
        assert main(["answer", "reading", "keep"]) == 0
        assert push.deliver(now=T0 + 120) is None and len(server.rows) == 1
        for path in _lead_mail(repo):
            path.unlink()
        push.deliver(now=T0 + 180)
        assert len(server.rows) == 2
        plain = _decrypt(server.rows[1][1], private, public, auth)
        _absent(plain, phrases)
        stored = (bridge_root() / "push" / "state").read_text(encoding="utf-8")
        _absent(stored.encode(), phrases)


def test_two_questions_are_one_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        text = (board / "status.md").read_text(encoding="utf-8")
        extra = "  - id: other\n    ask: " + OTHER + "\n    options: [a]\n    owner: human\n"
        text = text.replace("  - id: theirs", extra + "  - id: theirs")
        _status(board, text)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"question": True})
        push.deliver(now=T0)
        key = _key(repo)
        parsed = _one(
            server, private, public, auth, origin=origin, phrases=[ASK, OTHER, WHY, GOAL]
        )
        assert parsed["label"] == f"{push.short_name(key)}: 2 decisions"
        assert parsed["card"] == f"q/{key}/main/reading"
        sent = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))["sent"]
        assert sent == {"desk": [f"q/{key}/main/reading", f"q/{key}/main/other"]}


def test_a_question_goes_before_a_finished_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        fleet = _fleet(repo)
        _status(
            fleet / "board",
            "---\ncontract: status/v1\nstate: done\nquestions:\n"
            "  - id: reading\n    ask: Which reading?\n    options: [keep]\n"
            "    why: The note says keep.\n    owner: human\n---\n"
            "## Mandate\nDone when: GOAL-PHRASE-the-reading.\n",
        )
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"question": True, "done": True})
        push.deliver(now=T0)
        key = _key(repo)
        name = push.short_name(key)
        first = _one(
            server,
            private,
            public,
            auth,
            origin=origin,
            phrases=[ASK, WHY, "GOAL-PHRASE-the-reading"],
        )
        assert first == {"label": f"{name}: 1 decision", "card": f"q/{key}/main/reading"}
        assert push.deliver(now=T0 + 30) is None and len(server.rows) == 1
        push.deliver(now=T0 + 60)
        assert len(server.rows) == 2
        second = json.loads(_decrypt(server.rows[1][1], private, public, auth))
        assert second == {"label": f"{name}: 1 goal", "card": f"g/{key}/main"}
        _absent(server.rows[1][1], [ASK, "GOAL-PHRASE-the-reading"])
        assert push.deliver(now=T0 + 120) is None and len(server.rows) == 2


def test_chat_and_a_running_fleet_send_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        fleet = _fleet(repo)
        _status(fleet / "board", "---\ncontract: status/v1\nstate: working\n---\n")
        _mail(fleet / "board", "user", "20261122T120000Z-master-0123abcd.md", MAIL)
        _master_mail("master", CHAT)
        port = server.server_address[1]
        _subscribe(f"http://127.0.0.1:{port}/push")
        push.update_settings({kind: True for kind in push.KINDS})
        assert push.deliver(now=T0) is None
        assert server.rows == []


def test_a_dead_seat_is_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args: False)
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"seat": True})
        push.deliver(now=T0)
        key = _key(repo)
        parsed = _one(server, private, public, auth, origin=origin, phrases=[ASK, WHY, "is dead"])
        assert parsed["label"] == f"{push.short_name(key)}: 1 seat"
        assert parsed["card"] == f"s/{key}/main/lead"


def test_a_paused_seat_is_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args: True)
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        turns = foil_root(repo) / "fleets" / "main" / "run" / "turns"
        turns.mkdir()
        (turns / "lead.json").write_text(
            json.dumps({"schema_version": 1, "paused": True}) + "\n", encoding="utf-8"
        )
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"seat": True})
        push.deliver(now=T0)
        parsed = _one(server, private, public, auth, origin=origin, phrases=[ASK, "is paused"])
        assert parsed["label"].endswith("1 seat")
        assert "/lead" in parsed["card"]


def test_a_stuck_delivery_is_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        fleet = _fleet(repo)
        _status(fleet / "board", "---\ncontract: status/v1\nstate: working\n---\n")
        _mail(fleet / "board", "other", "20200101T000000Z-user-abcdef12.md", STUCK)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"delivery": True})
        push.deliver(now=T0)
        key = _key(repo)
        parsed = _one(server, private, public, auth, origin=origin, phrases=[STUCK])
        assert parsed["label"] == f"{push.short_name(key)}: 1 delivery"
        assert parsed["card"] == f"d/{key}/main/mail/other/20200101T000000Z-user-abcdef12.md"


def test_the_master_down_is_its_own_label(monkeypatch: pytest.MonkeyPatch) -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"down": True})
        window = master.directory() / "run" / "window.json"
        window.parent.mkdir(parents=True)
        window.write_text(
            json.dumps({"schema_version": 1, "window_id": "@1"}) + "\n", encoding="utf-8"
        )
        monkeypatch.setattr(master, "_alive", lambda record: True)
        assert push.deliver(now=T0) is None and server.rows == []
        monkeypatch.setattr(master, "_alive", lambda record: False)
        push.deliver(now=T0)
        parsed = _one(server, private, public, auth, origin=origin, phrases=[])
        assert parsed == {"label": "master down", "card": "down/master"}


def test_a_paused_master_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"down": True})
        record = master.directory() / "run" / "record.json"
        record.parent.mkdir(parents=True)
        record.write_text(
            json.dumps({"schema_version": 1, "paused": True}) + "\n", encoding="utf-8"
        )
        push.deliver(now=T0)
        parsed = _one(server, private, public, auth, origin=origin, phrases=[])
        assert parsed == {"label": "master down", "card": "down/master"}


def test_a_bridge_gap_is_one_label_and_the_card_stays() -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"down": True})
        assert push.deliver() is None and server.rows == []
        heartbeat = bridge_root() / "push" / "heartbeat"
        assert heartbeat.is_file() and _mode(heartbeat) == 0o600
        now = int(time.time())
        heartbeat.write_text(json.dumps({"t": now - push.GAP - 1}) + "\n", encoding="utf-8")
        push.deliver()
        parsed = _one(
            server, private, public, auth, origin=origin, phrases=[], check_claims=False
        )
        assert parsed == {"label": "bridge down", "card": "down/bridge"}
        assert push.describe_card("down/bridge") == parsed
        assert push.deliver() is None and len(server.rows) == 1
        later = int(time.time()) + push.INTERVAL
        heartbeat.write_text(json.dumps({"t": later - push.GAP - 1}) + "\n", encoding="utf-8")
        assert push.deliver(now=later) == parsed
        assert len(server.rows) == 2


def test_a_failed_send_waits_and_a_gone_subscription_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        server.code = 500
        _subscribe(f"http://127.0.0.1:{port}/push")
        push.update_settings({"question": True})
        assert push.deliver(now=T0) is None and len(server.rows) == 1
        stored = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))
        assert stored["sent"] == {}
        server.code = 201
        assert push.deliver(now=T0 + 30) is None and len(server.rows) == 1
        assert push.deliver(now=T0 + 60) is not None and len(server.rows) == 2
        assert main(["answer", "reading", "keep"]) == 0
        assert push.deliver(now=T0 + 120) is None and len(server.rows) == 2
        for path in _lead_mail(repo):
            path.unlink()
        server.code = 410
        server.rows.clear()
        assert push.deliver(now=T0 + 180) is None and len(server.rows) == 1
        assert not (bridge_root() / "push" / "subscriptions" / "desk").exists()
        stored = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))
        assert stored["sent"] == {}


def test_a_failed_device_is_retried_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        device = "0123456789abcdef"
        folder = bridge_root() / "devices"
        folder.mkdir(parents=True)
        (folder / device).write_text("x\n", encoding="utf-8")
        _subscribe(f"{origin}/push")
        other = ec.generate_private_key(ec.SECP256R1())
        point = other.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        push.save_subscription(
            device,
            {"endpoint": f"{origin}/push", "p256dh": _b64(point), "auth": _b64(os.urandom(16))},
        )
        server.codes = [500, 201]
        push.update_settings({"question": True})
        assert push.deliver(now=T0) is not None and len(server.rows) == 2
        key = _key(repo)
        card = f"q/{key}/main/reading"
        sent = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))["sent"]
        assert sent == {"desk": [card]}
        assert push.deliver(now=T0 + 30) is None and len(server.rows) == 2
        server.codes = [201]
        assert push.deliver(now=T0 + 60) is not None and len(server.rows) == 3
        sent = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))["sent"]
        assert sent["desk"] == [card]
        assert sent[device] == [card]


def test_a_later_outage_notifies_a_browser_that_already_accepted() -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        device = "0123456789abcdef"
        folder = bridge_root() / "devices"
        folder.mkdir(parents=True)
        (folder / device).write_text("x\n", encoding="utf-8")
        _subscribe(f"{origin}/push")
        other = ec.generate_private_key(ec.SECP256R1())
        point = other.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        push.save_subscription(
            device,
            {"endpoint": f"{origin}/push", "p256dh": _b64(point), "auth": _b64(os.urandom(16))},
        )
        push.update_settings({"down": True})
        heartbeat = bridge_root() / "push" / "heartbeat"
        heartbeat.write_text(json.dumps({"t": T0 - push.GAP - 5}) + "\n", encoding="utf-8")
        server.codes = [500, 201]
        assert push.deliver(now=T0) is not None and len(server.rows) == 2
        sent = json.loads((bridge_root() / "push" / "state").read_text(encoding="utf-8"))["sent"]
        assert "down/bridge" in sent["desk"]
        assert device not in sent
        heartbeat.write_text(json.dumps({"t": T0 + 60 - push.GAP - 5}) + "\n", encoding="utf-8")
        server.codes = [201, 201]
        assert push.deliver(now=T0 + 60) is not None
        assert len(server.rows) == 4


def test_each_browser_is_sent_only_its_new_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        desk_key, desk_pub, desk_auth = _subscribe(f"{origin}/push")
        push.update_settings({"question": True})
        push.deliver(now=T0)
        board = foil_root(repo) / "fleets" / "main" / "board"
        text = (board / "status.md").read_text(encoding="utf-8")
        extra = "  - id: other\n    ask: " + OTHER + "\n    options: [a]\n    owner: human\n"
        _status(board, text.replace("  - id: theirs", extra + "  - id: theirs"))
        device = "0123456789abcdef"
        folder = bridge_root() / "devices"
        folder.mkdir(parents=True)
        (folder / device).write_text("x\n", encoding="utf-8")
        phone = ec.generate_private_key(ec.SECP256R1())
        point = phone.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        phone_auth = os.urandom(16)
        push.save_subscription(
            device,
            {"endpoint": f"{origin}/push", "p256dh": _b64(point), "auth": _b64(phone_auth)},
        )
        server.rows.clear()
        push.deliver(now=T0 + 60)
        assert len(server.rows) == 2
        key = _key(repo)
        phone_body = json.loads(_decrypt(server.rows[0][1], phone, point, phone_auth))
        desk_body = json.loads(_decrypt(server.rows[1][1], desk_key, desk_pub, desk_auth))
        assert phone_body["label"].endswith("2 decisions")
        assert phone_body["card"] == f"q/{key}/main/reading"
        assert desk_body == {
            "label": f"{push.short_name(key)}: 1 decision",
            "card": f"q/{key}/main/other",
        }
        _absent(server.rows[0][1] + server.rows[1][1], [ASK, OTHER])


def test_a_large_sent_record_is_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        _subscribe(f"http://127.0.0.1:{port}/push")
        push.update_settings({"question": True})
        key = _key(repo)
        card = f"q/{key}/main/reading"
        filler = [f"q/{'a' * 40}/main/{'b' * 40}" for _ in range(120)]
        record = {"sent": {"desk": [card, *filler]}, "last": -1, "gap": False, "until": 0}
        path = bridge_root() / "push" / "state"
        path.write_text(json.dumps(record), encoding="utf-8")
        assert path.stat().st_size > 8192
        assert push.deliver(now=T0) is None
        assert server.rows == []


def test_a_changed_endpoint_has_not_already_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo, _endpoint() as server:
        _question(repo)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        private, public, auth = _subscribe(f"{origin}/push")
        push.update_settings({"question": True})
        assert push.deliver(now=T0) is not None and len(server.rows) == 1
        push.save_subscription(
            "desk",
            {"endpoint": f"{origin}/push", "p256dh": _b64(public), "auth": _b64(auth)},
        )
        assert push.deliver(now=T0 + 60) is None and len(server.rows) == 1
        push.save_subscription(
            "desk",
            {"endpoint": f"{origin}/again", "p256dh": _b64(public), "auth": _b64(auth)},
        )
        assert push.deliver(now=T0 + 120) is not None and len(server.rows) == 2
        plain = _decrypt(server.rows[1][1], private, public, auth)
        assert ASK.encode() not in plain


def test_a_revoked_device_is_not_saved() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    point = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    with pytest.raises(FoilError, match="push subscription is invalid"):
        push.save_subscription(
            "0123456789abcdef",
            {
                "endpoint": "http://127.0.0.1:9/push",
                "p256dh": _b64(point),
                "auth": _b64(os.urandom(16)),
            },
        )


def test_turning_down_on_does_not_inherit_a_stale_stamp() -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        _subscribe(f"http://127.0.0.1:{port}/push")
        heartbeat = bridge_root() / "push" / "heartbeat"
        heartbeat.write_text(json.dumps({"t": 1}) + "\n", encoding="utf-8")
        push.update_settings({"down": True})
        assert push.deliver() is None and server.rows == []
        stamp = json.loads(heartbeat.read_text(encoding="utf-8"))["t"]
        assert stamp >= int(time.time()) - 5


def test_an_outage_waits_for_a_subscriber() -> None:
    with _endpoint() as server:
        port = server.server_address[1]
        push.update_settings({"down": True})
        heartbeat = bridge_root() / "push" / "heartbeat"
        heartbeat.write_text(json.dumps({"t": 1}) + "\n", encoding="utf-8")
        assert push.deliver(now=T0) is None and server.rows == []
        _subscribe(f"http://127.0.0.1:{port}/push")
        assert push.deliver(now=T0) == {"label": "bridge down", "card": "down/bridge"}
        assert len(server.rows) == 1


def test_an_off_curve_key_is_refused() -> None:
    raw = bytes([4]) + bytes(64)
    with pytest.raises(FoilError, match="push subscription is invalid"):
        push.save_subscription(
            "desk",
            {
                "endpoint": "http://127.0.0.1:9/push",
                "p256dh": _b64(raw),
                "auth": _b64(os.urandom(16)),
            },
        )


def test_a_present_vapid_file_is_not_replaced() -> None:
    path = bridge_root() / "push" / "vapid"
    path.parent.mkdir(parents=True)
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FoilError, match="push subscription is invalid"):
        push.public_key()
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_a_bad_endpoint_is_refused() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    auth = _b64(os.urandom(16))
    point = _b64(public)
    samples = [
        "http://example.com/push",
        "file:///tmp/push",
        "http://user:pass@127.0.0.1/push",
        "http://127.0.0.1:9/push extra",
        "https://127.0.0.1:99999/push",
        "http://127.0.0.1:0/push",
    ]
    for endpoint in samples:
        with pytest.raises(FoilError, match="push subscription is invalid"):
            push.save_subscription(
                "desk", {"endpoint": endpoint, "p256dh": point, "auth": auth}
            )
    with pytest.raises(FoilError, match="push subscription is invalid"):
        push.save_subscription(
            "desk",
            {
                "endpoint": "http://127.0.0.1:9/push",
                "p256dh": point,
                "auth": auth,
                "extra": "no",
            },
        )


def test_switches_start_off(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        status, body = _request(port, "GET", "/v1/push")
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/v1/push", token=desk)
        assert status == 200
        view = json.loads(body)
        assert view["subscribed"] is False
        assert view["kinds"] == {kind: False for kind in push.KINDS}
        again = json.loads(_request(port, "GET", "/v1/push", token=desk)[1])
        assert again["publicKey"] == view["publicKey"]
        vapid = bridge_root() / "push" / "vapid"
        assert _mode(vapid) == 0o600
        assert _mode(vapid.parent) == 0o700
        status, body = _request(
            port,
            "POST",
            "/v1/push/settings",
            token=desk,
            headers=_origin(port),
            body={"question": "yes"},
        )
        assert status == 400 and b"push setting is invalid" in body
        status, _body = _request(
            port,
            "POST",
            "/v1/push/settings",
            token=desk,
            headers=_origin(port),
            body={"nope": True},
        )
        assert status == 400
        status, _body = _request(
            port, "POST", "/v1/push/settings", token=desk, body={"question": True}
        )
        assert status == 403
        status, body = _request(
            port,
            "POST",
            "/v1/push/settings",
            token=desk,
            headers=_origin(port),
            body={"question": True},
        )
        assert status == 200
        assert json.loads(body) == {
            "question": True,
            "seat": False,
            "delivery": False,
            "done": False,
            "down": False,
        }


def test_the_card_route_is_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo, _serving(monkeypatch) as server:
        _question(repo)
        key = _key(repo)
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        card = f"q/{key}/main/reading"
        status, body = _request(
            port, "GET", "/v1/push/card?id=" + quote(card, safe=""), token=desk
        )
        assert status == 200
        assert json.loads(body)["card"] == card
        assert ASK.encode() not in body
        status, body = _request(
            port, "GET", "/v1/push/card?id=" + quote(card + "/../x", safe=""), token=desk
        )
        assert status == 404 and body == b""
        fleet = foil_root(repo) / "fleets" / "main"
        _status(
            fleet / "board",
            "---\ncontract: status/v1\nstate: done\n---\n## Mandate\nDone when: "
            "GOAL-PHRASE-the-reading.\n",
        )
        goal = f"g/{key}/main"
        status, body = _request(
            port, "GET", "/v1/push/card?id=" + quote(goal, safe=""), token=desk
        )
        assert status == 200
        assert json.loads(body) == {"label": f"{push.short_name(key)}: 1 goal", "card": goal}
        assert b"GOAL-PHRASE-the-reading" not in body
        status, body = _request(
            port, "GET", "/v1/push/card?id=" + quote(card, safe=""), token=desk
        )
        assert status == 404


def test_revoking_a_device_drops_its_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        status, body = _request(
            port, "POST", "/v1/pairing", token=desk, headers=_origin(port)
        )
        code = json.loads(body)["code"]
        status, body = _request(
            port, "POST", "/v1/pair", body={"code": code}, headers=_origin(port)
        )
        device = json.loads(body)
        private = ec.generate_private_key(ec.SECP256R1())
        public = private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        push.save_subscription(
            device["id"],
            {
                "endpoint": "http://127.0.0.1:9/push",
                "p256dh": _b64(public),
                "auth": _b64(os.urandom(16)),
            },
        )
        path = bridge_root() / "push" / "subscriptions" / device["id"]
        assert path.is_file() and _mode(path) == 0o600
        status, _body = _request(
            port,
            "POST",
            f"/v1/devices/{device['id']}/revoke",
            token=desk,
            headers=_origin(port),
        )
        assert status == 200
        assert not path.exists()


def test_the_service_worker_opens_the_card() -> None:
    text = asset("sw.js").decode()
    assert "showNotification" in text
    assert "notificationclick" in text
    assert 'url.searchParams.set("card", card)' in text
    assert 'body: ""' in text


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def test_a_card_link_opens_that_question(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        card = f"q/{key}/main/reading"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            with page.expect_response(
                lambda response: response.url.endswith("/v1/push")
                and response.request.method == "GET"
            ):
                page.goto(f"{origin}/?token={token}&card={quote(card, safe='')}")
            current = page.locator('article[aria-current="true"]')
            expect(current).to_have_count(1)
            expect(current).to_contain_text(ASK)
            expect(page.locator("#opened")).to_be_empty()
            box = page.locator('#notify input[data-kind="question"]')
            expect(box).not_to_be_checked()
            with page.expect_response(
                lambda response: response.url.endswith("/v1/push/settings")
                and response.request.method == "POST"
            ) as saved:
                box.check()
            assert saved.value.ok
            page.reload()
            expect(page.locator('#notify input[data-kind="question"]')).to_be_checked()
            expect(page.locator('#notify input[data-kind="seat"]')).not_to_be_checked()
            page.goto(f"{origin}/?token={token}&card={quote(card + '-gone', safe='')}")
            expect(page.get_by_text("That card is gone.")).to_be_visible()
            goal = f"g/{key}/main"
            board = foil_root(repo) / "fleets" / "main" / "board" / "status.md"
            board.write_text(
                "---\ncontract: status/v1\nstate: done\n---\n## Mandate\nDone when: x.\n",
                encoding="utf-8",
            )
            page.goto(f"{origin}/?token={token}&card={quote(goal, safe='')}")
            expect(page.locator("#opened article")).to_have_count(1)
            with page.expect_response(
                lambda response: response.url.endswith("/v1/home")
                and response.request.method == "GET"
            ):
                stamp = time.time() + 2
                os.utime(board, (stamp, stamp))
            expect(page.locator("#opened article")).to_have_count(1)
            expect(page.locator("#opened")).to_contain_text("1 goal")
            board.write_text(
                "---\ncontract: status/v1\nstate: working\n---\n", encoding="utf-8"
            )
            with page.expect_response(
                lambda response: response.url.endswith("/v1/home")
                and response.request.method == "GET"
            ):
                stamp = time.time() + 4
                os.utime(board, (stamp, stamp))
            expect(page.get_by_text("That card is gone.")).to_be_visible()
            expect(page.locator("#opened article")).to_have_count(0)
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []
