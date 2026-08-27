"""Init produces an empty live fleet plus a role library."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from foil.fleet import FleetStore
from foil.onboarding import DEFAULT_ROLE_IDS
from foil.registry import RegistryStore


def test_init_git_setup_produces_empty_live_fleet(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state_root = tmp_path / "state"
    environment = os.environ.copy()
    environment["FOIL_STATE_DIR"] = str(state_root)

    initialized = subprocess.run(
        [sys.executable, "-m", "foil", "init"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert initialized.returncode == 0, initialized.stderr
    payload = json.loads(initialized.stdout)
    roles_path = project / ".foil" / "roles"
    assert payload["roles_path"] == str(roles_path)
    assert payload["git_branch"] == "foil-demo"
    assert payload["lead_seat_id"] is None
    assert not (project / ".foil" / "runtime.toml").exists()
    assert not (project / ".foil" / "fleet.toml").exists()
    assert (project / ".foil" / "seats.toml").is_file()

    subprocess.run(
        ["git", "init", "--quiet", "-b", payload["git_branch"]],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )

    assert {path.stem for path in roles_path.glob("*.toml")} == set(DEFAULT_ROLE_IDS)
    assert RegistryStore(payload["state_root"]).list_seats(payload["fleet_id"]) == []
    fleet = FleetStore(payload["state_root"]).read(payload["fleet_id"])
    assert fleet.lead_seat_id is None
    assert fleet.project_root == str(project.resolve())
