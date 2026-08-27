---
name: foil-poll-status
description: Read Foil fleet status from versioned files or live tmux reconciliation. Use when polling seats; never infer progress from tmux pane text.
---

# Foil poll-status

Two commands. Both are JSON for agent callers. If this skill is unavailable,
use `foil poll-status --help` and `foil status --help`.

## Install this skill

Canonical source: `skills/poll-status/`. Copy or link that directory into the
host skill path.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

## Files only: `foil poll-status`

```sh
foil poll-status --state-dir STATE_ROOT --fleet FLEET_ID
```

Reconstructs seat status from versioned files under
`<state-dir>/v1/fleets/<fleet-id>/status/seats/<seat-id>.json`. It does not
probe tmux and does not inspect pane text. Output is deterministic JSON:
`fleet_id` plus `seats`. Missing files yield `"seats": []`.

File states may include `waiting` or `idle` when `foil set-state` wrote those
snapshots. The live projector honors the same explicit operator transitions.
Do not infer waiting or idle from pane text.

## Live reconciliation: `foil status --json`

```sh
foil status --state-dir STATE_ROOT --fleet FLEET_ID --json
foil set-state --state-dir STATE_ROOT --fleet FLEET_ID --json --seat implementer --state idle
```

Reconciles registry, structured native session IDs, and verified tmux
liveness. Live states are `working`, `exited`, `blocked`, `waiting`, and
`idle`. Use this when you need tmux identity and native IDs, not as a
substitute for pane reading.

## Which to call

- Progress between lifecycle commands: `foil poll-status`.
- After `foil seat spawn`, `foil resume`, or a suspected tmux death:
  `foil status --json`.
- Message delivery: `foil message-status`, not poll-status.
