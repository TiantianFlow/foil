"""Suite fixtures. Init needs a harness binary; CI does not install agent CLIs."""

from __future__ import annotations

import os

import pytest

_HARNESSES = ("grok", "claude", "codex", "opencode", "gemini")


@pytest.fixture(autouse=True)
def _harness_stubs(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    bindir = tmp_path_factory.mktemp("harnesses")
    for name in _HARNESSES:
        binary = bindir / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), os.environ.get("PATH", ""))))
