#!/bin/sh
set -eu
python3 -c 'from pathlib import Path; p = Path("calc.py"); p.write_text(p.read_text().replace("left - right", "left + right"))'
git add calc.py
if ! git diff --cached --quiet; then
  git commit -m "fix add"
fi
foil send lead "fixed on foil/implementer-1"
