# Foil v0.2.0 specification

This document defines Foil v0.2.0: what it does, how it is structured,
and how to verify it. It replaces the v0.1.x requirements, design docs,
and ADRs. Where this spec is silent, the implementer chooses, **except**
that no command, flag, or feature beyond those listed here may be added
(see section 11).

## 1. Summary

Foil is a small command-line tool for running a fleet of CLI coding
agents in tmux. A human works through their own agent harness (the
*operator*), which uses Foil to start a *lead* agent. The lead spawns
and manages worker agents from role templates. Agents talk through short
messages and shared files. Anyone can glance at an agent's screen, but
Foil never tries to interpret it.

## 2. Glossary

| Term | Meaning |
|---|---|
| Operator | The human's own harness, outside the fleet. Not a seat. Foil sees it only as "a caller outside the fleet". |
| Fleet | All seats Foil runs for one project. One fleet per project. |
| Seat | One agent process in its own tmux window. |
| Lead | The one seat allowed to spawn and kill other seats. |
| Worker | Any seat that isn't the lead. |
| Template | A role definition: which harness, model, persona, worktree, and permission mode. |
| Harness preset | How to launch one agent CLI (claude, codex, ...). |
| Board | A shared folder of files all seats can read and write. |
| Mail | A message stored as one file on the board. |
| Nudge | The one line Foil types into a seat's pane to announce new mail. |
| Peek | Printing the raw last lines of a seat's pane. |
| Contract | A board file with an agreed, versioned format. |

## 3. Workflow

```text
human ──▶ operator (outside the fleet)
             │  foil init · foil spawn lead · foil send lead · foil status --peek
             ▼
        ┌──────────── tmux: one fleet ─────────────┐
        │  lead ──spawn/kill──▶ implementer-1       │
        │   │                   implementer-2       │
        │   └──send──▶          reviewer-1          │
        └───────────────────────────────────────────┘
             all seats read/write the board
```

1. The human asks the operator for something.
2. The operator runs `foil init` once per project, then
   `foil spawn lead --task "<goal>"`.
3. The lead receives the goal as mail and plans the work.
4. The lead spawns workers from templates and kills them when they're
   done. Seats whose template asks for a worktree get their own git
   worktree and branch.
5. Seats send short messages with `foil send` and put anything durable
   (task briefs, results, reviews, status) on the board as contract files.
6. The operator polls: it peeks at seats, reads the lead's status file,
   passes the lead's questions to the human, and sends the human's
   answers to the lead. It does no project work. The lead doesn't know an
   operator exists; it acts as if a human is talking to it.
7. The lead integrates the workers' branches and reports in its status
   file.
8. After a crash or reboot, `foil resume` restarts dead seats.

## 4. Requirements

### Functional

| ID | Requirement |
|---|---|
| F1 | Foil has exactly six commands: `init`, `spawn`, `kill`, `send`, `status`, `resume` (section 6). |
| F2 | The template named `lead` defines the lead, and its seat is always named `lead`. A fleet has at most one live lead, and the first seat spawned must be the lead. A new lead may be spawned only after the previous one was killed. |
| F3 | Only the lead or a caller outside the fleet may `spawn` or `kill`. Workers may only `send` and `status`. See the authority table in section 6. |
| F4 | A seat's identity comes from an environment variable Foil sets when launching it. A seat can't claim another identity through any flag. This only stops cooperating agents, and the docs must say so. |
| F5 | `spawn` takes a template name. Worker names are auto-numbered from the template name unless `--name` is given. A worker name is never reused within a fleet, including names of killed seats. |
| F6 | When a template asks for a worktree, `spawn` creates a git worktree on a new branch whose name is unique in the repository. `kill` never deletes that branch, and never silently destroys uncommitted work. |
| F7 | Foil-created files and worktrees never show up as untracked or modified files in the user's repository. |
| F8 | `send` stores the message as a mail file, then types one nudge line into the recipient's pane containing the sender and the mail file's absolute path. The message body is never typed into the pane. |
| F9 | Mail sent from outside the fleet shows the sender as `user`. There is no operator mailbox or seat. |
| F10 | `status` reports each seat's name, template, state (`alive`, `dead`, or `killed`), and worktree. `--peek N` adds the raw last N lines of each pane. |
| F11 | `alive` means the seat's exact tmux window exists; `dead` means it doesn't and the seat wasn't killed; `killed` means `foil kill` stopped it. Foil never parses, classifies, or interprets pane contents. |
| F12 | `resume` restarts `dead` seats (never `killed` ones). It uses the harness's own session resume when the preset supports it; otherwise it starts the seat fresh and tells it that it was restarted and should re-read its mail. |
| F13 | Each seat gets a generated instruction file (FOIL.md), and its launch prompt tells it to read that file first. The file contains: its name, the lead's name, the board path, the commands it may run (exact syntax), the contract formats, and, for the lead only, the list of available templates. |
| F14 | `init` creates the templates folder with default templates (at least lead, implementer, reviewer), each with a real persona prompt. It never overwrites existing templates. |
| F15 | Harness presets ship for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake` (test double, section 9). Users can add their own presets in the same format. |
| F16 | Three skills ship: `operator` (for the human's harness), `lead`, and `worker`. |

### Non-functional

| ID | Requirement |
|---|---|
| N1 | Python 3.11+ with the standard library only; no runtime dependencies. |
| N2 | External programs are limited to tmux 3.2+, git, and the harness CLIs. |
| N3 | Supported platforms: Linux and macOS. |
| N4 | At most about 2,000 lines of Python in the package source. |
| N5 | Foil never stores, prints, or logs credentials. Environment variables are forwarded to seats by name only. |
| N6 | Tmux windows are always targeted by exact window ID, never by name. |
| N7 | Foil's own state is written atomically and carries a schema version. |
| N8 | Every error is a one-line message on stderr with a non-zero exit code. No Python tracebacks. |
| N9 | The repository stays safe to publish: no secrets, personal paths, or private hosts in any tracked file. The existing public-safety tests stay and pass. |
| N10 | Package version is 0.2.0. |

## 5. Architecture

| Component | Responsibility | Source |
|---|---|---|
| CLI | Parses the six commands and enforces the authority table. | New |
| Registry | Foil-owned fleet and seat records (versioned JSON). | Reduce `src/foil/registry.py` |
| Tmux layer | Launch a marked window, probe by exact ID, kill a verified window, type one line, capture the pane tail. | Reuse `src/foil/tmux.py`, `src/foil/delivery.py` |
| Launcher | Execs a harness command without a shell, with forwarded env. | Reuse `src/foil/runner.py` |
| Config loader | Reads templates and harness presets. | Replaces the adapter, profile, and seat-roster modules |
| Worktrees | Create and clean up per-seat git worktrees. | New (small) |
| Board | Board folder layout and atomic mail writes. | Replaces the mailbox module |
| Instructions | Generates each seat's FOIL.md. | New (small) |
| Skills | Operator, lead, and worker instructions (Markdown). | New |

Data flow: CLI → config loader → (worktrees) → launcher inside a tmux
window → registry. `send` → board (mail file) → tmux layer (nudge).
`status` → registry + tmux probe (+ capture for peek).

## 6. Command reference

These are the only commands and flags. `--help` and `--version` also exist.

| Command | Flags | Behavior |
|---|---|---|
| `foil init [DIR]` | none | Checks that tmux and git exist and DIR (default: current directory) is a git repository. Creates the Foil folder, default templates, and the git ignore entry. Safe to re-run. |
| `foil spawn TEMPLATE` | `--name NAME`, `--task TEXT` | Creates a seat from the template (F2, F5, F6, F13). `--task` is delivered as the seat's first mail. |
| `foil kill NAME` | `--all` | Stops the seat's window and marks it `killed` (F6). `--all` stops every seat and is allowed only from outside the fleet. |
| `foil send TO TEXT` | none (`TEXT` = `-` reads stdin) | Writes mail and nudges the recipient (F8, F9). Unknown recipient → clear error. |
| `foil status [NAME]` | `--peek N`, `--json` | Reports seats (F10, F11). |
| `foil resume [NAME]` | none | Restarts `dead` seats (F12). |

Authority:

| Caller | init | spawn | kill | kill --all | send | status | resume |
|---|---|---|---|---|---|---|---|
| Outside the fleet | yes | yes | yes | yes | yes | yes | yes |
| Lead | no | yes | yes (not itself) | no | yes | yes | yes |
| Worker | no | no | no | no | yes | yes | no |

## 7. Data design

Everything lives in one Foil folder inside the project, which is
git-ignored (F7). Suggested layout (the implementer may adjust it, but
the concepts are fixed):

```text
<foil folder>/
  templates/<role>.toml        user-editable role templates
  harnesses/<id>.toml          optional user harness presets
  board/
    mail/<seat>/<time>-<from>-<rand>.md
    status.md                  lead's status (contract status/v1)
    tasks/<id>.md              contract task/v1
    results/<id>.md            contract result/v1
  run/                         Foil-owned state (registry, FOIL.md files)
```

**Template** (`<role>.toml`; the file name is the role):

| Field | Required | Meaning |
|---|---|---|
| `harness` | yes | Preset id. |
| `model` | no | Passed to the harness's model option. |
| `persona` | no | Inline text, or a path to a Markdown file used untouched. |
| `worktree` | no, default `false` | Give each seat its own worktree and branch. |
| `permission` | no, default `ask` | `ask` or `auto` (maps to the preset's flags). |

**Harness preset:**

| Field | Meaning |
|---|---|
| `id` | Preset name. |
| `command` | Launch argv. Placeholders: `{model}`, `{prompt}`, `{session_id}`. |
| `permission.ask`, `permission.auto` | Extra argv for each mode. |
| `resume` | Optional resume argv (uses `{session_id}`). |
| `session_id` | `generated` (Foil makes a UUID and passes it at launch) or `none`. |
| `env` | Names of environment variables to forward. |

Built-in presets must use each CLI's documented flags. Any flag that
couldn't be verified is marked as unverified with a comment in the preset
file.

**Mail file:** Markdown with front matter `contract: mail/v1`, `from`,
`to`, `time` (UTC, ISO 8601), optional `re` (the mail file it answers),
then the body. It's written atomically and file names never collide.

**Contracts** (Markdown with front matter; Foil only defines them, it
never reads them):

| Contract | Front matter |
|---|---|
| `status/v1` | `state: working \| blocked \| done`, `updated`, `questions` (list, may be empty) |
| `task/v1` | `id`, `owner`, `state: open \| doing \| done`, `acceptance` |
| `result/v1` | `task`, `author`, `branch` (if any), `outcome: pass \| fail` |

**Registry** (Foil-owned JSON, schema-versioned): fleet id, tmux session,
lead name, and per seat: name, template, harness, tmux window id, state, worktree path, branch, session id. State from v0.1.x is not
migrated.

## 8. What to remove

- **Commands:** everything except the six in section 6. That includes
  `seat` and `seats` with their subcommands, `send-message`,
  `ack-message`, `message-status`, `notepad-*`, `memory-*`,
  `poll-status`, `set-state`, `dispatch`, `doctor`, and `catalog-*`.
- **Modules** with no remaining use: memory, notepad, dispatch, catalog,
  doctor, status reader, seat roster, onboarding, and the old adapter
  and profile system, including its JSON schemas.
- **Skills:** controller, manager, poll-status, memory-update,
  shared-notepads, and the adapter skills.
- **Docs:** the spec, ADR, assignments, design, walking-skeleton, and
  live-exam docs. This specification becomes the single design document.
- **Tests** for removed features. Keep the tmux-identity, atomic-write,
  and public-safety tests.

Removal is part of the work, not a follow-up: the finished v0.2.0 must
not contain the old command surface alongside the new one.

## 9. Test design

Principle: **real Foil, real tmux, real git, a small real task; only the
agent is replaced.** Assertions check real outcomes (tests pass, a
branch exists, a window is gone), not Foil's own reports about itself.

**Fake harness** (the `fake` preset) is a test double for an agent CLI:

- It runs interactively in its pane and reads typed lines, like a real TUI.
- When a nudge line arrives, it reads the mail file it names.
- It follows a per-seat script, in a format the standard library can
  parse. Reactions include: run a shell command (e.g. `foil spawn`,
  `git commit`, the fixture's test runner), write a board file, and
  `foil send` a reply.
- It's idempotent across restarts: on start it handles any mail it hasn't
  handled yet.
- It keeps a debug log. Tests never assert on that log.

It's launched through the normal preset path, so templates, launch,
environment, FOIL.md, worktrees, and nudges are exercised exactly as with
a real CLI.

**Scenarios** (each is a fixture repository plus seat scripts; the test
plays the operator):

1. **Happy path.** The fixture has one failing test. The test runs `init`
   and `spawn lead --task "make the tests pass"`. The lead spawns an
   implementer and a reviewer. The implementer commits a fix on its
   branch and mails the lead. The reviewer runs the tests on that branch
   and mails approval. The lead merges and sets `status.md` to done.
   *Assert:* the tests pass on the merged result, the branch is merged,
   `status.md` says done, and `kill --all` leaves no Foil windows.
2. **Authority.** A worker's script tries `spawn` and `kill lead`.
   *Assert:* both fail, and the fleet is unchanged.
3. **Crash.** Kill the tmux server mid-scenario and run `resume`.
   *Assert:* the happy-path outcome is still reached.
4. **Operator loop.** The lead's script writes a question into
   `status.md`; the test answers with `foil send lead`. *Assert:* the
   lead's final status reflects the answer, and `status --peek` shows raw
   pane text.
5. **Errors.** Sending to an unknown seat, spawning a missing template,
   and running outside a git repository each give a one-line error and a
   non-zero exit, with no traceback.

**Live tier (optional):** the same scenarios with real harness CLIs in
place of `fake`, judged by the same assertions. It needs authenticated
CLIs, so it's opt-in and excluded from the default test run.

## 10. Definition of done

- [ ] `foil --help` lists exactly the six commands, with only the flags in section 6.
- [ ] Every requirement in section 4 is met.
- [ ] Scenarios 1–5 pass using the `fake` preset.
- [ ] Public-safety, tmux-identity, and atomic-write tests pass; lint is clean.
- [ ] Package source is at most about 2,000 lines of Python (N4).
- [ ] Three skills exist and match the command reference exactly.
- [ ] The README (English and Chinese) describes the section 3 workflow,
      shows a quick start with a mainstream harness, and states the
      cooperative-only authority limit (F4) and the `auto` permission risk.
- [ ] No removed command, module, skill, or doc remains (section 8).
- [ ] Package version is 0.2.0.

## 11. Out of scope

- Any command or flag not in section 6. A change requires updating this spec first.
- Parsing or interpreting pane contents (F11).
- More than one fleet per project, remote machines, and Windows.
- A UI, a server, MCP, or a hosted service.
- Memory curation, notepads, usage-based dispatch, persona catalog tools,
  and message acknowledgement.
- Migrating v0.1.x state.

## 12. Known issues in v0.1.1

These are for reference. Each should be gone or re-checked once v0.2.0
is done.

| # | Issue | Expected after v0.2.0 |
|---|---|---|
| 1 | A woken seat can't find its mail: `message-status` needs a message ID the seat doesn't have. | Gone (F8: the nudge carries the path) |
| 2 | Sending to a non-seat (e.g. `operator`) crashes with a Python traceback. | Clear error (N8) |
| 3 | The lead's FOIL.md says it owns spawning but gives no commands or roster. | Fixed (F13) |
| 4 | `seats set --profile` fails on starter seats with a CLI conflict. | Gone (command removed) |
| 5 | `seats set` has no authority check. | Gone (command removed) |
| 6 | Only `grok` and `opencode` presets. | Fixed (F15) |
| 7 | Design docs contradict the README about required flags. | Gone (docs replaced) |
| 8 | Seat isolation is a `git clone --local` on the main branch, not a worktree on its own branch. | Fixed (F6) |
| 9 | Seat instructions say "one worktree per fleet", contradicting per-seat isolation. | Fixed (F13) |
| 10 | The nudge is typed even when the pane isn't at an input prompt. | By design: Foil types, the harness decides; document it |
| 11 | The Chinese README is maintained separately and drifts. | README requirement in section 10 |
