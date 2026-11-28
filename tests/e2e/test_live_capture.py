"""One opt-in two-turn probe per public preset; no vendor store is copied."""

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foil import __version__
from foil.cli import main
from foil.runner import load_record
from foil.store import load_registry
from tests.conftest import _REAL_WHICH
from tests.e2e.conftest import STAND_INS
from tests.e2e.test_scenarios import _close, _prepare, _set_auto, _set_harness, _wait
from tests.e2e.test_turns import _board
from tests.harness import live_capture, live_record, recordings
from tests.harness.shims import install_shims, install_tmux

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.e2e_live,
    pytest.mark.skipif(os.environ.get("FOIL_E2E_LIVE") != "1",
                       reason="set FOIL_E2E_LIVE=1 to record the real preset round trips"),
]
_REAL_PATH = os.environ.get("PATH", "")


def test_preset_round_trip_live(tmp_path, monkeypatch):
    binaries = {name: _REAL_WHICH(name, path=_REAL_PATH) for name in STAND_INS}
    assert all(binaries.values()), "the live preset probe needs all five real CLI binaries on PATH"
    tmux = _REAL_WHICH("tmux", path=_REAL_PATH)
    assert tmux is not None
    for harness, binary in binaries.items():
        match = re.search(r"\b[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)[.0-9]*)?\b",
                          live_record.cli_version(binary))
        assert match is not None, f"{harness}: could not determine a safe CLI version"
        with monkeypatch.context() as env, tempfile.TemporaryDirectory(dir=tmp_path) as scratch:
            root = Path(scratch)
            bindir = root / "bin"
            bindir.mkdir()
            raw = root / "raw"
            raw.mkdir(mode=0o700)
            live_capture.install(bindir / harness, binary, raw)
            label = "foil-live-" + uuid.uuid4().hex[:12]
            install_tmux(bindir, tmux, label)
            install_shims(bindir)
            env.setenv("PATH", f"{bindir}{os.pathsep}{_REAL_PATH}")
            env.setattr(shutil, "which", _REAL_WHICH)
            for name in ("TMUX", "TMUX_PANE", "FOIL_E2E_HARNESS"):
                env.delenv(name, raising=False)
            repo = _prepare(root, "scenario-2", env, fake=False)
            _set_harness(repo, harness)
            _set_auto(repo)

            def completed(turn, repo=repo, raw=raw):
                state = load_record(_board(repo), "lead")
                return ((raw / f"{turn}.done").is_file() and state["turn_started"] is None
                        and state["last_exit"] == 0 and state["session_id"])

            passed = False
            try:
                first_task = "Run pwd once, then reply only with ready. Do not edit any files."
                assert main(["seat", "spawn", "lead", "--task", first_task]) == 0
                _wait(repo, lambda: completed("start"), timeout=180)
                first = load_record(_board(repo), "lead")["session_id"]
                second_task = "Run pwd once more. Reply only with done. Do not edit any files."
                assert main(["send", "lead", second_task]) == 0
                _wait(repo, lambda: completed("resume"), timeout=180)
                assert load_record(_board(repo), "lead")["session_id"] == first
                seat = load_registry(repo)["seats"]["lead"]
                assert seat["harness"] == harness
                live_capture.export(
                    harness, raw, recordings.GOLDEN / harness / recordings.SCENARIO,
                    session=first, cwd=seat["worktree"], cli_version=match.group(),
                )
                passed = True
            finally:
                try:
                    live_record.write(live_record.record_path(), [(
                        harness, match.group(), "preset_round_trip",
                        "passed" if passed else "failed", datetime.now(UTC).date().isoformat(),
                        __version__,
                    )])
                finally:
                    _close(repo)
                    subprocess.run([tmux, "-L", label, "kill-server"], check=False,
                                   capture_output=True)
