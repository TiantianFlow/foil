"""Live fleet membership and lead authorization."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil.fleet import FleetError, FleetRecord, FleetStore, resolve_caller


def test_list_ids_returns_only_recorded_fleets(tmp_path: Path) -> None:
    state = tmp_path / "state"
    store = FleetStore(state)
    assert store.list_ids() == []
    FleetStore(state).write(
        FleetRecord(
            fleet_id="fleet-auth",
            project_root=str(tmp_path.resolve()),
            updated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
    )
    assert store.list_ids() == ["fleet-auth"]


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


def test_in_seat_caller_cannot_override_identity(tmp_path: Path) -> None:
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
    with pytest.raises(FleetError, match="cannot override"):
        store.require_lifecycle_actor(
            fleet_id,
            "operator",
            environ={"FOIL_SEAT_ID": "implementer"},
        )
    with pytest.raises(FleetError, match="cannot override"):
        store.require_lifecycle_actor(
            fleet_id,
            "lead",
            environ={"FOIL_SEAT_ID": "implementer"},
        )
    with pytest.raises(FleetError, match="not authorized"):
        store.require_lifecycle_actor(
            fleet_id,
            None,
            environ={"FOIL_SEAT_ID": "implementer"},
        )
    store.require_lifecycle_actor(fleet_id, None, environ={"FOIL_SEAT_ID": "lead"})
    store.require_lifecycle_actor(fleet_id, None, environ={})
    assert resolve_caller(None, environ={"FOIL_SEAT_ID": "implementer"}) == "implementer"
    assert store.require_reviewer(fleet_id, "manager", environ={}) == "manager"
    with pytest.raises(FleetError, match="review memory"):
        store.require_reviewer(
            fleet_id, None, environ={"FOIL_SEAT_ID": "implementer"}
        )
