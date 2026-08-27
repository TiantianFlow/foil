---
name: foil-opencode-adapter
description: Operate Foil seats backed by the locally authenticated OpenCode CLI.
---

# OpenCode adapter

This adapter targets the locally authenticated OpenCode CLI. Keep the usage-pool adapter ID
`opencode` paired with model ID `xai/grok-4.6`.

OpenCode assigns its native session ID. Foil snapshots the structured output
from `opencode session list --format json`, launches the interactive process,
and records the single new session whose directory matches the configured
worktree. Native continuity uses `--session <recorded-id>`.

Launch delivers the startup instruction with OpenCode's `--prompt` flag:
`Read {bootstrap_path}, its sibling FOIL.md, and the role_path recorded in
bootstrap.json before beginning.` so a new seat reads its identity without
tmux typing. `--permission auto` adds
`--auto`. Default spawn stays `--permission supervised`. Requested
`--model` is not observed-model truth.

Session creation may lag until OpenCode initializes the conversation. If launch
status has a null native ID, run `foil status --json` to reconcile it from
structured discovery before stopping the seat. Ambiguous discovery fails closed;
terminal buffers are never scraped. Authentication remains owned by OpenCode.
