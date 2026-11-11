"""Put the fake harness, and the Foil under test, on PATH, and record each live run."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.harness.live_record import record
from tests.harness.shims import install_shims

_FAKE = Path(__file__).resolve().parents[1] / "harness" / "foil_fake.py"
STAND_INS = ("claude", "codex", "gemini", "opencode", "grok")


@pytest.fixture(autouse=True)
def _foil_fake_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    bindir = tmp_path_factory.mktemp("foil-fake")
    (bindir / "foil-fake").symlink_to(_FAKE)
    install_shims(bindir)
    # Nothing a seat runs may depend on the interpreter's own bin directory.
    venv_bin = str(Path(sys.executable).parent)
    kept = [part for part in os.environ.get("PATH", "").split(os.pathsep) if part != venv_bin]
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), *kept)))
    return bindir


@pytest.fixture
def stand_ins(_foil_fake_on_path: Path) -> Path:
    """The fake linked under each real preset's command name."""

    for name in STAND_INS:
        (_foil_fake_on_path / name).symlink_to(_FAKE)
    return _foil_fake_on_path


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
