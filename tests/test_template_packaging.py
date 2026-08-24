"""Distribution contracts for onboarding templates (CAP-025, CAP-031)."""

from __future__ import annotations

import tomllib
from importlib import resources

import pytest

from foil.adapters import load_builtin_adapter
from foil.onboarding import DEFAULT_ROLE_IDS


def test_default_fleet_and_every_role_are_package_resources() -> None:
    template_root = resources.files("foil.templates")
    fleet_template = template_root.joinpath("fleet.toml")

    assert fleet_template.is_file()
    fleet = tomllib.loads(fleet_template.read_text(encoding="utf-8"))
    assert fleet["schema_version"] == 1

    roles_root = template_root.joinpath("roles")
    packaged_role_ids = set()
    for role_id in DEFAULT_ROLE_IDS:
        role_resource = roles_root.joinpath(f"{role_id}.toml")
        assert role_resource.is_file()
        role = tomllib.loads(role_resource.read_text(encoding="utf-8"))
        assert role["id"] == role_id
        packaged_role_ids.add(role["id"])

    assert packaged_role_ids == set(DEFAULT_ROLE_IDS)


@pytest.mark.parametrize(
    ("adapter_id", "model"),
    [
        ("grok_cli", "grok-4.6"),
        ("opencode", "xai/grok-4.6"),
    ],
)
def test_builtin_adapter_records_are_package_local_resources(
    adapter_id: str,
    model: str,
) -> None:
    adapter_resource = (
        resources.files("foil")
        .joinpath("resources")
        .joinpath("adapters")
        .joinpath(f"{adapter_id}.toml")
    )

    assert adapter_resource.is_file()
    assert load_builtin_adapter(adapter_id).models == (model,)
