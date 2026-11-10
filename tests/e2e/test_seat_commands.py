"""Scenario 8: a mocked seat runs the Foil under test, not one the machine has installed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from foil import __version__
from foil.cli import main
from tests.e2e.support import board, close, scripts, wait
from tests.e2e.test_scenarios import _prepare

pytestmark = pytest.mark.e2e


def test_scenario_8_a_seat_runs_the_foil_under_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Another Foil, installed where the machine would find it first. Only the
    # scenario's own folder is ahead of it on the path.
    decoy = tmp_path / "installed"
    decoy.mkdir()
    (decoy / "foil").write_text("#!/bin/sh\necho 'foil 0.0.0'\n", encoding="utf-8")
    (decoy / "foil").chmod(0o755)
    own, rest = os.environ["PATH"].split(os.pathsep, 1)
    assert (Path(own) / "foil-fake").exists()
    monkeypatch.setenv("PATH", os.pathsep.join((own, str(decoy), rest)))

    repo = _prepare(tmp_path, "scenario-8", monkeypatch)
    try:
        scripts(repo, "scenario-8", {"lead": "lead.json", "implementer-1": "implementer.json"})
        assert main(["seat", "spawn", "lead", "--task", "report your foil"]) == 0
        notes = board(repo) / "notes"
        for seat in ("lead", "implementer-1"):
            note = notes / f"{seat}-foil.txt"
            wait(repo, lambda note=note: note.is_file() and note.read_text(), what=note.name)
            assert note.read_text(encoding="utf-8") == f"foil {__version__}\n", seat
    finally:
        close(repo)
