---
name: foil-worker
description: Work as a Foil seat. Use when you have FOIL_SEAT_ID and must poll mail, acknowledge after reading, and reply with files or messages. Do not change fleet membership unless you are the lead.
---

# Foil worker

You are one seat in a live Foil fleet. Foil does not embed a model. Your
identity is in `FOIL_BOOTSTRAP`, `FOIL_INSTRUCTIONS`, and `FOIL_*`
environment variables. Isolated seats also have `FOIL.md` in their
working directory. If this skill is unavailable, read
`$FOIL_INSTRUCTIONS` or `$FOIL_BOOTSTRAP` and run `foil --help`.

## Install this skill

Canonical source: `skills/worker/` in a Foil checkout. Copy or link that
directory into the host skill path.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

## Bootstrap

On start, read `$FOIL_BOOTSTRAP` and `$FOIL_INSTRUCTIONS`, plus the role
file in `FOIL_ROLE_FILE` when that variable is set. Coordinates are also
in `FOIL_STATE_DIR`, `FOIL_FLEET_ID`, `FOIL_SEAT_ID`,
`FOIL_INCARNATION_ID`, `FOIL_LEAD_SEAT_ID`, and `FOIL_CAPABILITIES`. A
wake is only a nudge. It does not contain the mail body. Do not try to
override `FOIL_SEAT_ID` with `--actor`.

## Mailbox

Poll, then acknowledge only after you have read the message. Acknowledgement
means you read it, not that the task is done. Reply with
`foil send-message`, a shared notepad, or a file.

```sh
foil message-status --state-dir STATE_DIR --fleet FLEET_ID --seat SEAT_ID --message MESSAGE_ID
foil ack-message --state-dir STATE_DIR --fleet FLEET_ID --seat SEAT_ID --message MESSAGE_ID --actor SEAT_ID
foil send-message --state-dir STATE_DIR --fleet FLEET_ID --seat LEAD_SEAT_ID --sender SEAT_ID --body "Result or question."
```

States are `queued` then `acknowledged`. Do not treat a successful wake as
acknowledgement.

## Collaboration files

Use `foil notepad-write`, `foil notepad-read`, `foil notepad-ack`, and
`foil memory-propose`. Memory accept, reject, and supersede belong to the
lead or an operator.

## Fleet membership

`FOIL_CAPABILITIES` lists what you may request. Workers may not run
`foil seat spawn`, `foil seat stop`, or `foil seat remove`. Only the lead
seat and the operator may change membership. If you need another seat, ask
the lead.

Inspect your own bootstrap with `foil seat inspect --seat SEAT_ID` only
when you need to confirm identity. Do not infer progress from tmux pane
text.

Keep a canonical checkout clean and fast-forwarded to remote main. Work
from the dedicated feature worktree for this fleet. Never mutate the
canonical checkout. If it is dirty, fail and notify rather than altering
it. An explicitly selected alternative base is allowed. This is
instruction only, not an enforcement gate.
