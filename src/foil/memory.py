"""Project-scoped lessons. Slice A is a stub."""

from __future__ import annotations

from pathlib import Path

from foil.errors import FoilError, assert_no_secret


def add_lesson(root: Path, text: str, *, replaces: str | None = None) -> None:
    del root, replaces
    assert_no_secret(text)
    raise FoilError("foil: not initialized")


def accept_lesson(root: Path, lesson_id: str) -> None:
    del root, lesson_id
    raise FoilError("foil: not initialized")


def reject_lesson(root: Path, lesson_id: str) -> None:
    del root, lesson_id
    raise FoilError("foil: not initialized")


def list_lessons(
    root: Path, *, all_lessons: bool = False, as_json: bool = False
) -> None:
    del root, all_lessons, as_json
    raise FoilError("foil: not initialized")
