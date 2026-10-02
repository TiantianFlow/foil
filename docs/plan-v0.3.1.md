# Plan: Foil 0.3.1

**Goal:** the operator sees the roster and the live fleet in full from two
commands it already runs, and a seat that starts behind a harness dialog is
found by the fleet and costs the human one answer per harness, not one per
seat (issue #15).

Issues #14 and #17 ship in the same release. They are summarized under
"Also in 0.3.1".

## Operator view

Today `foil roster list` prints `<role> [<harness>/<model>]` and
`foil seat list` prints name, template, state, and worktree. Neither shows
what a role is for, and `seat list` does not show which harness and model a
seat runs. The operator opens template and persona files by hand to find
out.

**Decision: no new command, subcommand, or flag.** The two list commands
carry the missing facts. Section 6 keeps seven top-level commands and
twenty actions. A combined command, a watch flag, and a UI were rejected:
section 11 excludes a UI, and the repeated look belongs to the operator's
check-in loop, which already runs these commands.

**Description.** A role's description is the first line of its persona text
that is neither blank nor a Markdown heading, trimmed. Every packaged
persona opens with a heading that repeats the role name, so the heading
would add nothing; the line after it says what the role does. The
description is empty when there is no such line, the template is invalid,
or the persona cannot be read (F22). One such role never makes a list fail.

**Preset source.** A template names a harness id. How that harness launches
(argv, permission flags, resume) is the preset, not the roster. `roster
list` shows each template's preset source: `builtin`, `user` when
`.foil/harnesses/<id>.toml` exists, or `invalid` when the id is unknown or
its preset does not load. Editing a template never changes a preset, and one
preset serves every template that names its id.

**Model at launch.** The registry already stores the harness a seat was
launched with. It also stores the model, at spawn and at resume, so `seat
list` reports what the seat runs and not what the template says now. A seat
record written before this release reads the missing field as empty (the
loader already does this), so the registry schema version does not change.

| Command | Human output | JSON |
|---|---|---|
| `foil roster list` | `<role>\t<harness>[/<model>]\t<preset>\t<description>`, then `+ <persona>\t<description>` | each template adds `preset` and `description`; `personas` becomes `[{"name","description"}]` |
| `foil roster show ROLE` | adds `preset:` and `description:` lines | adds `preset` and `description` |
| `foil seat list` | `<name>\t<template>\t<state>\t<worktree>\t<harness>[/<model>]\t<description>` | each row adds `harness`, `model`, `description` |

`personas` changing from strings to objects is a breaking change to the
0.3.0 JSON and goes under "Changed" in the changelog. The new `seat list`
columns are appended, so the first four stay where they are. A seat's
description is its template's current description, and is empty when that
template is gone.

## Startup dialogs

What the harnesses do, checked on macOS:

| Harness | Version | Finding | Checked |
|---|---|---|---|
| Claude Code | 2.1.286 | Asks once per folder. After the repository root is trusted, a linked worktree under `.foil/worktrees/` starts with no prompt. | yes |
| Claude Code | 2.1.286 | The trust dialog defaults to "No, exit". A nudge line followed by Enter, typed into that dialog, chose it, and the CLI exited. | yes |
| Codex | 0.147.0 | An update dialog comes first and defaults to "Update now", which runs an installer. The trust dialog defaults to "Yes, continue". | yes, not answered |
| Codex | 0.147.0 | Trust resolves to the repository root, including linked worktrees ([openai/codex#2585](https://github.com/openai/codex/pull/2585)). | docs only |
| Grok | 1.0.5 | Folder trust gates project hooks and repo-local MCP and LSP servers. An untrusted folder skips them; it does not stop the session. | docs only |
| Gemini, OpenCode | | not checked | no |

Two things follow. Trust is needed once per harness per repository, not once
per seat, because every seat runs in the repository root or in a linked
worktree under it ([anthropics/claude-code#23109](https://github.com/anthropics/claude-code/issues/23109)).
And today Foil types keys into a pane that may be showing one of these
dialogs: `seat spawn --task` nudges the new pane within milliseconds of
launch. Depending on the harness, that Enter exits the seat, grants folder
trust the human never saw, or starts an update.

**Decisions:**

1. **Spawn types nothing into a new pane.** `--task` is written as the
   seat's first mail before the harness starts, with no nudge. The first
   launch prompt names that mail file and tells the seat to read it first.
   `seat kill` keeps the mailbox and the lead is always `lead`, so mail from
   an earlier goal must not read as the new task. Without `--task`, the
   prompt tells the seat to run `foil mail list` and read its mail. A seat
   behind a dialog now waits there, alive, with its task on the board.
   `foil send` is unchanged (F13).
2. **Trust once per harness, before the first spawn.** The operator runs
   `foil roster list`, names each distinct harness id the roster uses, and
   asks the human to run each of those once in the repository root and
   accept its trust, update, first-run, and opt-in dialogs, or to confirm
   that is done. It asks again when a role moves to a harness not on that
   list.
3. **The lead watches the seats it starts.** After each spawn or resume, the
   lead waits for that seat's first mail or board file. If none comes in a
   few minutes, it peeks the seat. It answers no dialog. It sets
   `state: blocked` with a question that names the seat, its harness, and
   what the pane shows, and sends that seat nothing until the human answers.
   The operator finds it in `status.md` at its normal check-in and does not
   peek workers.
4. **Recovery.** The human trusts the repository in that harness from their
   own terminal. The lead then kills the stuck seat and spawns a new one.
   Later seats on that harness start without the dialog.

**Rejected:**

- Foil or an agent answering a dialog. It needs a command that types keys,
  and Foil cannot tell what the pane shows (F12). The defaults above show
  that a blind Enter can exit, grant trust, or install software.
- Foil writing a harness's trust store in the user's home directory. That
  is the harness's security decision and its file format, outside the
  project.
- A preset field for a trust flag. Only Grok has one (`--trust`), and Grok
  does not stop at an untrusted folder.

## Requirements to edit

All in `docs/requirements.md`, in the same change as the code:

- **Onboarding step 1:** trust is per harness per repository; seat
  worktrees inherit it; the agent lists the roster's harness ids and asks
  once per id, and again when a role moves to a new id; update dialogs join
  the list of dialogs.
- **Onboarding step 7:** a seat at a startup dialog waits there; spawn
  types nothing into its pane.
- **F10:** `seat list` adds the harness and model the seat was last
  launched with, as the registry recorded them, and its template's
  description.
- **F21:** define the description and the preset source; `list` and `show`
  report both; a role whose description cannot be read never makes `list`
  fail.
- **F24:** with `--task`, the first launch prompt names the task's mail
  file and says to read it first; without it, the prompt tells the seat to
  run `foil mail list` and read its mail before it acts.
- **Section 6.1:** the `seat spawn`, `seat list`, `roster list`, and
  `roster show` rows, with the output above. The counts do not change.
- **Section 7.5:** the registry seat record adds `model`.
- **Section 8:** operator (the per-harness trust step, `seat list` columns,
  blocked seats come from `status.md`), lead (watch new and resumed seats,
  report a dialog as blocked, kill and respawn after the human answers),
  worker (the `seat list` columns).

## Other changes

- `src/foil/lifecycle.py`: spawn writes the task mail before launch and
  does not nudge, records `model`, and `list_seats` adds the columns. If the
  launch fails, the mail file is removed.
- `src/foil/board.py`: writing a mail file is callable without a nudge and
  without a registry entry.
- `src/foil/roster_ops.py` and `src/foil/presets.py`: one description
  helper and one preset-source helper, used by `list`, `show`, and
  `seat list`.
- `skills/` and `src/foil/defaults/skills/`: the operator, lead, and worker
  skills, kept identical.
- `README.md` and `README.zh-CN.md`: the prerequisites paragraph says trust
  is per harness per repository.
- `docs/architecture.md`: spawn order and the registry field.
- `CHANGELOG.md`: entries in the 0.3.1 section.
- Tests: the fake harness gains a script option to exit on the first line
  typed into its pane, standing in for a startup dialog.

## Size

The package source is 2,793 lines on the base, over the N4 target of
2,500. This plan adds at most 60 net lines. The persona listing exists three
times (`presets.py`, `roster_ops.py`, and the init report in
`lifecycle.py`) and `_shown` exists three times; merging those comes first
and pays for part of it. The changelog states the remaining reason.

## Done when

1. `roster list` and `roster show` print the description and the preset
   source in both modes, and a template with a missing persona file or an
   unknown harness still lists, with an empty description or `invalid`.
2. `seat list` prints harness, model, and description in both modes, and an
   older registry with no `model` field still loads.
3. A fake seat set to exit on any typed line, spawned with `--task`, stays
   `alive` and completes the task. Reverting the spawn change makes that
   seat `dead`.
4. Requirements, the three skills, both READMEs, and the command-surface
   test agree.
5. The suite passes.

The live check is by hand and not in CI: spawn a Claude Code seat with
`--task` in a fresh repository that Claude has not trusted, and confirm
that `seat list` shows it `alive` and `seat peek` shows the dialog. Never
send Enter to a Codex pane that shows the update dialog.

## Out of scope

- A combined status command, a watch flag, or any UI.
- Detecting or answering a dialog, from Foil or from a seat.
- Writing any harness's trust or config files.
- Printing the tmux session so the human can answer inside a seat's pane.
- Descriptions in the init report or in the lead's instruction file.
- A later `foil send` that lands on an approval prompt mid-task. That is
  unchanged.
- Trust behavior of Gemini and OpenCode.
- Any model or vendor preference. F28 is unchanged.

## Also in 0.3.1

**Upgrading between goals (issue #14).** `foil init` prints the version
first, replaces each of the three skills atomically when its bytes differ
from this version, refuses a symlink, and reports `Updated skills: …` or
`Skills: current`. It still overwrites no template, persona, or preset, and
it names a built-in preset that a file in `.foil/harnesses` overrides. Each
instruction file names the Foil version that wrote it. The operator skill
and both READMEs say to install or update, and how to upgrade between goals:
`foil seat kill --all`, update, `foil init`, then spawn the lead, checking
`tmux ls` for where a preset's `env` value comes from. The session name,
the tmux markers, and registry schema 1 are a cross-version contract that
`tests/test_upgrade_compat.py` pins. The `model` field above stays within
it: an older loader ignores the field, and this loader reads an older record
with `model` empty.

**Persona detail (issue #17).** Each packaged persona says what the role is
for and adds only judgment its skill does not already state. The reviewer persona keeps the rule against editing
files. `init` never overwrites a persona, so a project that already ran it
keeps its old files.
