#!/bin/sh
set -eu
wt=$(python3 -c '
import subprocess
text = subprocess.check_output(["git", "worktree", "list", "--porcelain"], text=True)
path = ""
for line in text.splitlines():
    if line.startswith("worktree "):
        path = line.split(" ", 1)[1]
    elif line == "branch refs/heads/foil/implementer-1":
        print(path)
')
(
  cd "$wt"
  python3 -m pytest -q --noconftest check_calc.py
)
foil send lead approved
