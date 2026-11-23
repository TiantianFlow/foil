"""The desk page in a browser: show a code, pair once, revoke.

It needs Chromium for Playwright:
`uv run --frozen --extra dev playwright install --only-shell chromium`.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator

import pytest
from foil_bridge.server import open_server
from playwright.sync_api import Browser, expect, sync_playwright

from foil import api
from foil.bridge_cmd import read_desk_token
from tests.test_answer import _lead_mail, _question
from tests.test_bridge_home import _master_mail, _short_repo


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
            expect(page.get_by_text("Which reading?")).to_be_visible()
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
