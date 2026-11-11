"""Commands a mocked seat finds on PATH, bound to the Foil under test.

A seat runs `foil` and `python3` by name, from the PATH its spawner had.
Without these two files that is whatever is installed first on the
machine: another Foil version, or a Python that lacks the test tools.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import foil


def install_shims(bindir: Path) -> None:
    """Write `foil` and `python3` into bindir for this interpreter and this source tree."""

    python = shlex.quote(sys.executable)
    source = str(Path(foil.__file__).resolve().parents[1])
    # The source path goes in through sys.path, not PYTHONPATH: an exported
    # variable would reach a tmux server this command starts.
    entry = (
        f"import sys; sys.path.insert(0, {source!r}); "
        "from foil.cli import main; sys.exit(main())"
    )
    _script(bindir / "foil", f'exec {python} -c {shlex.quote(entry)} "$@"')
    _script(bindir / "python3", f'exec {python} "$@"')


def _script(path: Path, line: str) -> None:
    path.write_text(f"#!/bin/sh\n{line}\n", encoding="utf-8")
    path.chmod(0o755)
