"""Settings by asking and by screen. A tap is the write. A stale base is refused."""

from __future__ import annotations

import contextlib
import io
import json

import pytest
from foil_bridge.home import parse_card

from foil import api
from foil.bridge_cmd import read_desk_token
from foil.project import foil_root, store_root
from foil.roster_ops import roster_list_payload
from tests.test_bridge_home import _key, _master_mail, _short_repo
from tests.test_bridge_http import _origin, _request, _serving


def _list(project, fleet, caller, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()):
        return api.roster_list(project, fleet, caller, **kwargs)


def _update(project, fleet, caller, role, change, **kwargs) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        api.roster_update(project, fleet, caller, role, change, **kwargs)


def _harness(payload: dict, role: str) -> str:
    row = next(item for item in payload["templates"] if item["role"] == role)
    return ",".join(
        f"{entry['id']}/{entry['model']}" if entry["model"] else entry["id"]
        for entry in row["harness"]
    )


def _mapping() -> bytes:
    return (store_root() / "mapping.toml").read_bytes()


def test_the_screen_matches_roster_list(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _update(repo, "main", "user", "reviewer", "harness=gemini", project_layer=True)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            before = _mapping()
            status, body = _request(port, "GET", "/v1/settings", token=token)
            assert status == 200, body
            page = json.loads(body)
            listed = roster_list_payload(store_root())
            assert page["templates"] == listed["templates"]
            assert page["personas"] == listed["personas"]
            assert page["scope"] == "global"
            assert _harness(page, "reviewer") != "gemini"
            assert _mapping() == before
            key = _key(repo)
            status, body = _request(
                port, "GET", f"/v1/settings?project={key}", token=token
            )
            assert status == 200, body
            project = json.loads(body)
            assert project["templates"] == _list(repo, "main", "user")["templates"]
            assert project["personas"] == _list(repo, "main", "user")["personas"]
            assert _harness(project, "reviewer") == "gemini"
            files = {row["name"]: row for row in project["persona_files"]}
            for persona in project["personas"]:
                assert files[persona["name"]]["description"] == persona["description"]
                assert files[persona["name"]]["layer"] in {"packaged", "global", "project"}
            assert {row["layer"] for row in project["presets"]} <= {
                "packaged",
                "global",
                "project",
                "invalid",
            }
            status, body = _request(
                port, "GET", f"/v1/settings?project={key}&fleet=nope", token=token
            )
            assert status == 400, body
            assert "invalid fleet" in json.loads(body)["error"]
            status, _ = _request(port, "GET", "/settings")
            assert status == 401


def test_a_reorder_applies_on_the_tap_and_a_stale_write_shows_the_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _short_repo(monkeypatch) as repo:
        _update(repo, "main", "user", "reviewer", "harness=claude,codex")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/v1/settings", token=token)
            bases = json.loads(body)["bases"]
            order = {
                "role": "reviewer",
                "field": "harness",
                "value": "codex,claude",
                "base": bases["global"],
                "layer": "global",
                "before": "claude,codex",
            }
            held = _mapping()
            status, body = _request(port, "GET", "/v1/settings", token=token)
            assert _mapping() == held
            status, body = _request(
                port, "POST", "/v1/settings", token=token, headers=_origin(port), body=order
            )
            assert status == 200, body
            assert _harness(_list(repo, "main", "user"), "reviewer") == "codex,claude"
            stale = {**order, "value": "claude,codex", "before": "codex,claude"}
            status, body = _request(
                port, "POST", "/v1/settings", token=token, headers=_origin(port), body=stale
            )
            assert status == 400, body
            refused = json.loads(body)
            assert refused["error"].startswith("foil: roster changed, current revision ")
            assert refused["diff"]["before"] == "codex,claude"
            assert refused["diff"]["now"] == "codex,claude"
            assert refused["diff"]["after"] == "claude,codex"
            assert _harness(_list(repo, "main", "user"), "reviewer") == "codex,claude"


def test_a_fleet_reorder_stays_inside_that_fleet(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _update(repo, "main", "user", "reviewer", "harness=claude,codex")
        key = _key(repo)
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(
                port, "GET", f"/v1/settings?project={key}&fleet=main", token=token
            )
            assert status == 200, body
            page = json.loads(body)
            assert page["templates"] == _list(repo, "main", "lead", inside=True)["templates"]
            assert page["scope"] == "fleet"
            status, body = _request(
                port,
                "POST",
                "/v1/settings",
                token=token,
                headers=_origin(port),
                body={
                    "project": key,
                    "fleet": "main",
                    "role": "reviewer",
                    "field": "harness",
                    "value": "codex,claude",
                    "base": page["bases"]["fleet"],
                    "layer": "fleet",
                    "before": "claude,codex",
                },
            )
            assert status == 200, body
            assert _harness(_list(repo, "main", "user"), "reviewer") == "claude,codex"
            assert (
                _harness(_list(repo, "main", "lead", inside=True), "reviewer") == "codex,claude"
            )
            overrides = foil_root(repo) / "fleets" / "main" / "overrides.toml"
            assert overrides.is_file()


def test_a_bad_settings_write_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        _update(repo, "main", "user", "reviewer", "harness=claude,codex")
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/v1/settings", token=token)
            base = json.loads(body)["bases"]["global"]
            held = _mapping()
            missing = {
                "role": "reviewer",
                "field": "harness",
                "value": "codex,claude",
                "layer": "global",
            }
            sideways = {**missing, "base": base, "layer": "sideways"}
            extra = {**missing, "base": base, "extra": "no"}
            for payload in (missing, sideways, extra):
                status, body = _request(
                    port, "POST", "/v1/settings", token=token, headers=_origin(port), body=payload
                )
                assert status == 400, body
                assert json.loads(body)["error"] == "foil: settings change is invalid"
            assert _mapping() == held
            assert _harness(_list(repo, "main", "user"), "reviewer") == "claude,codex"


def test_a_settings_card_applies_only_on_the_tap(monkeypatch: pytest.MonkeyPatch) -> None:
    with _short_repo(monkeypatch) as repo:
        revision = _list(repo, "main", "user")["revision"]
        mail = _master_mail(
            "master",
            "card: settings\n"
            "role: lead\n"
            "field: permission\n"
            "value: ask\n"
            "before: auto\n"
            "after: ask\n"
            f"base: {revision}\n"
            "\n"
            "The lead should ask.\n",
        )
        later = _master_mail(
            "master",
            "card: settings\n"
            "role: lead\n"
            "field: permission\n"
            "value: auto\n"
            "before: ask\n"
            "after: auto\n"
            f"base: {revision}\n"
            "\n"
            "Too late.\n",
            stamp="20261122T120001Z",
        )
        ordinary = _master_mail(
            "master",
            "card: settings\nrole: lead\nfield: permission\nvalue: ask\nafter: auto\n\nNo.\n",
            stamp="20261122T120002Z",
        )
        with _serving(monkeypatch) as server:
            port = server.server_address[1]
            token = read_desk_token()
            status, body = _request(port, "GET", "/v1/home", token=token)
            home = json.loads(body)
            card = next(item["card"] for item in home["messages"] if item["id"] == mail.name)
            assert {"key": "before", "value": "auto"} in card["fields"]
            assert {"key": "after", "value": "ask"} in card["fields"]
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "auto"
            status, body = _request(
                port,
                "POST",
                "/v1/act",
                token=token,
                headers=_origin(port),
                body={"id": mail.name},
            )
            assert status == 200, body
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "ask"
            status, body = _request(
                port,
                "POST",
                "/v1/act",
                token=token,
                headers=_origin(port),
                body={"id": later.name},
            )
            assert status == 400, body
            refused = json.loads(body)
            assert refused["error"].startswith("foil: roster changed, current revision ")
            assert refused["diff"]["now"] == "ask"
            assert refused["diff"]["after"] == "auto"
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "ask"
            status, body = _request(port, "GET", "/v1/home", token=token)
            home = json.loads(body)
            skipped = next(item for item in home["messages"] if item["id"] == ordinary.name)
            assert skipped["card"] is None


def test_a_card_whose_after_differs_from_value_is_prose() -> None:
    parsed, prose = parse_card(
        "card: settings\nrole: lead\nfield: permission\nvalue: ask\nafter: auto\n\nNo.\n"
    )
    assert parsed is None
    assert prose.startswith("card: settings")
    parsed, _prose = parse_card(
        "card: settings\nrole: lead\nfield: permission\nvalue: ask\nbase: abcd\n\nNo.\n"
    )
    assert parsed is None
