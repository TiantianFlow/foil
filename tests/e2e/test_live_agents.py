"""Optional live T3/T6 against real grok and opencode CLIs.

Default CI cannot authenticate those tools. Set FOIL_E2E_LIVE=1 on a machine
that already has `grok` and `opencode` on PATH to exercise the same operator
commands without PATH shims.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.harness import OperatorFleet

pytestmark = pytest.mark.e2e_live


def _live_clis() -> list[str]:
    return [name for name in ("grok", "opencode") if shutil.which(name) is None]


@pytest.fixture
def live_fleet(tmp_path: Path) -> Iterator[OperatorFleet]:
    if os.environ.get("FOIL_E2E_LIVE") != "1":
        pytest.skip("set FOIL_E2E_LIVE=1 to run against real grok and opencode")
    missing = _live_clis()
    if missing:
        pytest.fail(f"FOIL_E2E_LIVE=1 but missing CLIs: {', '.join(missing)}")
    session = OperatorFleet(tmp_path, live=True)
    session.bootstrap()
    try:
        session.init_project()
        yield session
    finally:
        session.cleanup()


def test_live_lead_and_workers_spawn_status_and_stop(live_fleet: OperatorFleet) -> None:
    fleet = live_fleet
    fleet.start_complementary_fleet()
    status = fleet.lifecycle("status").json()
    seats = {seat["seat_id"]: seat for seat in status["seats"]}
    assert set(seats) == {"lead", "implementer", "reviewer-challenger"}
    assert all(seat["state"] == "working" for seat in seats.values())
    assert fleet.tmux_alive()
    stopped = fleet.lifecycle("stop").json()
    assert all(seat["state"] == "exited" for seat in stopped["seats"])
    assert not fleet.tmux_alive()
