"""The desk page in a browser: show a code, pair once, revoke.

It needs Chromium for Playwright:
`uv run --frozen --extra dev playwright install --only-shell chromium`.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from urllib.parse import urlencode

import pytest
from foil_bridge.server import open_server
from playwright.sync_api import Browser, expect, sync_playwright

from foil import api
from foil.bridge_cmd import read_desk_token
from foil.delivery import fleet_paused, pending_pointers
from foil.lifecycle import TmuxController
from foil.store import fleet_dir
from tests.test_answer import _lead_mail, _question
from tests.test_bridge_evidence import _registry, _seat
from tests.test_bridge_home import _goal_ahead, _key, _master_mail, _short_repo
from tests.test_bridge_settings import _harness, _list, _update


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def test_pair_once_and_revoke(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    server = open_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    token = read_desk_token()
    assert token is not None
    origin = f"http://127.0.0.1:{port}"
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    try:
        page.goto(f"{origin}/desk?token={token}")
        page.get_by_role("button", name="Show a code").click()
        code = page.locator("#code")
        expect(code).to_be_visible()
        expect(code).to_have_text(re.compile(r"^[2-9A-HJ-NP-Z]{8}$"))
        spent = code.inner_text()
        response = page.request.post(
            f"{origin}/v1/pair",
            data=json.dumps({"code": spent}),
            headers={"Origin": origin, "Content-Type": "application/json"},
        )
        assert response.ok
        device = response.json()["id"]
        page.reload()
        expect(page.locator("#devices")).to_contain_text(device)
        page.get_by_role("button", name="Revoke").click()
        expect(page.locator("#devices")).to_have_text("None yet.")
        offline = context.new_page()
        offline.goto(f"{origin}/offline?token={token}")
        expect(offline.locator("#offline")).to_have_text(
            "This machine can't be reached. Check Tailscale."
        )
        ready = page.evaluate(
            """() => Promise.race([
              navigator.serviceWorker.ready.then((reg) => Boolean(reg.active)),
              new Promise((resolve) => setTimeout(() => resolve(false), 5000)),
            ])"""
        )
        assert ready is True
        offline.close()
    finally:
        context.close()
        server.stopped.set()
        server.watch.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert errors == []


def test_home_answers_on_a_tap_and_leaves_a_card_until_then(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        _master_mail(
            "master",
            "card: settings\nrole: lead\nfield: permission\nvalue: ask\n\nThe lead should ask.\n",
        )
        assert api.roster_show(repo, "main", "user", "lead")["permission"] == "auto"
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(f"{origin}/?token={token}")
            expect(page.get_by_text("Which reading?", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Apply this setting")).to_be_visible()
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "auto"
            page.get_by_role("button", name="keep", exact=True).click()
            expect(page.get_by_text("answered, waiting for lead")).to_be_visible()
            mail = _lead_mail(repo)[0].read_text(encoding="utf-8")
            assert mail.split("---", 2)[2].lstrip("\n").startswith("question: reading\n")
            page.get_by_role("button", name="Apply this setting").click()
            expect(page.get_by_role("button", name="Done.")).to_be_visible()
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "ask"
            ready = page.evaluate(
                """() => Promise.race([
                  navigator.serviceWorker.ready.then((reg) => Boolean(reg.active)),
                  new Promise((resolve) => setTimeout(() => resolve(false), 5000)),
                ])"""
            )
            assert ready is True
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_pause_is_one_tap_and_kill_asks(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    monkeypatch.setattr(TmuxController, "matches_window", lambda *args, **kwargs: False)
    killed: list = []

    def _kill(*args, **kwargs):
        killed.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr("foil_bridge.controls.seat_kill", _kill)
    with _short_repo(monkeypatch) as repo:
        key = _key(repo)
        board = fleet_dir(repo, "main") / "board"
        board.mkdir(parents=True, exist_ok=True)
        (board / "status.md").write_text(
            "## Mandate\n\nMay decide: pause.\n", encoding="utf-8"
        )
        _registry(repo, "main", {"lead": _seat("lead", "")})
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            fleet = urlencode({"target": f"projects/{key}/fleets/main", "token": token})
            page.goto(f"{origin}/evidence?{fleet}")
            page.get_by_role("button", name="Pause").click()
            expect(page.get_by_role("button", name="Resume fleet")).to_be_visible()
            assert fleet_paused(fleet_dir(repo, "main")) is True
            seat = urlencode(
                {"target": f"projects/{key}/fleets/main/seats/lead", "token": token}
            )
            page.goto(f"{origin}/evidence?{seat}")
            asked: list[str] = []

            def _ask(dialog) -> None:
                asked.append(dialog.message)
                dialog.dismiss()

            page.on("dialog", _ask)
            page.get_by_role("button", name="Kill").click()
            assert asked == ["Kill?"]
            assert killed == []
            page.remove_listener("dialog", _ask)

            def _allow(dialog) -> None:
                asked.append(dialog.message)
                dialog.accept()

            page.on("dialog", _allow)
            with page.expect_response(f"{origin}/v1/control") as completed:
                page.get_by_role("button", name="Kill").click()
            assert completed.value.ok
            assert asked == ["Kill?", "Kill?"]
            assert killed and killed[0][0][2] == "user"
            assert killed[0][1]["name"] == "lead"
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_a_dragged_harness_order_applies_on_the_tap(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        _update(repo, "main", "user", "reviewer", "harness=claude,codex")
        key = _key(repo)
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(f"{origin}/settings?project={key}&token={token}")
            items = page.locator("ol[data-role=reviewer] li")
            expect(items).to_have_count(2)
            expect(items.nth(0)).to_contain_text("claude")
            apply = page.get_by_role("button", name="Apply this order")
            items.nth(1).drag_to(items.nth(0))
            apply.click()
            expect(apply).to_be_hidden()
            assert _harness(_list(repo, "main", "user"), "reviewer") == "codex,claude"
            _update(repo, "main", "user", "reviewer", "harness=agy,claude")
            items.nth(1).drag_to(items.nth(0))
            apply.click()
            expect(page.get_by_text("now: agy,claude")).to_be_visible()
            assert _harness(_list(repo, "main", "user"), "reviewer") == "agy,claude"
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_a_stale_settings_card_shows_the_diff_on_the_home(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        revision = _list(repo, "main", "user")["revision"]
        _update(repo, "main", "user", "lead", "permission=ask")
        _master_mail(
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
        )
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(f"{origin}/?token={token}")
            page.get_by_role("button", name="Apply this setting").click()
            expect(page.get_by_text("now: ask")).to_be_visible()
            assert api.roster_show(repo, "main", "user", "lead")["permission"] == "ask"
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_selecting_lines_sends_them_to_the_owner(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    task = (
        "---\n"
        "contract: task/v1\n"
        "id: login\n"
        "owner: implementer-1\n"
        "state: open\n"
        "acceptance: the test passes\n"
        "---\n"
        "\n"
        "alpha\n"
        "beta\n"
        "gamma\n"
    )
    with _short_repo(monkeypatch) as repo:
        _registry(
            repo,
            "main",
            {"lead": _seat("lead", "main"), "implementer-1": _seat("implementer-1", "")},
        )
        board = fleet_dir(repo) / "board"
        (board / "tasks").mkdir(parents=True, exist_ok=True)
        (board / "tasks" / "login.md").write_text(task, encoding="utf-8")
        key = _key(repo)
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            query = urlencode(
                {"target": f"projects/{key}/fleets/main/board/tasks/login.md", "token": token}
            )
            page.goto(f"{origin}/evidence?{query}")
            lines = page.locator("ol.pick li")
            expect(lines).to_have_count(11)
            lines.nth(9).click()
            lines.nth(10).click(modifiers=["Shift"])
            page.locator("textarea").fill("Fix the check.")
            page.get_by_role("button", name="Send").click()
            expect(page.locator("[data-note]")).to_have_text("Sent to implementer-1")
            mailed = (board / "mail" / "implementer-1").glob("*.md")
            body = next(iter(mailed)).read_text(encoding="utf-8")
            assert "path: tasks/login.md" in body
            assert "lines: 10-11" in body
            assert "10|beta" in body
            assert [item["to"] for item in pending_pointers(fleet_dir(repo))] == ["implementer-1"]
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_home_shows_the_next_step_and_remove_clears_a_watch(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    from foil.master import directory

    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch):
        root = directory()
        root.mkdir(parents=True)
        (root / "log.md").write_text(
            "## demo main\n### Next\n- record the choice\n", encoding="utf-8"
        )
        (root / "watches.md").write_text(
            "- ci 2026-11-23T12:00:00Z the review\n", encoding="utf-8"
        )
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        token = read_desk_token()
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(f"{origin}/?token={token}")
            expect(page.locator("#plan")).to_contain_text("record the choice")
            expect(page.locator("#watches")).to_contain_text("the review")
            page.get_by_role("button", name="Remove").click()
            expect(page.locator("#watches")).to_contain_text("None.")
            assert "ci" not in (root / "watches.md").read_text(encoding="utf-8")
            nudge = page.locator("#modes p").filter(has_text="nudge")
            expect(nudge).to_contain_text("observe")
            nudge.get_by_role("button", name="Act").click()
            expect(page.locator("#modes p").filter(has_text="nudge")).to_contain_text("act")
            assert "- nudge\n" in (root / "mandate.md").read_text(encoding="utf-8")
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []


def test_the_phone_posts_answers_and_merges_by_tapping(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    """Steps 1, 5, and 7 of the phone path, tapped on the page."""

    monkeypatch.setattr("foil_bridge.tailnet.tailnet", lambda: None)
    with _short_repo(monkeypatch) as repo:
        _question(repo)
        _goal_ahead(repo)
        key = _key(repo)
        _master_mail(
            "master",
            f"card: merge\nproject: {key}\nfleet: main\n\nMerge when it is done.\n",
            stamp="20261122T120001Z",
        )
        assert not (repo / "from-goal.txt").exists()
        server = open_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        desk = read_desk_token()
        assert desk is not None
        origin = f"http://127.0.0.1:{port}"
        context = browser.new_context()
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        try:
            page.goto(f"{origin}/desk?{urlencode({'token': desk})}")
            page.get_by_role("button", name="Show a code").click()
            code = page.locator("#code")
            expect(code).to_be_visible()
            spent = code.inner_text()
            paired = page.request.post(
                f"{origin}/v1/pair",
                data=json.dumps({"code": spent}),
                headers={"Origin": origin, "Content-Type": "application/json"},
            )
            assert paired.ok
            token = paired.json()["token"]
            page.goto(f"{origin}/?{urlencode({'token': token})}")
            expect(page.get_by_text("Which reading?", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Merge this fleet")).to_be_visible()
            message = page.locator("#message")
            message.fill("fix the calc")
            page.locator("#say").get_by_role("button", name="Send").click()
            expect(page.get_by_text("fix the calc", exact=True)).to_be_visible()
            assert _lead_mail(repo) == []
            page.get_by_role("button", name="keep", exact=True).click()
            expect(page.get_by_text("answered, waiting for lead")).to_be_visible()
            mail = _lead_mail(repo)[0].read_text(encoding="utf-8")
            assert mail.split("---", 2)[2].lstrip("\n").startswith("question: reading\n")
            page.get_by_role("button", name="Merge this fleet").click()
            expect(page.get_by_role("button", name="Done.", exact=True)).to_be_visible()
            assert (repo / "from-goal.txt").read_text(encoding="utf-8") == "from the goal\n"
        finally:
            context.close()
            server.stopped.set()
            server.watch.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert errors == []
