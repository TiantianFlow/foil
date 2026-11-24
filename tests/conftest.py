"""Suite fixtures. Init needs a harness binary; CI does not install agent CLIs."""

from __future__ import annotations

import os

import pytest

_HARNESSES = ("grok", "claude", "codex", "opencode")


@pytest.fixture(autouse=True)
def _harness_stubs(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    # A suite run inside an agent harness must not join as that session.
    for name in ("CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FOIL_HOME", str(tmp_path_factory.mktemp("foil-home")))
    bindir = tmp_path_factory.mktemp("harnesses")
    for name in _HARNESSES:
        binary = bindir / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)
    # agy sorts first, so a real one installed here would take the default
    # templates. The suite never sees a real CLI that has no stub here.
    kept = [
        part
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if not (part and os.access(os.path.join(part, "agy"), os.X_OK))
    ]
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), *kept)))
