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

from foil.bridge_cmd import read_desk_token


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def test_pair_once_and_revoke(
    monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    monkeypatch.setattr("foil.view.tailnet", lambda: None)
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
        page.goto(f"{origin}/?token={token}")
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
