"""A nudge's Enter has to land after a harness paste burst, or the line sits."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from foil.tmux import NUDGE_ENTER_DELAY, TmuxController

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")

# Codex keeps Enter from submitting for 120ms after a fast burst.
_BURST_S = 0.12

_STAND_IN = """\
import os
import select
import sys
import time
import tty
from pathlib import Path

result = Path(sys.argv[1])
ready = Path(sys.argv[2])
fd = sys.stdin.fileno()
tty.setraw(fd)
ready.write_text("ready\\n", encoding="utf-8")
pending = bytearray()
last = None
end = time.monotonic() + 5
while time.monotonic() < end:
    if not select.select([fd], [], [], 0.02)[0]:
        continue
    data = os.read(fd, 4096)
    if not data:
        break
    now = time.monotonic()
    for byte in data:
        if byte in (10, 13):
            gap = 0.0 if last is None else now - last
            status = "held" if len(pending) >= 3 and gap <= __BURST__ else "submitted"
            text = pending.decode("utf-8", "replace")
            # The test polls for the file, so it must appear already written.
            partial = result.with_name(result.name + ".partial")
            partial.write_text(f"{status}\\n{text}\\n{gap:.4f}\\n", encoding="utf-8")
            os.replace(partial, result)
            raise SystemExit(0)
        pending.append(byte)
        last = now
"""


def _wait(path: Path, seconds: float = 5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if path.is_file():
            return True
        time.sleep(0.02)
    return False


def test_nudge_enter_submits_after_a_paste_burst(tmp_path: Path) -> None:
    assert NUDGE_ENTER_DELAY > _BURST_S
    real = shutil.which("tmux")
    assert real
    socket = f"foilnudge{uuid.uuid4().hex[:8]}"
    wrapper = tmp_path / "tmux"
    wrapper.write_text(f'#!/bin/sh\nexec {real} -L {socket} "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    script = tmp_path / "stand-in.py"
    script.write_text(_STAND_IN.replace("__BURST__", str(_BURST_S)), encoding="utf-8")
    ready = tmp_path / "ready"
    result = tmp_path / "result"
    session = "nudge"
    line = "lead /mail/implementer-1/note.md"
    try:
        started = subprocess.run(
            [
                real,
                "-L",
                socket,
                "new-session",
                "-d",
                "-s",
                session,
                "-x",
                "80",
                "-y",
                "24",
                sys.executable,
                str(script),
                str(result),
                str(ready),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert started.returncode == 0, started.stderr
        assert _wait(ready)
        window = subprocess.run(
            [real, "-L", socket, "display-message", "-p", "-t", session, "#{window_id}"],
            check=False,
            capture_output=True,
            text=True,
        )
        window_id = window.stdout.strip()
        assert window_id.startswith("@")
        TmuxController(executable=str(wrapper)).nudge(window_id, line)
        assert _wait(result), "the stand-in never saw Enter"
        text = result.read_text(encoding="utf-8")
        status, body, _gap = text.splitlines()
        assert status == "submitted", text
        assert body == line
    finally:
        subprocess.run(
            [real, "-L", socket, "kill-server"],
            check=False,
            capture_output=True,
        )
