"""Evidence-based seat ranking from live registry usage probes (CAP-006–CAP-007)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from foil.registry import RegistryStore
from foil.runtime import _safe_environment

DEFAULT_USAGE_ARGV = ("usage", "--format", "json")


def dispatch(
    state_root,
    fleet_id: str,
    *,
    capability: str,
) -> dict[str, Any]:
    records = RegistryStore(state_root).list_seats(fleet_id)
    if not records:
        raise ValueError("no seats are available to dispatch")
    candidates = [
        _candidate(record)
        for record in records
        if not capability or _matches_capability(record, capability)
    ]
    if not candidates:
        candidates = [_candidate(record) for record in records]
    ranked = sorted(candidates, key=lambda item: (item["load"], item["seat_id"]))
    selected = ranked[0]
    return {
        "selected_seat_id": selected["seat_id"],
        "selected_pool_id": selected["pool_id"],
        "evidence": {
            item["seat_id"]: {
                "pool_id": item["pool_id"],
                "availability": item["evidence"].get("availability", "unknown"),
                "active_load": item["load"],
            }
            for item in ranked
        },
        "capability": capability,
    }


def _matches_capability(record, capability: str) -> bool:
    profile = record.extensions.get("profile") or {}
    haystack = " ".join(
        str(value)
        for value in (
            record.seat_id,
            profile.get("display_name"),
            profile.get("role_id"),
            profile.get("cli"),
        )
        if value
    ).lower()
    return capability.lower() in haystack


def _candidate(record) -> dict[str, Any]:
    profile = record.extensions.get("profile") or {}
    cli = profile.get("cli") if isinstance(profile.get("cli"), str) else None
    evidence = _probe_usage(cli)
    load = evidence.get("active_load")
    if not isinstance(load, int):
        load = 0
    return {
        "seat_id": record.seat_id,
        "pool_id": record.usage_pool_id,
        "evidence": evidence,
        "load": load,
    }


def _probe_usage(executable_name: str | None) -> dict[str, Any]:
    if not executable_name:
        return {"availability": "unknown"}
    resolved = shutil.which(executable_name)
    if resolved is None:
        return {"availability": "unknown"}
    argv = [resolved, *DEFAULT_USAGE_ARGV]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env=_safe_environment(executable=resolved),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"availability": "unknown"}
    if result.returncode != 0 or not result.stdout.strip():
        return {"availability": "unknown"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"availability": "unknown"}
    if not isinstance(payload, dict):
        return {"availability": "unknown"}
    return payload
