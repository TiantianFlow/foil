"""Put the fake harness on PATH for operator scenarios."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_FAKE = Path(__file__).resolve().parents[1] / "harness" / "foil_fake.py"


@pytest.fixture(autouse=True)
def _foil_fake_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    bindir = tmp_path_factory.mktemp("foil-fake")
    binary = bindir / "foil-fake"
    binary.symlink_to(_FAKE)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), os.environ.get("PATH", ""))))
