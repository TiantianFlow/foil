"""Init-to-runtime compatibility acceptance (CAP-016, CAP-024–CAP-026, CAP-031)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from foil.onboarding import DEFAULT_ROLE_IDS
from foil.registry import RegistryStore
from foil.runtime_config import load_fleet_config


def test_init_git_setup_produces_loadable_two_seat_runtime_config(tmp_path: Path) -> None:
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
    plan_path = project / ".foil" / "fleet.toml"
    runtime_path = project / ".foil" / "runtime.toml"
    assert payload["config_path"] == str(plan_path)
    assert payload["runtime_config_path"] == str(runtime_path)
    assert payload["git_branch"] == "foil-demo"

    subprocess.run(
        ["git", "init", "--quiet", "-b", payload["git_branch"]],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )

    plan = tomllib.loads(plan_path.read_text(encoding="utf-8"))
    assert [seat["id"] for seat in plan["seats"]] == list(DEFAULT_ROLE_IDS)
    assert RegistryStore(payload["state_root"]).list_seats(payload["fleet_id"]) == []

    runtime = load_fleet_config(runtime_path)
    assert runtime.fleet_id == payload["fleet_id"]
    assert [
        (pool.pool_id, pool.adapter_id, pool.model)
        for pool in runtime.usage_pools
    ] == [
        ("primary", "grok_cli", "grok-4.6"),
        ("independent-review", "opencode", "xai/grok-4.6"),
    ]
    assert [
        (seat.seat_id, seat.usage_pool_id)
        for seat in runtime.seats
    ] == [
        ("implementer", "primary"),
        ("reviewer-challenger", "independent-review"),
    ]
    assert all(seat.working_directory == project.resolve() for seat in runtime.seats)
    assert all(seat.worktree_path == project.resolve() for seat in runtime.seats)
    assert all(seat.git_branch == "foil-demo" for seat in runtime.seats)
