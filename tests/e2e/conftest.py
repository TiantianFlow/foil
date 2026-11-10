"""Put the fake harness, and the Foil under test, on PATH for the scenarios."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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
