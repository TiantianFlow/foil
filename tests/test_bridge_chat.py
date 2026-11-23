"""A seat page chats with that seat. Mail is from the human, and the state is the log."""

from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest
from foil_bridge.chat import WAITING
from foil_bridge.evidence import page
from foil_bridge.home import snapshot
from foil_bridge.pages import evidence_html

from foil.answer import quoted_answer
from foil.bridge_cmd import read_desk_token
from foil.cli import main
from foil.delivery import list_pointers, note, pending_pointers
from foil.project import foil_root
from foil.store import fleet_dir
from tests.test_answer import _lead_mail, _question
from tests.test_bridge_home import _key, _short_repo
from tests.test_bridge_http import _origin, _request, _serving
from tests.test_room_post import _fleet, _seat


def _chat(
    port: int, token: str, project: str, fleet: str, seat: str
) -> tuple[int, dict]:
    query = urlencode({"project": project, "fleet": fleet, "seat": seat})
    status, body = _request(port, "GET", "/v1/chat?" + query, token=token)
    return status, json.loads(body)


def _post(port: int, token: str, payload: dict) -> tuple[int, dict]:
    status, body = _request(
        port, "POST", "/v1/chat", token=token, headers=_origin(port), body=payload
    )
    return status, json.loads(body)


def test_mail_to_a_worker_reaches_the_seat_and_the_pointer_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"), _seat("worker", window="@2"))
        key = _key(repo)
        fleet = fleet_dir(repo, "main")
        lead_mail = fleet / "board" / "mail" / "lead"
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            before = set(lead_mail.glob("*.md")) if lead_mail.is_dir() else set()
            status, _body = _post(
                port,
                token,
                {"project": key, "fleet": "main", "seat": "worker", "text": "look here"},
            )
            assert status == 200
            files = sorted((fleet / "board" / "mail" / "worker").glob("*.md"))
            assert len(files) == 1
            assert "from: user" in files[0].read_text(encoding="utf-8")
            pending = pending_pointers(fleet)
            assert [item["to"] for item in pending] == ["worker"]
            assert pending[0]["path"].endswith(files[0].name)
            after = set(lead_mail.glob("*.md")) if lead_mail.is_dir() else set()
            assert after == before
            list_pointers(fleet, [pending[0]["path"]])
            assert pending_pointers(fleet) == []
            status, _body = _post(
                port,
                token,
                {"project": key, "fleet": "main", "seat": "worker", "text": "and here"},
            )
            assert status == 200
            pending = pending_pointers(fleet)
            assert len(pending) == 1
            assert files[0].name not in pending[0]["path"]
            status, thread = _chat(port, token, key, "main", "worker")
            assert status == 200
            assert thread["questions"] == []
            assert [item["state"] for item in thread["messages"]] == ["queued", "queued"]


def test_delivery_state_follows_the_log(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"), _seat("worker", window="@2"))
        key = _key(repo)
        fleet = fleet_dir(repo, "main")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, _body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "worker", "text": "ping"}
            )
            assert status == 200
            status, thread = _chat(port, token, key, "main", "worker")
            assert thread["messages"][0]["state"] == "queued"
            note(fleet, "worker", pending_pointers(fleet)[0]["path"], "delivered")
            status, thread = _chat(port, token, key, "main", "worker")
            assert thread["messages"][0]["state"] == "delivered"


def test_a_joined_lead_lands_when_its_session_is_waiting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _short_repo(monkeypatch) as repo:
        assert main(["seat", "join", "lead", "--harness", "fake", "--session", "s-1"]) == 0
        capsys.readouterr()
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, _body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "lead", "text": "ping"}
            )
            assert status == 200, _body
            status, thread = _chat(port, token, key, "main", "lead")
            assert thread["notice"] == WAITING
            assert thread["messages"][0]["state"] == WAITING
            assert main(["mail", "wait", "--timeout", "0"]) == 0
            status, thread = _chat(port, token, key, "main", "lead")
            assert thread["messages"][0]["state"] == "delivered"
            assert thread["notice"] == WAITING


def test_the_first_answer_is_the_one_both_show(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        board = foil_root(repo) / "fleets" / "main" / "board"
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            for text in ("keep", "drop"):
                status, body = _request(
                    port,
                    "POST",
                    "/v1/answer",
                    token=token,
                    headers=_origin(port),
                    body={"project": key, "fleet": "main", "id": "reading", "text": text},
                )
                assert status == 200, body
            files = _lead_mail(repo)
            assert len(files) == 1
            assert "keep" in files[0].read_text(encoding="utf-8")
            assert "drop" not in files[0].read_text(encoding="utf-8")
            status, thread = _chat(port, token, key, "main", "lead")
            card = next(item for item in thread["questions"] if item["id"] == "reading")
            assert card["state"] == "answered"
            assert card["answer"] == "keep"
            home = next(item for item in snapshot()["questions"] if item["id"] == "reading")
            assert home["answer"] == "keep"
            assert quoted_answer(board, "reading") == "keep"


def test_a_desk_answer_is_what_the_page_shows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        assert main(["answer", "reading", "keep"]) == 0
        capsys.readouterr()
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(
                port,
                "POST",
                "/v1/answer",
                token=token,
                headers=_origin(port),
                body={"project": key, "fleet": "main", "id": "reading", "text": "drop"},
            )
            assert status == 200, body
            assert len(_lead_mail(repo)) == 1
            status, thread = _chat(port, token, key, "main", "lead")
            card = next(item for item in thread["questions"] if item["id"] == "reading")
            assert card["answer"] == "keep"


def test_a_lead_reply_joins_the_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"))
        key = _key(repo)
        fleet = fleet_dir(repo, "main")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, _body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "lead", "text": "hello"}
            )
            assert status == 200
            folder = fleet / "board" / "mail" / "user"
            folder.mkdir(parents=True)
            (folder / "20990101T000000Z-lead-abcd1234.md").write_text(
                "---\ncontract: mail/v1\nfrom: lead\nto: user\ntime: 2099-01-01T00:00:00Z\n---\n\n"
                "heard\n",
                encoding="utf-8",
            )
            status, thread = _chat(port, token, key, "main", "lead")
            assert [(item["from"], item["state"]) for item in thread["messages"]] == [
                ("user", "queued"),
                ("lead", ""),
            ]
            assert thread["messages"][1]["body"] == "heard"


def test_a_symlinked_mailbox_is_not_the_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"), _seat("worker", window="@2"))
        key = _key(repo)
        fleet = fleet_dir(repo, "main")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, _body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "worker", "text": "real"}
            )
            assert status == 200
            mail = fleet / "board" / "mail"
            outside = repo.parent / "outside-mail"
            (outside / "worker").mkdir(parents=True)
            (outside / "worker" / "20990101T000000Z-user-abcd1234.md").write_text(
                "---\ncontract: mail/v1\nfrom: user\nto: worker\n"
                "time: 2099-01-01T00:00:00Z\n---\n\nplanted\n",
                encoding="utf-8",
            )
            mail.rename(fleet / "board" / "mail-real")
            mail.symlink_to(outside)
            status, thread = _chat(port, token, key, "main", "worker")
            assert status == 200
            assert thread["messages"] == []


def test_an_unknown_seat_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"))
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "missing", "text": "hi"}
            )
            assert status == 400
            assert "unknown seat" in body["error"]
            status, body = _post(
                port, token, {"project": key, "fleet": "main", "seat": "lead", "text": "  "}
            )
            assert status == 400
            assert body["error"] == "foil: message text is empty"


def test_the_seat_page_has_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _fleet(repo, _seat("lead", window="@1"), _seat("worker", window="@2"))
        key = _key(repo)
        model = page(f"projects/{key}/fleets/main/seats/worker")
        assert model is not None
        html = evidence_html(model, "token").decode()
        assert 'id="chat"' in html
        assert "seat.js" in html
        assert 'data-seat="worker"' in html
