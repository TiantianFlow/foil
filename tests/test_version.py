"""One public version identity across metadata, imports, and the CLI."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from foil import __version__

ROOT = Path(__file__).parents[1]
RELEASE_VERSION = "0.3.2"


def test_release_version_is_consistent_across_package_and_cli() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    result = subprocess.run(
        [sys.executable, "-m", "foil", "--version"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert project["project"]["version"] == RELEASE_VERSION
    assert __version__ == RELEASE_VERSION
    assert result.returncode == 0
    assert result.stdout == f"foil {RELEASE_VERSION}\n"
    assert result.stderr == ""
