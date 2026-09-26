"""Scenario fixtures land with the fake harness; keep the e2e path collectable."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def test_scenarios_are_not_part_of_slice_a() -> None:
    pytest.skip("scenario 1-6 fixtures land with the fake harness")
