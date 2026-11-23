"""Fleet and seat controls. A tap calls foil.api as user. Kill and archive ask."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from foil_bridge.controls import next_roles

from foil.delivery import fleet_paused
from foil.lifecycle import TmuxController
from foil.store import fleet_dir
from tests.test_bridge_evidence import _registry, _seat
from tests.test_bridge_home import _key, _master_mail, _short_repo
from tests.test_bridge_http import _origin, _request, _serving

_COVERS = (
    "## Mandate\n\n"
    "May decide: pause, resume, renew, fleet, and kill.\n"
    "Must stop for: spawn.\n"
)


def _mandate(repo: Path, fleet: str, text: str) -> None:
    board = fleet_dir(repo, fleet) / "board"
    board.mkdir(parents=True, exist_ok=True)
    (board / "status.md").write_text(text, encoding="utf-8")


def _post(port: int, token: str, body: dict) -> tuple[int, bytes]:
    return _request(
        port, "POST", "/v1/control", token=token, headers=_origin(port), body=body
    )


def _act(port: int, token: str, body: dict) -> tuple[int, bytes]:
    return _request(port, "POST", "/v1/act", token=token, headers=_origin(port), body=body)


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list:
    calls: list = []

    def _fake(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(f"foil_bridge.controls.{name}", _fake)
    return calls


def _error(body: bytes) -> str:
    return json.loads(body)["error"]


def test_fleet_page_shows_the_next_spawn_and_pause_runs_as_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _mandate(repo, "main", _COVERS)
        roles = next_roles(repo, "main")
        assert roles and roles[0]["note"] == ""
        shown = roles[0]["model"] or "harness default"
        pair = f"{roles[0]['harness']} / {shown}"
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, page = _request(
                port, "GET", f"/evidence?target=projects/{key}/fleets/main", token=token
            )
            assert status == 200
            html = page.decode()
            assert 'data-op="pause"' in html
            assert 'data-op="pause" data-confirm' not in html
            assert pair in html
            assert "Start a new goal" in html
            assert 'data-op="fleet"' in html
            assert "data-confirm" in html
            body = {"op": "pause", "project": key, "fleet": "main"}
            status, payload = _post(port, token, body)
            assert status == 200, payload
            state = json.loads(
                (fleet_dir(repo, "main") / "run" / "fleet.json").read_text(encoding="utf-8")
            )
            assert state == {"paused": True, "told": False}
            assert fleet_paused(fleet_dir(repo, "main")) is True
            status, page = _request(
                port, "GET", f"/evidence?target=projects/{key}/fleets/main", token=token
            )
            assert status == 200
            again = page.decode()
            assert 'data-op="resume-fleet"' in again
            assert 'data-op="resume-fleet" data-confirm' not in again
            assert 'data-op="pause"' not in again
            status, payload = _post(
                port, token, {"op": "resume-fleet", "project": key, "fleet": "main"}
            )
            assert status == 200, payload
            assert fleet_paused(fleet_dir(repo, "main")) is False


def test_spawn_and_a_new_goal_ask_when_the_mandate_does_not_cover_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = _spy(monkeypatch, "seat_spawn")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _mandate(repo, "main", _COVERS)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, payload = _post(
                port,
                token,
                {"op": "spawn", "project": key, "fleet": "main", "role": "implementer"},
            )
            assert status == 400 and _error(payload) == "foil: confirm this"
            assert spawned == []
            status, payload = _post(
                port,
                token,
                {
                    "op": "spawn",
                    "project": key,
                    "fleet": "main",
                    "role": "implementer",
                    "confirm": True,
                },
            )
            assert status == 200, payload
            assert spawned[0][0][1:] == ("main", "user", "implementer")
            status, payload = _post(
                port, token, {"op": "fleet", "project": key, "fleet": "side"}
            )
            assert status == 400 and _error(payload) == "foil: confirm this"
            assert len(spawned) == 1
            status, payload = _post(
                port,
                token,
                {"op": "fleet", "project": key, "fleet": "side", "confirm": True},
            )
            assert status == 200, payload
            assert spawned[1][0][1:] == ("side", "user", "lead")


def test_kill_and_archive_on_the_seat_need_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args, **kwargs: False)
    killed = _spy(monkeypatch, "seat_kill")
    archived = _spy(monkeypatch, "seat_archive")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _mandate(repo, "main", _COVERS)
        _registry(repo, "main", {"lead": _seat("lead", "")})
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, page = _request(
                port,
                "GET",
                f"/evidence?target=projects/{key}/fleets/main/seats/lead",
                token=token,
            )
            assert status == 200
            html = page.decode()
            assert 'data-op="resume" data-seat="lead"' in html
            assert 'data-op="resume" data-seat="lead" data-confirm' not in html
            assert 'data-op="renew" data-seat="lead" data-confirm' not in html
            assert 'data-op="kill" data-seat="lead" data-confirm="yes"' in html
            assert 'data-op="archive" data-seat="lead" data-confirm="yes"' in html
            for op, calls in (("kill", killed), ("archive", archived)):
                body = {"op": op, "project": key, "fleet": "main", "seat": "lead"}
                status, payload = _post(port, token, body)
                assert status == 400 and _error(payload) == "foil: confirm this"
                assert calls == []
                status, payload = _post(port, token, {**body, "confirm": True})
                assert status == 200, payload
            assert killed[0][0][:3] == (repo.resolve(), "main", "user")
            assert killed[0][1]["name"] == "lead"
            assert archived[0][0][:3] == (repo.resolve(), "main", "user")
            assert archived[0][0][3] == "lead"


def test_kill_and_archive_cards_are_never_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    killed = _spy(monkeypatch, "seat_kill")
    archived = _spy(monkeypatch, "seat_archive")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        kill = _master_mail(
            "master",
            f"card: kill\nproject: {key}\nfleet: main\nseat: lead\n\nStop the lead.\n",
        )
        archive = _master_mail(
            "master",
            f"card: archive\nproject: {key}\nfleet: main\nseat: lead\n\nFile the lead.\n",
            stamp="20261122T120001Z",
        )
        _master_mail(
            "lead",
            f"card: kill\nproject: {key}\nfleet: main\nseat: lead\n\nNot a card.\n",
            stamp="20261122T120002Z",
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, payload = _request(port, "GET", "/v1/home", token=token)
            assert status == 200
            cards = {
                item["card"]["action"]: item["card"]
                for item in json.loads(payload)["messages"]
                if item["card"]
            }
            assert set(cards) == {"kill", "archive"}
            assert cards["kill"]["route"] == "control" and cards["kill"]["confirm"] is True
            assert cards["archive"]["route"] == "control"
            for name, calls in ((kill.name, killed), (archive.name, archived)):
                for body in ({"id": name}, {"id": name, "confirm": True}):
                    status, payload = _act(port, token, body)
                    assert status == 400 and _error(payload) == "foil: confirm this"
                    assert calls == []
                status, payload = _post(port, token, {"id": name})
                assert status == 400 and _error(payload) == "foil: confirm this"
                assert calls == []
                status, payload = _post(port, token, {"id": name, "confirm": True})
                assert status == 200, payload
            assert killed[0][0][2] == "user" and killed[0][1]["name"] == "lead"
            assert archived[0][0][2] == "user" and archived[0][0][3] == "lead"


def test_a_covered_spawn_card_runs_without_another_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = _spy(monkeypatch, "seat_spawn")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        _mandate(repo, "main", "## Mandate\n\nMay decide: spawn.\n")
        mail = _master_mail(
            "master",
            f"card: spawn\nproject: {key}\nfleet: main\nrole: implementer\n\nOne more.\n",
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, payload = _request(port, "GET", "/v1/home", token=token)
            card = json.loads(payload)["messages"][0]["card"]
            assert card["route"] == "act" and card["confirm"] is False
            status, payload = _act(port, token, {"id": mail.name})
            assert status == 200, payload
            assert spawned[0][0][1:] == ("main", "user", "implementer")


def test_a_symlink_or_a_stopped_word_covers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumed = _spy(monkeypatch, "fleet_resume")
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        text = "## Mandate\n\nMay decide: pause and resume.\nMust stop for: resume.\n"
        _mandate(repo, "main", text)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_token()
            status, payload = _post(
                port, token, {"op": "resume-fleet", "project": key, "fleet": "main"}
            )
            assert status == 400 and _error(payload) == "foil: confirm this"
            assert resumed == []
            extra = {"op": "pause", "project": key, "fleet": "main", "extra": 1}
            status, payload = _post(port, token, extra)
            assert status == 400 and _error(payload) == "foil: control is invalid"
            assert fleet_paused(fleet_dir(repo, "main")) is False
            path = fleet_dir(repo, "main") / "board" / "status.md"
            path.unlink()
            path.symlink_to(path.with_name("missing"))
            status, payload = _post(port, token, {"op": "pause", "project": key, "fleet": "main"})
            assert status == 400 and _error(payload) == "foil: confirm this"
            assert fleet_paused(fleet_dir(repo, "main")) is False


def read_token() -> str:
    from foil.bridge_cmd import read_desk_token

    token = read_desk_token()
    assert token is not None
    return token
