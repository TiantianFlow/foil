#!/bin/sh
set -eu
common=$(git rev-parse --git-common-dir)
case "$common" in
  /*) ;;
  *) common="$(pwd)/$common" ;;
esac
project=$(CDPATH= cd "$(dirname "$common")" && pwd)
note="$project/.foil/board/notes/accept.txt"
mkdir -p "$(dirname "$note")"
id=$(foil memory add "check the merged tests")
if foil memory accept "$id"; then
  echo accepted > "$note"
else
  echo refused > "$note"
fi
foil send lead "proposed $id"
