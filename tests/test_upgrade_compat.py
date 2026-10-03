"""Cross-version pins for registry schema 1, session names, and tmux markers.

The derivation and the marker names are the same as Foil 0.2.0. A registry
written in that shape still loads, with `model` empty.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from foil.lifecycle import _session_name
from foil.store import SCHEMA_VERSION, SEAT_FIELDS, load_registry
from foil.tmux import _PROBE_FORMAT


def _session_name_0_2_0(toplevel: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", toplevel.name).strip("-")[:32] or "repo"
    digest = hashlib.sha256(str(toplevel.resolve()).encode()).hexdigest()[:8]
    return f"foil-{slug}-{digest}"


def test_schema_markers_and_session_name_stay_at_the_0_2_0_contract(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 1
    assert SEAT_FIELDS == (
        "name",
        "template",
        "harness",
        "model",
        "window_id",
        "state",
        "worktree",
        "branch",
        "session_id",
    )
    assert _PROBE_FORMAT.startswith(
        "#{session_id}\t#{window_id}\t#{@foil-fleet-id}\t#{@foil-seat-id}"
    )
    names = [tmp_path / "My Repo", tmp_path / "...", tmp_path / ("A" * 40)]
    for path in names:
        path.mkdir()
        assert _session_name(path) == _session_name_0_2_0(path)
    assert _session_name_0_2_0(names[1]).startswith("foil-repo-")
    assert _session_name_0_2_0(names[2]).startswith("foil-" + ("A" * 32) + "-")


def test_a_0_2_0_shaped_registry_still_loads(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    path = repo / ".foil" / "run" / "registry.json"
    path.parent.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "fleet_id": "fleet",
        "tmux_session": "foil-project-01234567",
        "lead": "lead",
        "seats": {
            "lead": {
                "name": "lead",
                "template": "lead",
                "harness": "claude",
                "window_id": "@1",
                "state": "alive",
                "worktree": "",
                "branch": "",
                "session_id": "sid",
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_registry(repo)
    assert loaded["schema_version"] == 1
    assert loaded["fleet_id"] == "fleet"
    assert loaded["tmux_session"] == "foil-project-01234567"
    assert loaded["lead"] == "lead"
    seat = loaded["seats"]["lead"]
    assert seat["window_id"] == "@1"
    assert seat["harness"] == "claude"
    assert seat["session_id"] == "sid"
    assert seat["model"] == ""
    assert set(seat) == set(SEAT_FIELDS)
