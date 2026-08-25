"""Shared end-to-end fixtures. These tests require tmux like the product does."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.e2e.harness import OperatorFleet


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        item.add_marker(pytest.mark.e2e)


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[OperatorFleet]:
    if shutil.which("tmux") is None:
        pytest.skip("tmux is unavailable")
    session = OperatorFleet(tmp_path)
    session.bootstrap()
    try:
        yield session
    finally:
        session.cleanup()


@pytest.fixture
def initialized(fleet: OperatorFleet) -> OperatorFleet:
    fleet.init_project()
    return fleet
