---
name: foil-controller
description: Operate a local Foil fleet from the CLI. Use when you must init, spawn, poll, message, resume, stop, or remove seats without a Foil UI.
---

# Foil controller

You are an outside controller. Foil does not embed a model and has no fleet UI.
Drive everything with the `foil` CLI and versioned files. If a host cannot load
this skill, use `foil --help` and `foil <command> --help`.

## Install this skill

Canonical source: `skills/controller/` in a Foil checkout. Copy or link that
directory into the host skill path. Adding a host is a mapping change, not a
Foil core change.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

Related skills: `skills/manager/`, `skills/worker/`, `skills/poll-status/`.
Adapter quirks live in `skills/adapters/`.

## State and JSON

`foil init` prints JSON with `state_root`, `fleet_id`, `roles_path`,
`lead_seat_id` (null), and `git_branch`. Copy `state_root` and `fleet_id`
into later flags. Lifecycle commands take `--state-dir` and `--fleet`.
Pass `--json` on `status`, `resume`, `seat spawn`, `seat stop`,
`seat remove`, `seat wake`, `set-state`, `dispatch`, and `doctor`. `init`,
`poll-status`, mailbox, notepad, and memory commands already emit JSON.

State precedence when `foil init` runs: `FOIL_STATE_DIR`, then the Git common
directory, then `XDG_STATE_HOME`, then the platform fallback. After init, pass
the printed `state_root` explicitly with `--state-dir`.

## Lead-owned live fleet

`foil init` accepts an empty directory or an existing Git repository. It
preserves every tracked and untracked file and all Git state, refuses a
non-empty directory outside Git, and fails closed on conflicting `.foil` or
fleet-state collisions without mutating user files. It writes a role library
and an empty live registry. No seats start. The first seat must be the lead.
The lead or an operator then spawns workers. Membership is live data
persisted only so `foil resume` can continue after interruption. After an
intentional full shutdown and `foil seat remove` of every seat, the next
fleet starts lead-only again.

```sh
foil init .
git init -b foil-demo
# copy state_root and fleet_id from the init JSON
foil seats list --json
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat lead
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat implementer
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat reviewer-challenger
foil seat list --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json
foil status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json
foil poll-status --state-dir "$STATE_DIR" --fleet "$FLEET_ID"
foil send-message --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat reviewer-challenger --sender lead --body "Challenge the current plan."
foil seat wake --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat reviewer-challenger
foil message-status --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat reviewer-challenger --message MESSAGE_ID
foil resume --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json
foil resume --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --fresh --seat implementer
foil seat stop --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat implementer
foil seat stop --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --all
foil seat remove --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat implementer
foil doctor --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json
foil dispatch --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --capability review
foil set-state --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat implementer --state waiting
foil catalog-list --path ./personas --json
foil catalog-map --path ./personas --persona "Engineering reviewer" --json
foil seats set --seat reviewer --cli opencode --role-file ./personas/reviewer.md
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat reviewer
foil seats set --seat researcher --profile ./profiles/pi-interactive.toml --role researcher
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --json --seat researcher
```

Copy `state_root` and `fleet_id` from `foil init` JSON into `$STATE_DIR`
and `$FLEET_ID`. `--seat` alone is not enough. Replace `MESSAGE_ID` from
command JSON. Do not open a Foil UI. Authenticate `grok` and `opencode`
themselves. `foil init` writes a complementary starter roster; `foil seats set`
edits it after you discover local CLIs.
Default spawn reads `.foil/seats.toml`. Persist the mapping with
`foil seats set`; `--cli`, `--model`, `--profile`, `--role`,
`--role-file`, and `--permission` on `foil seat spawn` are ad hoc
overrides. Known CLIs `grok` and `opencode` use their shipped launch,
resume, and session-capture contracts, plus a startup instruction that
tells the seat to read `{bootstrap_path}`, its sibling `FOIL.md`, and
the `role_path` recorded in `bootstrap.json`. Permission comes from the
seat file when set; otherwise `--permission supervised` asks the
provider for approvals. `--permission auto` maps to adapter-declared
flags (`--always-approve` or `--auto`). Pass extra argv after `--` only
for a custom CLI. `--role FILE_ID` resolves inside the project
`.foil/roles/` library; `--role-file PATH` staffs the seat from any
Markdown persona or role file, used untouched — find one with
`foil catalog-list` / `foil catalog-map` (which returns the persona
`path`; `cli` and `preset` stay null) and persist that path with
`foil seats set --role-file`.
A missing or invalid role file fails closed before any worktree, runner
plan, registry record, or tmux window is created. Workers are isolated by default (`worktrees/<seat_id>`).
`--shared-cwd` is the advanced override to share a directory. Uncommitted
project-root files are not copied. Isolated clones exclude `FOIL.md` and
the parent excludes `worktrees/` in Git's private exclude file, not in
the tracked `.gitignore`. `foil doctor` reports missing or stale clones
and leftover fleet files without inspecting credentials.

Keep a canonical checkout clean and fast-forwarded to remote main. Create
one dedicated feature worktree per fleet and run the lead and workers from
that fleet worktree. Never mutate the canonical checkout. If it is dirty,
fail and notify rather than altering it. An explicitly selected alternative
base is allowed. This is operator instruction, not an enforcement gate.

`foil send-message` persists mail only. Pass `--wake` or run `foil seat wake`
after inspecting tmux. Wake success is not acknowledgement.

`foil seat stop` keeps the seat record so `foil resume` can continue it.
`foil seat remove` stops the seat and deletes the record. When
`FOIL_SEAT_ID` is set, that identity is authoritative. A worker cannot
override it with `--actor operator` or `--actor <lead>`. This is
cooperative same-user protection, not hostile-process isolation.

## Mailbox: ack versus wake

`foil send-message` persists mail only. Pass `--wake` or run
`foil seat wake` if the seat is idle. The wake is a fixed Foil notice. It
does not inject the message body. `foil message-status` stays `queued`
until the seat (or an operator) writes an acknowledgement with
`foil ack-message`. Wake success is advisory and separate from
acknowledgement.

```sh
foil ack-message --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat SEAT --message MESSAGE_ID --actor ACTOR
```

## Resume precedence

`foil resume` applies this order per seat: matching live tmux → `revive_tmux`;
recorded native session ID → `resume_native`; otherwise logged `start_fresh`.
Pass `--fresh` (optionally with `--seat`) to skip live tmux and native resume
and mint a new `incarnation_id` linked by `previous_incarnation_id`.
Use `foil seat stop` to stop verified tmux windows. Do not kill a tmux target
by display name.

## Status

`foil status --json` reconciles registry, structured native IDs, and tmux
liveness. Live projection is `working`, `exited`, `blocked`, plus `waiting` or
`idle` after `foil set-state`. `foil poll-status` reads status files only; see
`skills/poll-status/`. Do not infer seat state from tmux pane text.

## Collaboration files

Use `skills/shared-notepads/` and `skills/memory-update/` for `foil notepad-write`,
`foil notepad-read`, `foil notepad-ack`, `foil memory-propose`,
`foil memory-accept`, `foil memory-supersede`, `foil memory-reject`, and
`foil memory-status`.
