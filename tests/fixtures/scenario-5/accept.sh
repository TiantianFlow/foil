#!/bin/sh
set -eu
id=$(python3 -c '
import os
from pathlib import Path
found = ""
root = Path(os.environ["FOIL_HOME"]) / "projects" / os.environ["FOIL_PROJECT"]
for path in sorted((root / "board/mail/lead").glob("*.md")):
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("proposed "):
            found = line.split()[1]
if found:
    print(found)
')
foil memory accept "$id"
foil seat spawn reviewer --name reader --task "carry the lesson"
