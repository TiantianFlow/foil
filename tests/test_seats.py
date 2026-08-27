"""Predefined seat staffing recipes (.foil/seats.toml)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from foil.seats import (
    SeatStaffing,
    SeatStaffingError,
    apply_overrides,
    dump_roster,
    load_roster,
    resolve_spawn_staffing,
    upsert_seat,
)
from tests.test_profiles import (
    COMPLETE_PROFILE,
    _fleet_root,
    _profile_controller,
    generated_argv,
)


def _write_roster(project: Path, text: str) -> Path:
    path = project / ".foil" / "seats.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_load_roster_reads_a_complete_seat(tmp_path: Path) -> None:
    _write_roster(
        tmp_path,
        """
schema_version = 1

[seats.lead]
lead = true
cli = "grok"
role = "manager"
model = "grok-4.6"
permission = "auto"
display_name = "Lead"

[seats.spec]
profile = "profiles/spec.toml"
role_file = "personas/engineering-frontend.md"
cwd = "apps/web"
isolated = false
shared_cwd = true
""",
    )

    roster = load_roster(tmp_path)

    assert roster["lead"].cli == "grok"
    assert roster["lead"].lead is True
    assert roster["lead"].model == "grok-4.6"
    assert roster["lead"].permission == "auto"
    assert roster["spec"].profile == "profiles/spec.toml"
    assert roster["spec"].role_file == "personas/engineering-frontend.md"
    assert roster["spec"].isolated is False
    assert roster["spec"].shared_cwd is True


def test_unknown_fields_and_role_plus_role_file_fail_closed(tmp_path: Path) -> None:
    _write_roster(
        tmp_path,
        """
schema_version = 1

[seats.lead]
cli = "grok"
quota = "secret"
""",
    )
    with pytest.raises(SeatStaffingError, match="unknown fields"):
        load_roster(tmp_path)

    _write_roster(
        tmp_path,
        """
schema_version = 1

[seats.lead]
cli = "grok"
role = "manager"
role_file = "personas/manager.md"
""",
    )
    with pytest.raises(SeatStaffingError, match="both role and role_file"):
        load_roster(tmp_path)


def test_upsert_and_dump_round_trip(tmp_path: Path) -> None:
    path = upsert_seat(
        tmp_path,
        SeatStaffing(seat_id="lead", lead=True, cli="grok", role="manager"),
    )

    assert path == tmp_path / ".foil" / "seats.toml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("schema_version = 1\n")
    assert "[seats.\"lead\"]" in text
    assert dump_roster(load_roster(tmp_path)) == text


def test_resolve_spawn_staffing_fails_closed_without_config_or_override(
    tmp_path: Path,
) -> None:
    with pytest.raises(SeatStaffingError, match="no predefined config"):
        resolve_spawn_staffing(tmp_path, "spec")

    _write_roster(tmp_path, "schema_version = 1\n")
    with pytest.raises(SeatStaffingError, match="no predefined seat config"):
        resolve_spawn_staffing(tmp_path, "spec")

    _write_roster(
        tmp_path,
        """
schema_version = 1

[seats.spec]
role = "implementer"
""",
    )
    with pytest.raises(SeatStaffingError, match="neither cli nor profile"):
        resolve_spawn_staffing(tmp_path, "spec")


def test_apply_overrides_lets_each_flag_win() -> None:
    base = SeatStaffing(
        seat_id="spec",
        cli="grok",
        model="from-file",
        role_file="personas/old.md",
        permission="auto",
    )

    merged = apply_overrides(
        base,
        seat_id="spec",
        model="from-flag",
        role="implementer",
        permission="supervised",
    )

    assert merged.cli == "grok"
    assert merged.model == "from-flag"
    assert merged.role == "implementer"
    assert merged.role_file is None
    assert merged.permission == "supervised"


def test_spawn_from_predefined_seat_config_without_cli_or_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write_roster(
        project,
        """
schema_version = 1

[seats.lead]
lead = true
cli = "fixture-agent"
model = "from-file"
permission = "auto"
""",
    )

    spawned = controller.spawn(seat_id="lead")

    assert {seat["seat_id"] for seat in spawned} == {"lead"}
    record = controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    assert record.extensions["model"] == "from-file"
    assert record.extensions["profile"]["permission"] == "auto"
    assert record.extensions["profile"]["cli"] == "fixture-agent"


def test_spawn_flags_override_predefined_seat_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write_roster(
        project,
        """
schema_version = 1

[seats.lead]
lead = true
cli = "fixture-agent"
model = "from-file"
permission = "auto"
""",
    )

    controller.spawn(
        seat_id="lead",
        model="from-flag",
        permission="supervised",
        launch_argv=generated_argv(),
    )

    record = controller.registry.read_seat(controller.fleet.fleet_id, "lead")
    assert record.extensions["model"] == "from-flag"
    assert record.extensions["profile"]["permission"] == "supervised"


def test_spawn_remainder_argv_still_wins_over_configured_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, _tmux, _executable = _profile_controller(tmp_path, monkeypatch)
    project = tmp_path / "project"
    profile_path = project / "profiles" / "spec.toml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(COMPLETE_PROFILE, encoding="utf-8")
    _write_roster(
        project,
        """
schema_version = 1

[seats.lead]
lead = true
profile = "profiles/spec.toml"
""",
    )

    controller.spawn(seat_id="lead", launch_argv=generated_argv())

    plan = json.loads(
        (_fleet_root(controller) / "runner-plans" / "lead.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["argv"][1] == "launch"
    assert "--native-id" in plan["argv"]


def test_seats_cli_set_list_and_show(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    written = subprocess.run(
        [
            sys.executable,
            "-m",
            "foil",
            "seats",
            "set",
            "--project",
            str(project),
            "--json",
            "--seat",
            "lead",
            "--lead",
            "--cli",
            "grok",
            "--role",
            "manager",
            "--model",
            "grok-4.6",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert written.returncode == 0, written.stderr
    payload = json.loads(written.stdout)
    assert payload["seat"]["cli"] == "grok"
    assert payload["seat"]["lead"] is True

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "foil",
            "seats",
            "list",
            "--project",
            str(project),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout)["seats"][0]["seat_id"] == "lead"

    shown = subprocess.run(
        [
            sys.executable,
            "-m",
            "foil",
            "seats",
            "show",
            "--project",
            str(project),
            "--json",
            "--seat",
            "lead",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["model"] == "grok-4.6"


def test_seats_set_without_cli_or_profile_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "foil",
            "seats",
            "set",
            "--project",
            str(project),
            "--seat",
            "spec",
            "--role",
            "implementer",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "--cli" in result.stderr or "--profile" in result.stderr
    assert not (project / ".foil" / "seats.toml").exists()
