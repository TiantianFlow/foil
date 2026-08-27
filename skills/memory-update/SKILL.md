---
name: foil-memory-update
description: Propose and review durable Foil lessons. Use when a seat should record a lesson that a manager can accept, reject, or supersede.
---

# Foil memory update

Reviewed lessons are JSON files, not chat. If a host cannot load this skill,
use `foil --help` and `foil memory-propose --help`.

## Install this skill

Canonical source: `skills/memory-update/` in a Foil checkout. Copy or link that
directory into the host skill path.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

## Commands

```sh
foil memory-propose --state-dir STATE_ROOT --fleet FLEET_ID --lesson lesson-ack-first --author memory-curator --task quickstart-review --body "Acknowledge mailbox messages before claiming done."
foil memory-accept --state-dir STATE_ROOT --fleet FLEET_ID --lesson lesson-ack-first --actor manager
foil memory-supersede --state-dir STATE_ROOT --fleet FLEET_ID --lesson lesson-ack-first --author memory-curator --replacement lesson-ack-and-status --body "Ack mail, then poll-status." --task quickstart-review
foil memory-status --state-dir STATE_ROOT --fleet FLEET_ID --lesson lesson-ack-and-status
foil memory-reject --state-dir STATE_ROOT --fleet FLEET_ID --lesson lesson-ack-and-status --actor manager
```

States are `proposed`, `accepted`, `superseded`, and `rejected`. Secret-shaped
bodies are rejected. Do not invent quota, credentials, or pane-scraped lessons.
