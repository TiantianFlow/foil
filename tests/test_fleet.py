"""Live fleet membership and lead authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil.fleet import FleetError, FleetRecord, FleetStore


def test_operator_and_lead_may_change_membership(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fleet_id = "fleet-auth"
    FleetStore(state).write(
        FleetRecord(
            fleet_id=fleet_id,
            project_root=str(tmp_path.resolve()),
            updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            lead_seat_id="lead",
        )
    )
    store = FleetStore(state)
    store.require_lead_actor(fleet_id, None)
    store.require_lead_actor(fleet_id, "operator")
    store.require_lead_actor(fleet_id, "lead")
    with pytest.raises(FleetError, match="not authorized"):
        store.require_lead_actor(fleet_id, "implementer")
