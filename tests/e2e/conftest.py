"""Put the fake harness, and the Foil under test, on PATH, and record each live run."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.harness.live_record import record
from tests.harness.shims import install_shims

_FAKE = Path(__file__).resolve().parents[1] / "harness" / "foil_fake.py"


@pytest.fixture(autouse=True)
def _foil_fake_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    bindir = tmp_path_factory.mktemp("foil-fake")
    binary = bindir / "foil-fake"
    binary.symlink_to(_FAKE)
    install_shims(bindir)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), os.environ.get("PATH", ""))))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """A live test that ran leaves a row in the record, whether it passed or failed."""

    outcome = yield
    report = outcome.get_result()
    scratch = getattr(item, "funcargs", {}).get("tmp_path")
    if report.when != "call" or scratch is None or item.get_closest_marker("e2e_live") is None:
        return
    try:
        record(scratch / "project", item.name, report.passed)
    except Exception as exc:
        report.sections.append(("live record", f"not written: {exc}"))
