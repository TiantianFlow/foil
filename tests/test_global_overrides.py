"""Global lists plus per-fleet overrides. Real git, mocked harness launch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foil.cli import main
from foil.project import foil_root
from foil.store import load_registry
from tests.test_spawn import _commit, _launch, _plan, _repo


def test_init_writes_shared_lists_and_defaults_permission_to_auto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    root = foil_root(repo)
    assert not (root / "templates" / "lead.toml").exists()
    roles = (root / "roles.toml").read_text(encoding="utf-8")
    assert 'id = "implementer"' in roles
    assert 'permission = "auto"' in roles
    assert 'worktree = true' in roles
    mapping = (root / "mapping.toml").read_text(encoding="utf-8")
    assert 'role = "implementer"' in mapping
    assert "opus" not in mapping
    assert (root / "fleets" / "main" / "board" / "room").is_dir()
    assert not (root / "fleet.json").exists()


def test_fleet_memory_is_what_spawn_puts_on_the_model_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    root = foil_root(repo)
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "implementer", "model=opus"]) == 0
    assert "fleet memory: implementer model = opus" in capsys.readouterr().out
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert "opus" not in (root / "mapping.toml").read_text(encoding="utf-8")
    overrides = root / "fleets" / "main" / "overrides.toml"
    assert 'role = "implementer"' in overrides.read_text(encoding="utf-8")
    assert 'model = "opus"' in overrides.read_text(encoding="utf-8")
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    lead = _plan(calls, 0)["argv"][-1]
    assert str(overrides.resolve()) in lead
    assert "implementer: opus" in lead
    assert "model flag" in lead
    worker = _plan(calls, 1)["argv"]
    assert worker[worker.index("--model") + 1] == "opus"
    seat = load_registry(repo)["seats"]["implementer-1"]
    assert seat["model"] == "opus"
    assert seat["harness"] == "claude"
    assert _plan(calls, 1)["env"]["FOIL_FLEET"] == "main"


def test_two_fleets_share_the_mapping_and_keep_separate_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, monkeypatch)
    _commit(repo)
    calls = _launch(monkeypatch)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    monkeypatch.setenv("FOIL_FLEET", "side")
    monkeypatch.setenv("FOIL_SEAT_ID", "lead")
    assert main(["roster", "update", "implementer", "model=opus"]) == 0
    monkeypatch.delenv("FOIL_SEAT_ID", raising=False)
    assert main(["seat", "spawn", "lead"]) == 0
    assert main(["seat", "spawn", "implementer"]) == 0
    side = _plan(calls, -1)["argv"]
    assert side[side.index("--model") + 1] == "opus"
    assert load_registry(repo)["seats"]["implementer-1"]["model"] == "opus"
    assert "lead" in load_registry(repo)["seats"]
    monkeypatch.delenv("FOIL_FLEET", raising=False)
    main_worker = load_registry(repo)["seats"]["implementer-1"]
    assert main_worker["model"] == ""
    assert "opus" not in (foil_root(repo) / "mapping.toml").read_text(encoding="utf-8")
    main_board = foil_root(repo) / "fleets" / "main" / "board" / "mail"
    side_board = foil_root(repo) / "fleets" / "side" / "board" / "mail"
    assert main_board.is_dir() and side_board.is_dir()
    assert calls[0]["session_name"] != calls[2]["session_name"]
    assert json.loads((foil_root(repo) / "fleets" / "main" / "run" / "registry.json").read_text())[
        "seats"
    ]["implementer-1"]["model"] == ""
