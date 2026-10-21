"""`foil view` in a real browser: the page's own script, which the HTML tests do not run.

It needs Chromium for Playwright:
`uv run --frozen --extra dev playwright install --only-shell chromium`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from foil.project import foil_root
from tests.test_slice_b import _repo
from tests.test_view import _registry, _seat, _serving, _windows


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch()
        yield launched
        launched.close()


def _open(browser: Browser, port: int, repo: Path, path: str) -> tuple[Page, list[str]]:
    path = f"/projects/{foil_root(repo).name}{path}"
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
        page, errors = _open(browser, port, repo, "/settings")
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
        page, errors = _open(browser, port, repo, "/fleets/main")
        button = page.locator("button[data-copy]")
        button.click()
        expect(button).to_have_text("Copied")
        copied = page.evaluate("navigator.clipboard.readText()")
        page.context.close()
    assert copied == "tmux attach -t '=foil-demo-1'"
    assert errors == []


def test_an_info_popup_in_a_table_stays_inside_a_phone_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    with _serving(repo) as port:
        page, errors = _open(browser, port, repo, "/settings")
        page.set_viewport_size({"width": 390, "height": 844})
        tip = page.locator("th details.tip").filter(
            has=page.locator('summary[aria-label="About Worktree"]')
        )
        tip.locator("summary").click()
        # The page places the popup on the toggle event, which runs after the click.
        expect(tip.locator("p")).to_have_attribute("style", re.compile("left"))
        icon = tip.locator("summary").bounding_box()
        box = tip.locator("p").bounding_box()
        page.context.close()
    assert icon is not None and box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390
    assert box["y"] >= icon["y"] + icon["height"]
    assert errors == []


def test_on_a_phone_every_seat_shows_its_state_without_sideways_scrolling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    seats = [_seat("lead", window="@1")] + [
        _seat(f"implementer-{n}", window=f"@{n + 1}", branch=f"foil/implementer-{n}")
        for n in range(1, 5)
    ]
    _registry(repo, *seats)
    _windows(monkeypatch, {"@1": "lead", "@3": "implementer-2"})
    with _serving(repo) as port:
        page, errors = _open(browser, port, repo, "/fleets/main")
        page.set_viewport_size({"width": 390, "height": 844})
        width = page.evaluate("document.documentElement.scrollWidth")
        states = [cell.bounding_box() for cell in page.locator(".agents .dot").all()]
        page.context.close()
    assert width <= 390
    assert len(states) == 5
    assert all(box is not None and box["x"] + box["width"] <= 390 for box in states)
    assert errors == []


def test_opening_a_board_file_shows_it_at_the_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: Browser
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    board = foil_root(repo) / "fleets" / "main" / "board"
    (board / "status.md").write_text("status\n" * 60, encoding="utf-8")
    for n in range(12):
        (board / "tasks" / f"t{n:02}.md").write_text(f"brief {n}\n", encoding="utf-8")
    with _serving(repo) as port:
        page, errors = _open(browser, port, repo, "/fleets/main")
        link = page.locator('#board a[href$="?open=tasks/t11.md"]')
        link.scroll_into_view_if_needed()
        link.click()
        page.wait_for_load_state()
        text = page.locator("pre").first
        expect(text).to_have_text("brief 11\n")
        top = text.bounding_box()
        page.locator("text=Back to fleet").click()
        page.wait_for_load_state()
        back = page.evaluate("location.hash")
        page.context.close()
    assert top is not None and top["y"] < 300
    assert back == "#board"
    assert errors == []
