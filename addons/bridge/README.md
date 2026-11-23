# foil-bridge

The local page for [Foil](../../README.md). It listens on `127.0.0.1`
only. Core does not import this package. `foil bridge` looks for the
`foil-bridge` program on `PATH`, and `foil bridge install` writes the
boot unit.

The home is the conversation with the master. A path in that
conversation opens a read-only page: the project's fleets, one fleet,
a board file, or a seat's conversation and the diff of its branch.
The page, the token, and `foil doctor` are specified in
[the requirements](../../docs/requirements.md).

`foil-bridge transcript SEAT` prints one seat's conversation. It is a
summary unless `--level` says otherwise, and it masks text that looks
like a credential. The requirements say where each harness keeps the file.
