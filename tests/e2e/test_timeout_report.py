"""A wait that times out shows the fleet: every seat, each pane, and the doubles' logs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foil.cli import main
from foil.store import load_registry
from tests.e2e.support import close, fleet_dir, timeout_report, wait
from tests.e2e.test_scenarios import _prepare, _wait

pytestmark = pytest.mark.e2e


def _state(repo: Path, seat: str) -> str:
    for line in timeout_report(repo).splitlines():
        parts = line.split("\t")
        if len(parts) == 4 and parts[0] == seat:
            return parts[2]
    return ""


def test_a_timeout_shows_the_seat_list_and_every_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _prepare(tmp_path, "scenario-2", monkeypatch)
    try:
        fake = fleet_dir(repo) / "run" / "fake"
        fake.mkdir(parents=True, exist_ok=True)
        (fake / "implementer-1.json").write_text(
            json.dumps({"start": [{"exit": 3}]}), encoding="utf-8"
        )
        assert main(["seat", "spawn", "lead"]) == 0
        assert main(["seat", "spawn", "implementer"]) == 0
        wait(repo, lambda: _state(repo, "implementer-1") == "dead", what="the worker to exit")
        # The worker is dead on purpose, so this wait must not stop for it.
        wait(
            repo,
            lambda: "foil-fake lead ready" in timeout_report(repo),
            dead_ok=True,
            what="the lead",
        )
        for failing in (_wait, wait):
            with pytest.raises(AssertionError, match="timed out") as caught:
                failing(repo, lambda: False, 0)
            message = str(caught.value)
            # The seat list, with the state tmux reports.
            assert "lead\tlead\talive\tfake" in message
            assert "implementer-1\timplementer\tdead\tfake" in message
            # A living seat's pane, and the last words of one that exited.
            assert "foil-fake lead ready" in message
            assert "foil-fake: exit 3" in message
            # The double's own log.
            assert "----- implementer-1.log" in message
        assert load_registry(repo)["seats"]["implementer-1"]["window_id"]
    finally:
        close(repo)


def test_a_timeout_names_what_was_awaited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _prepare(tmp_path, "scenario-6", monkeypatch)
    with pytest.raises(AssertionError) as caught:
        wait(repo, lambda: False, 0, what="the merge")
    assert str(caught.value).startswith("timed out after 0s waiting for the merge\n")
    assert "no seats" in str(caught.value)
