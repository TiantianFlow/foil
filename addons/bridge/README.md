# foil-bridge

The local page for [Foil](../../README.md). It listens on `127.0.0.1`
only. Core does not import this package. `foil bridge` looks for the
`foil-bridge` program on `PATH`, and `foil bridge install` writes the
boot unit.

The home is the conversation with the master. A notification stays off
until a kind is turned on. It is a label and the id of the card it opens. A path in that
conversation opens a read-only page: the project's fleets, one fleet,
a board file, or a seat's conversation and the diff of its branch.
A seat's page is also a chat with that seat. Mail is sent as you, and
each message shows its delivery state.
A fleet page can pause or resume that fleet, spawn a role, or start a
new goal as a new fleet. A seat page can resume, renew, kill, or
archive that seat. Each one calls `foil.api` as you. Kill, archive, and
anything that fleet's mandate does not cover ask first. A kill or an
archive the master requests stays a card until you confirm it.
A seat with a worktree Foil owns can open a terminal beside the
conversation. It runs your shell there and closes with the view. It
does not attach to the seat. Copy attach command still copies the tmux
command.
The page, the token, and `foil doctor` are specified in
[the requirements](../../docs/requirements.md).

`foil-bridge transcript SEAT` prints one seat's conversation. It is a
summary unless `--level` says otherwise, and it masks text that looks
like a credential. The requirements say where each harness keeps the file.
