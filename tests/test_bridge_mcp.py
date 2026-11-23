"""MCP on stdio and on POST /mcp. The home reads the same mailbox."""

from __future__ import annotations

import json
import os
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest
from foil_bridge import auth, mcp
from foil_bridge.cli import main as bridge_main
from foil_bridge.home import snapshot
from foil_bridge.transcript import read as read_transcript

from foil.bridge_cmd import read_desk_token
from foil.errors import FoilError
from foil.master import directory
from foil.project import foil_root, store_root
from tests.test_answer import _lead_mail, _question
from tests.test_bridge_evidence import FAKE, _registry, _seat
from tests.test_bridge_home import _key, _short_repo
from tests.test_bridge_http import NODE, _request, _serving

ACCEPT = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
USER = "Please ship the readme"
REPLY = "Ship it."


def _message(method: str, request_id: object = 1, **params: object) -> dict:
    body: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params:
        body["params"] = params
    return body


def _post(
    port: int,
    message: dict,
    *,
    token: str | None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    return _request(
        port,
        "POST",
        "/mcp",
        token=token,
        headers={**ACCEPT, **(headers or {})},
        body=message,
    )


def _result(payload: bytes) -> dict:
    return json.loads(payload)["result"]


def _protocol(port: int, token: str, message: dict, headers: dict[str, str]) -> str | None:
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "POST",
        "/mcp",
        body=json.dumps(message).encode(),
        headers={**ACCEPT, **headers, "Authorization": f"Bearer {token}"},
    )
    response = connection.getresponse()
    response.read()
    version = response.getheader("MCP-Protocol-Version")
    connection.close()
    return version


def _raw(port: int, token: str, body: bytes) -> tuple[int, bytes]:
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        "POST",
        "/mcp",
        body=body,
        headers={**ACCEPT, "Authorization": f"Bearer {token}"},
    )
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


def _mail(sender: str, body: str, stamp: str) -> None:
    folder = directory() / "mail"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-{sender}-0123abcd.md"
    path.write_text(
        "---\n"
        "contract: mail/v1\n"
        f"from: {sender}\n"
        "to: master\n"
        "time: 2026-11-23T15:00:00Z\n"
        "---\n"
        "\n"
        f"{body}\n",
        encoding="utf-8",
    )


def _when_user_writes(body: str) -> None:
    folder = directory() / "mail"
    deadline = time.time() + 5
    while time.time() < deadline:
        if folder.is_dir() and any("-user-" in path.name for path in folder.glob("*.md")):
            _mail("master", body, "20261123T150000Z")
            return
        time.sleep(0.01)
    raise AssertionError("user message was not written")


def _stdio(lines: list[dict], *, read: int) -> list[dict]:
    in_read, in_write = os.pipe()
    out_read, out_write = os.pipe()
    incoming = os.fdopen(in_read, "r", encoding="utf-8")
    outgoing = os.fdopen(out_write, "w", encoding="utf-8", buffering=1)
    thread = threading.Thread(target=mcp.serve_stdio, args=(incoming, outgoing), daemon=True)
    thread.start()
    writer = os.fdopen(in_write, "w", encoding="utf-8", buffering=1)
    reader = os.fdopen(out_read, "r", encoding="utf-8")
    found: list[dict] = []
    try:
        for line in lines:
            writer.write(json.dumps(line) + "\n")
            writer.flush()
        for _ in range(read):
            box: list[str] = []

            def take(slot: list[str] = box) -> None:
                slot.append(reader.readline())

            getter = threading.Thread(target=take, daemon=True)
            getter.start()
            getter.join(5)
            if getter.is_alive() or not box or not box[0]:
                raise AssertionError("no response")
            found.append(json.loads(box[0]))
        return found
    finally:
        writer.close()
        thread.join(2)
        reader.close()


def test_stdio_protocol_and_the_reply_is_in_the_conversation() -> None:
    early = _stdio([_message("tools/list")], read=1)[0]
    assert early["error"]["code"] == -32600
    assert early["error"]["message"] == "expected initialize"
    watcher = threading.Thread(target=_when_user_writes, args=(REPLY,), daemon=True)
    watcher.start()
    responses = _stdio(
        [
            _message(
                "initialize",
                protocolVersion="2025-11-25",
                capabilities={},
                clientInfo={"name": "test", "version": "0"},
            ),
            _message("notifications/initialized", request_id=None),
            _message("tools/list", 2),
            _message("tools/call", 3, name="talk_to_master", arguments={"text": USER}),
        ],
        read=3,
    )
    watcher.join(5)
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert responses[0]["result"]["serverInfo"]["name"] == "foil-bridge"
    names = [tool["name"] for tool in responses[1]["result"]["tools"]]
    assert names == [
        "talk_to_master",
        "brief",
        "inbox",
        "fleet",
        "agent_transcript",
        "answer",
    ]
    assert responses[2]["result"].get("isError") is not True
    assert responses[2]["result"]["content"][0]["text"] == f"{REPLY}\n"
    shown = snapshot()["messages"]
    assert any(item["from"] == "user" and USER in item["body"] for item in shown)
    assert any(item["from"] == "master" and REPLY in item["body"] for item in shown)


def test_mcp_command_rejects_an_extra_argument(capsys: pytest.CaptureFixture[str]) -> None:
    assert bridge_main(["mcp", "listen"]) == 2
    assert capsys.readouterr().err == "foil-bridge: unexpected argument\n"


def test_http_refuses_a_missing_bearer_and_shows_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        mode = (store_root() / "bridge" / "desk").stat().st_mode & 0o777
        assert mode == 0o600
        status, body = _post(port, _message("ping"), token=None)
        assert status == 401 and body == b""
        status, body = _request(
            port,
            "POST",
            f"/mcp?token={desk}",
            headers=ACCEPT,
            body=_message("ping"),
        )
        assert status == 401 and body == b""
        status, body = _post(port, _message("ping"), token="not-the-token")
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/mcp")
        assert status == 401 and body == b""
        status, body = _request(port, "GET", "/mcp", token=desk)
        assert status == 405 and body == b""
        status, body = _request(port, "DELETE", "/mcp", token=desk)
        assert status == 405 and body == b""
        status, body = _post(
            port, _message("ping"), token=desk, headers={"Accept": "application/json"}
        )
        assert status == 406 and body == b""
        status, body = _post(
            port,
            _message("ping"),
            token=desk,
            headers={"MCP-Protocol-Version": "1999-01-01"},
        )
        assert status == 400 and body == b""
        status, body = _post(
            port,
            _message("ping"),
            token=desk,
            headers={"Origin": "https://evil.example"},
        )
        assert status == 403 and body == b""
        forged = {
            "X-Forwarded-For": "203.0.113.8",
            "X-Forwarded-Host": NODE[0],
            "Tailscale-User-Login": NODE[1],
            "Tailscale-Funnel-Request": "?1",
        }
        status, body = _post(port, _message("ping"), token=desk, headers=forged)
        assert status == 403 and body == b""
        status, body = _post(port, {"jsonrpc": "2.0"}, token=desk)
        assert status == 200
        assert json.loads(body)["error"]["code"] == -32600
        status, body = _raw(port, desk, b"not-json")
        assert status == 400
        assert json.loads(body)["error"]["code"] == -32700
        status, body = _raw(port, desk, b"[1]")
        assert status == 400
        assert json.loads(body)["error"]["code"] == -32600


def test_token_and_tailnet_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch, NODE) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        owner = {
            "Host": NODE[0],
            "Tailscale-User-Login": NODE[1],
            "X-Forwarded-Host": NODE[0],
        }
        status, body = _post(port, _message("ping"), token=None, headers=owner)
        assert status == 401 and body == b""
        status, body = _post(port, _message("ping"), token=desk, headers=owner)
        assert status == 200
        assert _result(body) == {}
        assert _protocol(port, desk, _message("ping"), owner) == "2025-03-26"
        asked = _message(
            "initialize",
            protocolVersion="2025-11-25",
            capabilities={},
            clientInfo={"name": "test", "version": "0"},
        )
        assert _protocol(port, desk, asked, owner) == "2025-11-25"
        shared = {**owner, "Tailscale-User-Login": "someone-else"}
        status, _body = _post(port, _message("ping"), token=desk, headers=shared)
        assert status == 403
        funnel = {
            "Host": NODE[0],
            "X-Forwarded-Host": NODE[0],
            "Tailscale-Funnel-Request": "?1",
        }
        status, _body = _post(port, _message("ping"), token=desk, headers=funnel)
        assert status == 403
        device = auth.new_device()["token"]
        status, body = _post(port, _message("tools/list", 4), token=device, headers=owner)
        assert status == 200
        names = [tool["name"] for tool in _result(body)["tools"]]
        assert "kill" not in names
        assert "archive" not in names
        assert "merge" not in names
        assert "settings" not in names


def test_tagged_machine_refuses_a_forwarded_host(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch, None) as server:
        port = server.server_address[1]
        status, _body = _post(
            port,
            _message("ping"),
            token=read_desk_token(),
            headers={"Host": "box.tail1234.ts.net"},
        )
        assert status == 403


def test_talk_to_master_shows_on_the_home_and_a_missing_reply_stays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp, "WAIT", 2)
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        watcher = threading.Thread(target=_when_user_writes, args=(REPLY,), daemon=True)
        watcher.start()
        status, body = _post(
            port,
            _message("tools/call", 5, name="talk_to_master", arguments={"text": USER}),
            token=desk,
        )
        watcher.join(5)
        assert status == 200
        assert _result(body)["content"][0]["text"] == f"{REPLY}\n"
        status, home = _request(port, "GET", "/v1/home", token=desk)
        assert status == 200
        messages = json.loads(home)["messages"]
        assert any(item["from"] == "user" and USER in item["body"] for item in messages)
        calls = [path.name for path in (directory() / "mail").glob("*-user-*.md")]
        assert len(calls) == 1
        status, body = _post(
            port,
            _message("tools/call", 6, name="talk_to_master", arguments={"text": "still here"}),
            token=desk,
        )
        assert _result(body)["isError"] is True
        assert _result(body)["content"][0]["text"] == "foil-bridge: the master has not replied"
        status, home = _request(port, "GET", "/v1/home", token=desk)
        messages = json.loads(home)["messages"]
        assert any(item["from"] == "user" and "still here" in item["body"] for item in messages)


def test_cancel_keeps_the_message(monkeypatch: pytest.MonkeyPatch) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        box: list[tuple[int, bytes]] = []

        def call() -> None:
            box.append(
                _post(
                    port,
                    _message("tools/call", 7, name="talk_to_master", arguments={"text": USER}),
                    token=desk,
                )
            )

        thread = threading.Thread(target=call)
        thread.start()
        folder = directory() / "mail"
        deadline = time.time() + 5
        while time.time() < deadline and not (
            folder.is_dir() and any("-user-" in path.name for path in folder.glob("*.md"))
        ):
            time.sleep(0.01)
        status, body = _post(
            port,
            _message("notifications/cancelled", None, requestId=7),
            token=desk,
        )
        assert status == 202 and body == b""
        thread.join(5)
        assert not thread.is_alive()
        assert _result(box[0][1])["isError"] is True
        assert "cancelled" in _result(box[0][1])["content"][0]["text"]
        messages = snapshot()["messages"]
        assert any(item["from"] == "user" and USER in item["body"] for item in messages)


def test_unknown_tool_and_reads_and_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            desk = read_desk_token()
            for name in ("kill", "archive", "merge", "settings"):
                status, body = _post(
                    port,
                    _message("tools/call", 8, name=name, arguments={}),
                    token=desk,
                )
                assert status == 200
                assert json.loads(body)["error"]["code"] == -32602
            status, body = _post(
                port,
                _message("tools/call", 9, name="brief", arguments={}),
                token=desk,
            )
            assert status == 200
            brief = json.loads(_result(body)["content"][0]["text"])
            assert any(item["fleet"] == "main" and item["project"] == key for item in brief)
            status, body = _post(
                port,
                _message("tools/call", 10, name="inbox", arguments={}),
                token=desk,
            )
            assert isinstance(json.loads(_result(body)["content"][0]["text"]), list)
            status, body = _post(
                port,
                _message("tools/call", 11, name="fleet", arguments={"fleet": "main"}),
                token=desk,
            )
            assert _result(body)["isError"] is True
            assert _result(body)["content"][0]["text"] == "foil-bridge: fleet needs a project"
            status, body = _post(
                port,
                _message("tools/call", 12, name="fleet", arguments={"project": key}),
                token=desk,
            )
            rows = json.loads(_result(body)["content"][0]["text"])
            assert rows[0]["fleet"] == "main"
            assert "lead" in {seat["name"] for seat in rows[0]["seats"]}
            status, body = _post(
                port,
                _message(
                    "tools/call",
                    13,
                    name="answer",
                    arguments={"project": key, "fleet": "main", "id": "reading", "text": "keep"},
                ),
                token=desk,
            )
            assert _result(body)["content"][0]["text"] == "answered"
            files = _lead_mail(repo)
            assert len(files) == 1
            text = files[0].read_text(encoding="utf-8")
            assert "from: user" in text
            assert text.split("---", 2)[2].lstrip("\n").startswith("question: reading\n")
            status, _body = _post(
                port,
                _message(
                    "tools/call",
                    14,
                    name="answer",
                    arguments={"project": key, "fleet": "main", "id": "reading", "text": "later"},
                ),
                token=desk,
            )
            assert len(_lead_mail(repo)) == 1
            assert "keep" in files[0].read_text(encoding="utf-8")
            assert "later" not in files[0].read_text(encoding="utf-8")


def test_agent_transcript_is_a_masked_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("foil_bridge.transcript.user_home", lambda: tmp_path)
    monkeypatch.setattr(
        "foil_bridge.transcript.peek_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FoilError("foil: no transcript")),
    )
    folder = tmp_path / ".foil-fake" / "sessions"
    folder.mkdir(parents=True)
    lines = [
        {
            "role": "user",
            "kind": "message",
            "text": "hello from the seat",
            "tool": "",
            "usage": None,
        },
        {"role": "tool", "kind": "tool", "text": "ran true", "tool": "shell", "usage": None},
        {
            "role": "assistant",
            "kind": "message",
            "text": "password=hunter2",
            "tool": "",
            "usage": None,
        },
    ]
    (folder / f"{FAKE}.jsonl").write_text(
        "".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8"
    )
    with _short_repo(monkeypatch) as repo:
        _registry(repo, "main", {"worker": _seat("worker", "", FAKE)})
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            status, body = _post(
                port,
                _message(
                    "tools/call",
                    15,
                    name="agent_transcript",
                    arguments={"seat": "worker", "project": foil_root(repo).name},
                ),
                token=read_desk_token(),
            )
            assert status == 200
            text = _result(body)["content"][0]["text"]
            payload = json.loads(text)
            assert payload["level"] == "summary"
            assert payload["masked"] is True
            assert "hunter2" not in text
            assert "[hidden]" in text
            assert '"kind": "tool"' not in text
            direct = read_transcript("worker", project=foil_root(repo).name)
            assert direct["level"] == "summary"


def test_reply_cap() -> None:
    assert mcp._cap("x" * (mcp.REPLY_CAP + 5)).endswith("\n... truncated\n")


def test_a_refused_post_does_not_become_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        connection = HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request(
            "POST",
            "/mcp",
            body=json.dumps(_message("ping")).encode(),
            headers=dict(ACCEPT),
        )
        response = connection.getresponse()
        assert response.status == 401
        assert response.read() == b""
        assert response.getheader("Connection") == "close"
        connection.request(
            "POST",
            "/mcp",
            body=json.dumps(_message("ping")).encode(),
            headers={**ACCEPT, "Authorization": f"Bearer {desk}"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["result"] == {}
        connection.close()


def test_an_overlapping_call_keeps_the_first_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp, "WAIT", 5)
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        box: list[tuple[int, bytes]] = []

        def call() -> None:
            box.append(
                _post(
                    port,
                    _message("tools/call", 7, name="talk_to_master", arguments={"text": USER}),
                    token=desk,
                )
            )

        thread = threading.Thread(target=call)
        thread.start()
        folder = directory() / "mail"
        deadline = time.time() + 5
        while time.time() < deadline and not (
            folder.is_dir() and any("-user-" in path.name for path in folder.glob("*.md"))
        ):
            time.sleep(0.01)
        status, body = _post(
            port,
            _message("tools/call", 7, name="brief", arguments={}),
            token=desk,
        )
        assert status == 200
        assert json.loads(body)["error"]["code"] == -32600
        assert json.loads(body)["error"]["message"] == "request id is in use"
        status, body = _post(
            port,
            _message("notifications/cancelled", None, requestId=7),
            token=desk,
        )
        assert status == 202 and body == b""
        thread.join(5)
        assert not thread.is_alive()
        assert _result(box[0][1])["isError"] is True
        assert "cancelled" in _result(box[0][1])["content"][0]["text"]
        assert len(list(folder.glob("*-user-*.md"))) == 1


def test_a_queued_talk_can_be_cancelled_before_it_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp, "WAIT", 5)
    with _serving(monkeypatch) as server:
        port = server.server_address[1]
        desk = read_desk_token()
        first: list[tuple[int, bytes]] = []
        second: list[tuple[int, bytes]] = []

        def call(box: list[tuple[int, bytes]], request_id: int, text: str) -> None:
            box.append(
                _post(
                    port,
                    _message(
                        "tools/call", request_id, name="talk_to_master", arguments={"text": text}
                    ),
                    token=desk,
                )
            )

        lead = threading.Thread(target=call, args=(first, 21, USER))
        lead.start()
        folder = directory() / "mail"
        deadline = time.time() + 5
        while time.time() < deadline and not (
            folder.is_dir() and any("-user-" in path.name for path in folder.glob("*.md"))
        ):
            time.sleep(0.01)
        queued = threading.Thread(target=call, args=(second, 22, "second"))
        queued.start()
        time.sleep(0.2)
        status, body = _post(
            port,
            _message("notifications/cancelled", None, requestId=22),
            token=desk,
        )
        assert status == 202 and body == b""
        queued.join(2)
        assert not queued.is_alive()
        assert _result(second[0][1])["isError"] is True
        assert "cancelled" in _result(second[0][1])["content"][0]["text"]
        assert len(list(folder.glob("*-user-*.md"))) == 1
        _post(
            port,
            _message("notifications/cancelled", None, requestId=21),
            token=desk,
        )
        lead.join(5)
        assert not lead.is_alive()
