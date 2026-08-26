"""Runner plan validation and exec-time environment forwarding."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foil.runner import RunnerError, _load_plan


def _write_plan(tmp_path: Path, payload: dict) -> Path:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(payload), encoding="utf-8")
    return plan


def _base_plan(tmp_path: Path) -> dict:
    return {"argv": ["fixture", "--version"], "cwd": str(tmp_path), "env": {}}


def test_plan_accepts_an_optional_env_forward_list(tmp_path: Path) -> None:
    payload = _base_plan(tmp_path)
    payload["env_forward"] = ["FOIL_FORWARD_ME"]
    plan = _write_plan(tmp_path, payload)

    loaded = _load_plan(plan)

    assert loaded["env_forward"] == ["FOIL_FORWARD_ME"]


def test_plan_rejects_unknown_fields_and_bad_forward_names(tmp_path: Path) -> None:
    unknown = _base_plan(tmp_path)
    unknown["secrets"] = {"token": "sk-nope"}
    with pytest.raises(RunnerError, match="invalid fields"):
        _load_plan(_write_plan(tmp_path, unknown))

    missing = {"argv": ["fixture"], "cwd": str(tmp_path)}
    with pytest.raises(RunnerError, match="invalid fields"):
        _load_plan(_write_plan(tmp_path, missing))

    bad_name = _base_plan(tmp_path)
    bad_name["env_forward"] = ["NAME=WITH-VALUE"]
    with pytest.raises(RunnerError, match="env_forward"):
        _load_plan(_write_plan(tmp_path, bad_name))


def test_runner_merges_forwarded_values_at_exec_time(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\n"
        "print(os.environ.get('FOIL_FORWARD_ME', 'absent'))\n"
        "print(os.environ.get('FOIL_FORWARD_MISSING', 'absent'))\n",
        encoding="utf-8",
    )
    plan = _write_plan(
        tmp_path,
        {
            "argv": [sys.executable, str(probe)],
            "cwd": str(tmp_path),
            "env": {"PATH": os.environ.get("PATH", "")},
            "env_forward": ["FOIL_FORWARD_ME", "FOIL_FORWARD_MISSING"],
        },
    )
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "FOIL_FORWARD_ME": "forwarded-exec-value",
    }

    result = subprocess.run(
        [sys.executable, "-m", "foil.runner", str(plan)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["forwarded-exec-value", "absent"]
    # The persisted plan holds variable names only, never values.
    assert "forwarded-exec-value" not in plan.read_text(encoding="utf-8")
