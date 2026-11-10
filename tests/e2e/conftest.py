"""Put the fake harness, and a `foil` for the running interpreter, on PATH."""

from __future__ import annotations

import os
import shlex
import sys
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
    # A seat runs `foil` from PATH; this one is the checkout the test process imports.
    shim = bindir / "foil"
    shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m foil "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), os.environ.get("PATH", ""))))
