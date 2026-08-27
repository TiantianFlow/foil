---
name: foil-grok-cli-adapter
description: Operate Foil seats backed by the locally authenticated Grok CLI.
---

# Grok CLI adapter

This adapter targets the locally authenticated Grok CLI. Keep the usage-pool adapter ID
`grok_cli` paired with the bare model ID `grok-4.6`.

Foil generates the native session UUID before launch and passes it through
`--session-id`. Native continuity uses `--resume <recorded-id>`. Authentication
remains owned by the CLI; never place credentials in Foil config or state.

Launch appends `Read {bootstrap_path}, its sibling FOIL.md, and the
role_path recorded in bootstrap.json before beginning.` so a new seat
reads its identity without tmux typing. `--permission auto` adds
`--always-approve`. Default spawn stays `--permission supervised`.

Use `foil status --json` to verify the exact recorded native ID and tmux
identity. Use `foil seat stop` and `foil resume` for verified lifecycle
transitions; do not kill a tmux target by display name alone.
