---
name: foil-shared-notepads
description: Share a durable notepad brief between Foil seats. Use when a mailbox message is too narrow and both seats need the same written plan.
---

# Foil shared notepads

You are an outside controller. Shared briefs live in versioned JSON under the
state root. If a host cannot load this skill, use `foil --help` and
`foil notepad-write --help`.

## Install this skill

Canonical source: `skills/shared-notepads/` in a Foil checkout. Copy or link
that directory into the host skill path.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

## Commands

```sh
foil notepad-write --state-dir STATE_ROOT --fleet FLEET_ID --notepad sprint-brief --author implementer --body "Build the mailbox ack path."
foil notepad-read --state-dir STATE_ROOT --fleet FLEET_ID --notepad sprint-brief
foil notepad-ack --state-dir STATE_ROOT --fleet FLEET_ID --notepad sprint-brief --actor reviewer-challenger
```

Records are `<state-dir>/v1/fleets/<fleet-id>/notepads/<notepad-id>.json`.
Duplicate writes of the same body by the same author are reported as
`"duplicate": true`. Secret-shaped bodies are rejected. Do not put credentials
in a notepad. Do not scrape tmux pane text for the brief.
