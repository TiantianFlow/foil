import subprocess
import sys
import time
from pathlib import Path

from .streams import ASSISTANT

MALFORMED = (
    "---\nstate:\n  working: yes\n---\n## Mandate\nDone when: the front matter is refused.\n"
)


def interrupt(fault: dict, root: Path, fake: Path, actor: str) -> tuple[int, str | None] | None:
    if fault.get("leave_child"):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        (fake / f"{actor}.child").write_text(str(child.pid))
    if fault.get("hang"):
        time.sleep(3600)
    if "think" in fault:
        time.sleep(float(fault["think"]))
    if "exit" in fault:
        return int(fault["exit"]), None
    if "pane" in fault:
        return 0, str(fault["pane"])
    if fault.get("silent"):
        return 0, None
    if fault.get("malformed_status"):
        (root / "board" / "status.md").write_text(MALFORMED, encoding="utf-8")
        return 0, ASSISTANT
    return None
