---
name: foil-manager
description: Staff and run a complementary Foil fleet from the outside. Use when decomposing work, choosing seats, polling progress, or intervening without a Foil UI.
---

# Foil manager

You remain an ordinary user-selected agent. Foil does not embed a supervisor
model. Every management action is a `foil` command or a file under the state
root. If this skill is unavailable, use `foil --help` and `skills/controller/`.

## Install this skill

Canonical source: `skills/manager/`. Copy or link that directory into the host
skill path.

| Host | Project skill location | Always-on fallback |
|---|---|---|
| Cursor | `.agents/skills/` | `AGENTS.md`, Cursor rules |
| Claude Code | `.claude/skills/` | `CLAUDE.md` |
| Codex CLI | `.agents/skills/` | `AGENTS.md` |
| Gemini CLI | `.agents/skills/` | `AGENTS.md` |
| OpenCode | `.opencode/skills/` | `AGENTS.md` |

Use `skills/controller/` for command flags, `skills/worker/` for seat duties,
and `skills/poll-status/` for status files versus live tmux.

## Staffing as it exists today

`foil init` writes eight role files under `.foil/roles/`, a complementary
starter `.foil/seats.toml` recipe, and an empty live fleet. Roles are
templates, not running seats. Do not claim eight seats are running.

Every fleet starts with one lead. Init already persisted a starter
mapping. Edit CLI, model, persona, and permission with `foil seats set`
after discovery, then spawn from that file. Later commands default to
this Git project's Foil state and its only live fleet. Pass
`--state-dir` or `--fleet` only to override.
The operator or the lead then spawns workers. Live membership is
persisted only so `foil resume` can continue after a crash or tmux
death. Do not treat the registry as a recipe to replay tomorrow.

Known CLIs `grok` and `opencode` carry shipped launch, resume, and
session-capture contracts. A custom seat may pass argv after `--`. Do not
add a Python adapter. Do not teach a core code change as the way to support
another CLI.

Typical complementary pair after the lead exists:

| Seat | Role template | CLI | Isolated |
|---|---|---|---|
| `lead` | `manager` | `grok` | no |
| `implementer` | `implementer` | `grok` | yes |
| `reviewer-challenger` | `reviewer-challenger` | `opencode` | yes |

Keep complementary seats on different CLIs when capacity allows. Challenge
edges in role files are plan data, not a runtime enforcer.

Keep a canonical checkout clean and fast-forwarded to remote main. Create
one dedicated feature worktree per fleet and run the lead and workers from
that fleet worktree. Never mutate the canonical checkout. If it is dirty,
fail and notify rather than altering it. An explicitly selected alternative
base is allowed. This is operator instruction, not an enforcement gate.

## Duties with shipped commands

1. Turn the user objective into a testable brief. Put the brief in
   `foil send-message` bodies, not in tmux keystrokes.
2. Edit the starter mapping with `foil seats set` if discovery says so,
   then spawn the lead with `foil seat spawn --seat lead --json`.
   Flags on spawn are overrides. Pass `--state-dir` or `--fleet` only
   to override the project default.
   Pass `--permission auto` only when the operator wants the provider
   to approve ordinary tool use. Workers isolate by default. Use
   `--shared-cwd` only when two seats must share a directory.
   Confirm with `foil seats list --json`, `foil seat list --json`,
   `foil status --json`, and `foil poll-status`.
3. Assign work by messaging a seat (`foil send-message`, then `--wake` or
   `foil seat wake` if the seat is idle). Independent review belongs on
   `reviewer-challenger`, not on the same seat that implemented the change.
4. Poll files and acknowledgements. Do not infer progress from pane text.
5. Intervene with another `foil send-message`, or `foil seat stop` /
   `foil resume` when a seat is dead or blocked. Do not retry blindly.
6. `foil resume` follows live tmux, then native session, then `start_fresh`.
   Request a clean context with `foil resume --fresh --seat SEAT`. The new
   record stores `previous_incarnation_id`.
7. Share a brief with `foil notepad-write` / `foil notepad-read` /
   `foil notepad-ack`. Keep reviewed lessons with `foil memory-propose` and
   manager `foil memory-accept` / `foil memory-reject` / `foil memory-supersede`.
8. Rank a live seat with `foil dispatch --capability`. The probe is a
   non-interactive `usage --format json` command, not slash `/usage` in a TUI.
9. Check the machine with `foil doctor --json`. Pass `--apply` to create
   missing isolated clones for seats that already exist.
10. Stop a seat without forgetting it (`foil seat stop`). Delete a seat with
    `foil seat remove`. A worker with `FOIL_SEAT_ID` cannot change
    membership, even with `--actor operator` or `--actor <lead>`.
11. Synthesize from mailbox bodies, notepad JSON, memory lessons, status JSON,
    and seat output files.

## Local persona catalog

`foil catalog-list --path` and `foil catalog-map --path --persona` read a *local*
Markdown catalog with YAML `name` / `description` frontmatter. Mapping yields
display name, specialization, usage pool, and the persona `path`. `cli` and
`preset` stay null; persist CLI staffing with `foil seats set`. This skill
does not parse, download, or vendor remote catalog files. Do not treat any
GitHub URL as exclusive. Do not claim browser or UI features.

A persona file staffs a seat directly, untouched and without an English
wrapper: persist the mapped `path` with `foil seats set --role-file PATH`,
then `foil seat spawn --seat ID`.
The file must exist and be a regular
file; a missing or invalid role file fails closed before any worktree,
runner plan, registry record, or tmux window is created. Generated seat
instructions require the seat to read that validated `role_path` and
apply it as a specialist lens scoped to the assignment.

## Profile-owned CLI

A seat may declare workspace, `cli`, and argv directly after `--`. Known
`grok` and `opencode` names use shipped contracts when no extra argv is
given. Do not add a Python adapter module.

For anything more durable, `foil seat spawn --profile PATH` loads a
declarative schema-v1 TOML that owns the executable candidates, launch,
startup/bootstrap delivery, resume argv, session capture, permission-mode
flags, workdir isolation default, and safe environment forwarding (variable
names only — values resolve from the host environment at launch and are
never persisted or printed). Credential-shaped argv is rejected before any
artifact is written. Remainder argv after `--` still wins over the file's
launch argv. Interactive CLI profiles omit one-shot print flags such as
Pi's `-p`. The full contract, migration notes, and limitations live in
docs/design/profiles.md.
