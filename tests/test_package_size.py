"""Report package size against N4.

Counts every line of Python under `src/foil`, including blank lines and
comments. `__pycache__` is excluded. N4 targets 2,500 lines and is not a
hard limit, so this check prints the count and does not fail the build.
"""

from __future__ import annotations

import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "src" / "foil"
N4_TARGET = 2500


def _package_python_files() -> list[Path]:
    files = [
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    return sorted(files)


def _line_count(path: Path) -> int:
    """Count every physical line, including blanks and comments."""

    text = path.read_text(encoding="utf-8")
    if text == "":
        return 0
    return len(text.splitlines())


def test_package_python_line_count_is_reported() -> None:
    total = sum(_line_count(path) for path in _package_python_files())
    warnings.warn(
        f"package Python is {total} lines (N4 target {N4_TARGET})",
        UserWarning,
        stacklevel=1,
    )
