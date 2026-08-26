"""Shim e2e must not discover a real grok sitting beside tmux on PATH."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from tests.e2e.harness import OperatorFleet


def test_private_bin_python3_is_the_test_runner(fleet: OperatorFleet) -> None:
    assert os.path.samefile(fleet.bin / "python3", sys.executable)
    found = shutil.which("python3", path=fleet.env()["PATH"])
    assert found is not None
    assert os.path.samefile(found, sys.executable)


def test_hermetic_path_hides_host_grok_beside_tmux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_tmux = shutil.which("tmux")
    real_git = shutil.which("git")
    if real_tmux is None:
        pytest.skip("tmux is unavailable")
    assert real_git is not None

    host = tmp_path / "opt" / "homebrew" / "bin"
    host.mkdir(parents=True)
    (host / "tmux").symlink_to(real_tmux)
    (host / "git").symlink_to(real_git)
    decoy_python = host / "python3"
    decoy_python.write_text(
        "#!/bin/sh\necho APPLE_PYTHON39 >&2\nexit 1\n",
        encoding="utf-8",
    )
    decoy_python.chmod(decoy_python.stat().st_mode | stat.S_IXUSR)
    decoy = host / "grok"
    decoy.write_text("#!/bin/sh\necho REAL_GROK_LAUNCHED\n", encoding="utf-8")
    decoy.chmod(decoy.stat().st_mode | stat.S_IXUSR)

    monkeypatch.setenv("PATH", os.pathsep.join((str(host), os.environ.get("PATH", ""))))
    assert Path(shutil.which("grok") or "").resolve() == decoy.resolve()

    fleet = OperatorFleet(tmp_path / "case")
    fleet.bootstrap()
    try:
        hermetic = fleet._hermetic_path()
        parts = hermetic.split(os.pathsep)
        assert str(fleet.bin) == parts[0]
        assert str(host) not in parts
        assert shutil.which("tmux", path=hermetic) == str(fleet.bin / "tmux")
        assert shutil.which("git", path=hermetic) == str(fleet.bin / "git")
        assert shutil.which("grok", path=hermetic) == str(fleet.bin / "grok")
        assert os.path.samefile(fleet.bin / "python3", sys.executable)
        found_python = shutil.which("python3", path=hermetic)
        assert found_python is not None
        assert os.path.samefile(found_python, sys.executable)

        fleet.init_project()
        (fleet.bin / "grok").unlink()
        assert shutil.which("grok", path=fleet.env()["PATH"]) is None
        spawned = fleet.spawn("lead", "grok", lead=True, role="manager")
        assert spawned.returncode != 0
        assert "unavailable" in spawned.stderr.lower()
        assert not fleet.tmux_alive()
    finally:
        fleet.cleanup()
