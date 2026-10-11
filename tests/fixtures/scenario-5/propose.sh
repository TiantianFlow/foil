#!/bin/sh
set -eu
root="${FOIL_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/foil}"
note="$root/projects/$FOIL_PROJECT/board/notes/accept.txt"
mkdir -p "$(dirname "$note")"
id=$(foil memory add "check the merged tests")
if foil memory accept "$id"; then
  echo accepted > "$note"
else
  echo refused > "$note"
fi
foil send lead "proposed $id"
