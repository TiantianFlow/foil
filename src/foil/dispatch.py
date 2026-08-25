"""Evidence-based usage-pool dispatch (CAP-006–CAP-007)."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from foil.runtime import AdapterCatalog, _safe_environment
from foil.runtime_config import FleetConfig, SeatConfig, UsagePoolConfig

DEFAULT_USAGE_ARGV = ("usage", "--format", "json")


def dispatch(config: FleetConfig, *, capability: str) -> dict[str, Any]:
    catalog = AdapterCatalog(config.adapter_paths)
    candidates: list[dict[str, Any]] = []
    for seat in config.seats:
        if capability and not _matches_capability(seat, capability):
            continue
        candidates.append(_candidate(config, seat, catalog))
    if not candidates:
        candidates = [_candidate(config, seat, catalog) for seat in config.seats]
    if not candidates:
        raise ValueError("no seats are available to dispatch")
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


def _matches_capability(seat: SeatConfig, capability: str) -> bool:
    haystack = f"{seat.seat_id} {seat.display_name}".lower()
    return capability.lower() in haystack


def _candidate(
    config: FleetConfig, seat: SeatConfig, catalog: AdapterCatalog
) -> dict[str, Any]:
    pool = config.pool_for(seat)
    evidence = _probe_usage(_executable_name(seat, pool, catalog))
    load = evidence.get("active_load")
    if not isinstance(load, int):
        load = 0
    return {
        "seat_id": seat.seat_id,
        "pool_id": pool.pool_id,
        "evidence": evidence,
        "load": load,
    }


def _executable_name(
    seat: SeatConfig, pool: UsagePoolConfig, catalog: AdapterCatalog
) -> str | None:
    if seat.cli:
        return seat.cli
    if not pool.adapter_id:
        return None
    try:
        adapter = catalog.load(pool.adapter_id)
    except Exception:
        return pool.adapter_id
    if adapter.executable.candidates:
        return adapter.executable.candidates[0]
    return pool.adapter_id


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
