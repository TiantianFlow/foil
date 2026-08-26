"""Seat and usage-pool records reconstructed from the live registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UsagePoolConfig:
    pool_id: str
    adapter_id: str | None
    model: str | None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SeatConfig:
    seat_id: str
    display_name: str
    usage_pool_id: str
    working_directory: Path
    worktree_path: Path
    git_branch: str
    cli: str | None = None
    launch_argv: tuple[str, ...] | None = None
    resume_argv: tuple[str, ...] | None = None
    session_capture: str | None = None
    environment_forward: tuple[str, ...] = ()
