"""Suite fixtures. Init needs a harness binary; CI does not install agent CLIs."""

from __future__ import annotations

import os
import shutil

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
    monkeypatch.setenv("PATH", os.pathsep.join((str(bindir), os.environ.get("PATH", ""))))
    monkeypatch.setattr(shutil, "which", _which_without_real_agy)


# agy sorts first, so a real one installed here would take the default
# templates. Only lookups are hidden; a stand-in a test puts on PATH still counts.
_REAL_WHICH = shutil.which
_REAL_AGY_DIRS = {
    part
    for part in os.environ.get("PATH", "").split(os.pathsep)
    if part and os.access(os.path.join(part, "agy"), os.X_OK)
}


def _which_without_real_agy(cmd, mode=os.F_OK | os.X_OK, path=None):  # type: ignore[no-untyped-def]
    if os.path.basename(str(cmd)) != "agy" or os.path.dirname(str(cmd)):
        return _REAL_WHICH(cmd, mode, path)
    search = os.environ.get("PATH", "") if path is None else path
    kept = [part for part in search.split(os.pathsep) if part not in _REAL_AGY_DIRS]
    return _REAL_WHICH(cmd, mode, os.pathsep.join(kept))
