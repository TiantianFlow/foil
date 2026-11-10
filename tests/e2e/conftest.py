"""Put the fake harness, and a `foil` for the running interpreter, on PATH."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

_FAKE = Path(__file__).resolve().parents[1] / "harness" / "foil_fake.py"
STAND_INS = ("claude", "codex", "gemini", "opencode", "grok")


@pytest.fixture(autouse=True)
def _foil_fake_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    bindir = tmp_path_factory.mktemp("foil-fake")
    (bindir / "foil-fake").symlink_to(_FAKE)
    # A seat runs `foil` from PATH; this one is the checkout the test process imports.
    for name, module in (("foil", " -m foil"), ("python3", "")):
        shim = bindir / name
        shim.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)}{module} "$@"\n')
        shim.chmod(0o755)
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
