"""`foil view` in a real browser: the page's own script, which the HTML tests do not run.

It needs Chromium for Playwright:
`uv run --frozen --extra dev playwright install --only-shell chromium`.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.test_slice_b import _repo
from tests.test_view import _registry, _seat, _serving, _windows


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def _open(browser: Browser, port: int, path: str) -> tuple[Page, list[str]]:
    origin = f"http://127.0.0.1:{port}"
    context = browser.new_context()
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda message: message.type == "error" and errors.append(message.text))
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(origin + path)
    return page, errors


def _open_tips(page: Page) -> list[bool]:
    return page.eval_on_selector_all("details.tip", "tips => tips.map(tip => tip.open)")


def test_an_info_popup_closes_on_a_click_elsewhere_or_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port:
        page, errors = _open(browser, port, "/settings")
        icons = page.locator("details.tip summary")
        assert icons.count() >= 2
        closed = [False] * icons.count()
        icons.nth(0).click()
        assert _open_tips(page) == [True, *closed[1:]]
        page.locator("details.tip p").nth(0).click()
        assert _open_tips(page) == [True, *closed[1:]]
        page.locator("h1").click()
        assert _open_tips(page) == closed
        icons.nth(0).click()
        icons.nth(1).click()
        assert _open_tips(page) == [False, True, *closed[2:]]
        page.keyboard.press("Escape")
        assert _open_tips(page) == closed
        icons.nth(1).click()
        icons.nth(1).click()
        assert _open_tips(page) == closed
        page.context.close()
    assert errors == []


def test_copy_puts_the_attach_command_on_the_clipboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _registry(repo, _seat("lead", window="@4"))
    _windows(monkeypatch, {"@4": "lead"})
    with _serving(repo) as port:
        page, errors = _open(browser, port, "/fleets/main")
        button = page.locator("button[data-copy]")
        button.click()
        expect(button).to_have_text("Copied")
        copied = page.evaluate("navigator.clipboard.readText()")
        page.context.close()
    assert copied == "tmux attach -t '=foil-demo-1'"
    assert errors == []
