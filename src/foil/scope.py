"""The project, fleet, and caller of one in-process call.

A thread that sets these does not change another thread's call, and nothing
here reads or writes the process environment.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

project_var: ContextVar[Path | None] = ContextVar("foil_project", default=None)
fleet_var: ContextVar[str | None] = ContextVar("foil_fleet", default=None)
# ``(name, inside, pinned)``. ``inside`` is true when the caller is a seat.
# ``pinned`` is false for the outside default, which is lead or user per fleet.
caller_var: ContextVar[tuple[str, bool, bool] | None] = ContextVar("foil_caller", default=None)


@contextmanager
def bound(
    project: Path | None, fleet: str, caller: str, *, inside: bool, pinned: bool = True
) -> Iterator[None]:
    """Apply one call's project, fleet, and caller until the block ends.

    ``pinned`` false means the outside default: ``lead`` while that fleet's
    lead is joined, otherwise ``user``. ``mail wait --all-fleets`` asks each
    fleet. An explicit caller stays the name it was given.
    """

    tokens: list[tuple[ContextVar[object], object]] = []
    if project is not None:
        tokens.append((project_var, project_var.set(project)))
    tokens.append((fleet_var, fleet_var.set(fleet)))
    tokens.append((caller_var, caller_var.set((caller, inside, pinned))))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
