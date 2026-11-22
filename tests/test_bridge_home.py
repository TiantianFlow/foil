"""The home reads the master's mail. A tap is what calls foil.api."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from foil_bridge.home import snapshot

from foil import api
from foil.bridge_cmd import read_desk_token
from foil.cli import main
from foil.master import deliver, directory, empty_record, renew, save_record
from foil.project import foil_root, store_root
from tests.test_answer import _STATUS, _lead_mail, _question
from tests.test_bridge_http import _origin, _request, _serving
from tests.test_mail_board_ops import _init_git_repository


@contextlib.contextmanager
def _short_repo(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A checkout whose store key starts with a letter.

    A long temporary path can truncate to a key ``checkout_for_key`` refuses.
    """

    root = Path(tempfile.mkdtemp(prefix="foil"))
    try:
        repo = root / "p"
        repo.mkdir()
        _init_git_repository(repo)
        monkeypatch.chdir(repo)
        with contextlib.redirect_stdout(io.StringIO()):
            assert main(["init"]) == 0
        yield repo
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _master_mail(sender: str, body: str, stamp: str = "20261122T120000Z") -> Path:
    folder = directory() / "mail"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-{sender}-0123abcd.md"
    text = (
        "---\n"
        "contract: mail/v1\n"
        f"from: {sender}\n"
        "to: master\n"
        "time: 2026-11-22T12:00:00Z\n"
        "---\n"
        "\n"
        f"{body}"
    )
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return path


def _key(repo: Path) -> str:
    return foil_root(repo).name


def test_history_survives_renew(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    deliver("first note", None)
    deliver("second note", None)
    before = [item["body"].strip() for item in snapshot()["messages"]]
    renew(None)
    after = [item["body"].strip() for item in snapshot()["messages"]]
    assert sorted(before) == ["first note", "second note"]
    assert after == before
    assert len(list((directory() / "mail").glob("*.md"))) == 2


def test_question_card_waits_until_the_lead_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/v1/home", token=token)
            assert status == 200, body
            cards = [item for item in json.loads(body)["questions"] if item["id"] == "reading"]
            assert cards[0]["state"] == "open"
            assert cards[0]["options"] == ["keep", "drop"]
            assert all(item["id"] != "theirs" for item in json.loads(body)["questions"])
            status, body = _request(
                port,
                "POST",
                "/v1/answer",
                token=token,
                headers=_origin(port),
                body={"project": key, "fleet": "main", "id": "reading", "text": "keep"},
            )
            assert status == 200, body
            mail = _lead_mail(repo)[0].read_text(encoding="utf-8")
            assert mail.split("---", 2)[2].lstrip("\n").startswith("question: reading\n")
            status, body = _request(port, "GET", "/v1/home", token=token)
            card = next(item for item in json.loads(body)["questions"] if item["id"] == "reading")
            assert card["state"] == "answered"
            assert card["answer"] == "keep"
            board = foil_root(repo) / "fleets" / "main" / "board" / "status.md"
            board.write_text(_STATUS.replace("id: reading", "id: gone"), encoding="utf-8")
            status, body = _request(port, "GET", "/v1/home", token=token)
            assert all(item["id"] != "reading" for item in json.loads(body)["questions"])


def test_a_message_sent_mid_turn_is_queued(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    record = empty_record()
    record["turn_started"] = 1
    record["pgid"] = 1
    root = directory()
    root.mkdir(parents=True)
    save_record(root, record)
    _master_mail("user", "while you work\n", stamp="20261122T120100Z")
    message = snapshot()["messages"][0]
    assert message["queued"] == "the next turn"
    assert snapshot()["working"]["running"] is False

    record["pgid"] = os.getpid()
    save_record(root, record)
    view = snapshot()
    assert view["working"]["running"] is True
    assert view["messages"][0]["queued"] == "a running turn"


def test_action_card_runs_only_on_the_tap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    _init_git_repository(other)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _master_mail(
            "master",
            "card: settings\n"
            "role: lead\n"
            "field: permission\n"
            "value: ask\n"
            "\n"
            "The lead should ask.\n",
        )
        _master_mail(
            "master",
            f"card: init\ndir: {other}\n\nInit the other checkout.\n",
            stamp="20261122T120001Z",
        )
        _master_mail(
            "master",
            f"card: merge\nproject: {key}\nfleet: main\n\nMerge when it is done.\n",
            stamp="20261122T120002Z",
        )
        assert api.roster_show(repo, "main", "user", "lead")["permission"] == "auto"
        assert not (store_root() / "projects" / foil_root(other).name / "project.json").is_file()
        calls: list[tuple] = []

        def _spawn(*args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True}

        monkeypatch.setattr("foil_bridge.home.seat_spawn", _spawn)
        fleet_mail = _master_mail(
            "master",
            f"card: fleet\nproject: {key}\nfleet: side\n\nA second fleet.\n",
            stamp="20261122T120003Z",
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/v1/home", token=token)
            cards = [
                item["card"]["action"] for item in json.loads(body)["messages"] if item["card"]
            ]
            assert cards == ["settings", "init", "merge", "fleet"]
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "auto"
            assert calls == []
            settings = "20261122T120000Z-master-0123abcd.md"
            status, body = _request(
                port, "POST", "/v1/act", token=token, headers=_origin(port), body={"id": settings}
            )
            assert status == 200, body
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "ask"
            status, body = _request(
                port,
                "POST",
                "/v1/act",
                token=token,
                headers=_origin(port),
                body={"id": "20261122T120001Z-master-0123abcd.md"},
            )
            assert status == 200, body
            assert (store_root() / "projects" / foil_root(other).name / "project.json").is_file()
            status, body = _request(
                port,
                "POST",
                "/v1/act",
                token=token,
                headers=_origin(port),
                body={"id": "20261122T120002Z-master-0123abcd.md"},
            )
            assert status == 400
            assert json.loads(body)["error"] == "foil: fleet merge is not a command"
            status, body = _request(
                port,
                "POST",
                "/v1/act",
                token=token,
                headers=_origin(port),
                body={"id": fleet_mail.name},
            )
            assert status == 200, body
            assert calls and calls[0][0][2] == "user"
            assert calls[0][0][1] == "side"
            assert calls[0][0][3] == "lead"


def test_a_link_target_resolves_and_a_missing_one_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        target = f"projects/{key}/fleets/main/board/status.md"
        board = foil_root(repo) / "fleets" / "main" / "board"
        (board / "status.md").write_text("status\n", encoding="utf-8")
        _master_mail("master", f"See {target}. and {target}.missing for the note.\n")
        view = snapshot()
        links = view["messages"][0]["links"]
        assert [item["target"] for item in links] == [target]
        assert links[0]["kind"] == "file"
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/evidence?target=" + target, token=token)
            assert status == 200
            assert b"A file in this fleet." in body
            assert key.encode() in body
            assert b"status.md" in body
            status, body = _request(
                port, "GET", "/evidence?target=" + target + ".missing", token=token
            )
            assert status == 404 and body == b""


def test_the_pane_tail_is_display_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = directory()
    root.mkdir(parents=True)
    record = empty_record()
    record["turn_started"] = 1
    record["pgid"] = os.getpid()
    save_record(root, record)
    window = root / "run" / "window.json"
    window.write_text(json.dumps({"schema_version": 1, "window_id": "@1"}), encoding="utf-8")

    def _capture(_self, window_id: str, lines: int) -> str:
        assert window_id == "@1"
        assert lines == 40
        return "still working\n"

    monkeypatch.setattr(
        "foil.master.TmuxController.matches_window", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr("foil.master.TmuxController.capture_pane", _capture)
    view = snapshot()
    assert view["working"] == {"running": True, "tail": "still working\n"}
    monkeypatch.setattr(
        "foil.master.TmuxController.matches_window", lambda *_args, **_kwargs: False
    )
    assert snapshot()["working"]["tail"] == ""
