"""L2: package-size standing check against N4.

Counts every line of Python under `src/foil`, including blank lines and
comments. `__pycache__` is excluded. N4 allows at most 2,000 lines of
Python in the package source; this test fails above that limit.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "src" / "foil"
MAX_PACKAGE_LINES = 2000


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


def test_package_python_line_count_is_at_most_2000() -> None:
    files = _package_python_files()
    counts = [(path, _line_count(path)) for path in files]
    total = sum(count for _path, count in counts)
    if total <= MAX_PACKAGE_LINES:
        return

    ranked = sorted(counts, key=lambda item: (-item[1], str(item[0])))
    breakdown = "\n".join(
        f"  {count:5d}  {path.relative_to(ROOT)}" for path, count in ranked
    )
    raise AssertionError(
        f"package Python is {total} lines (N4 limit {MAX_PACKAGE_LINES}). "
        "Count includes blanks and comments in every src/foil/*.py file; "
        f"excludes __pycache__.\n{breakdown}"
    )
