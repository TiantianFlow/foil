# Changelog

Notable changes to Foil, newest first.

## Unreleased

### Added
- `foil mail read`, `foil mail list`, `foil board read`, and `foil board list`.
  A seat in a worktree can read mail and board files without a permission
  prompt. `mail read` takes an absolute path. JSON mail includes the
  `mail/v1` contract. JSON board output keeps list front matter as arrays. (#12)

### Changed
- Skills and seat instructions use `foil mail read` and `foil mail list`
  instead of direct mail file reads.
- `foil mail list` rejects an unsafe seat id. An outside-fleet caller
  gets `foil: not allowed`.

## [0.2.2] - 2026-09-30

`foil --version` prints 0.2.2.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.2.md](docs/plan-v0.2.2.md) is the plan for this release.
The lead keeps a checklist in `status.md` of what is done and what is
left, and the operator reports that checklist. No command, flag, or
contract version was added.

### Changed

- Once the lead has a plan, the `status/v1` body holds a `## Checklist` of `- [ ]` and `- [x]` items. An item starts with its task id when it has one. `state: done` means every item is ticked. The front matter is unchanged, and Foil does not read the body.
- The lead skill says how to keep the checklist. The operator skill says to report it at each check-in and whenever the human asks, quoting the file rather than the pane.
- Both READMEs and both demos describe the checklist. The scenario 1 status sample includes a ticked checklist.

### Upgrading

`foil init` does not replace an existing project's `.foil/skills/`. A project upgraded from 0.2.1 to 0.2.2 keeps the old lead and operator skills, so it does not get the checklist. Move `.foil/skills/lead.md` and `.foil/skills/operator.md` aside, or delete them if they were never edited. Run `foil init` again, and re-copy any user-level copy of the operator skill. New and resumed seats then get the new text.

## [0.2.1] - 2026-09-29

`foil --version` prints 0.2.1.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.1.md](docs/plan-v0.2.1.md) is the plan for this release.
Onboarding is a sentence the human gives a coding agent. The agent
installs Foil, reads the `foil init` report, and starts the fleet.
No command or flag was added.

### Added

- `foil init` reports every installed eligible harness, in id order. The order is ascending Unicode code point of the id, case preserved: a tiebreak, not a ranking. `fake` is never eligible. A user preset that reuses a built-in id counts once. `lead` and `implementer` get the first installed id. `reviewer` gets the second when one exists, and the first otherwise. The report counts the distinct harness ids stored in the three templates. Two ids can still run the same program or model; set model on a template to choose one. It names what was written and what was left alone, and explains `ask` and `auto`. One invalid user preset is skipped and named; it does not abort `init`.
- Candidate personas: `documentation-writer`, `domain-designer`, `memory-curator`, `researcher`, and `verifier`. `init` copies every packaged persona and still writes only the three default templates. It overwrites nothing.
- Seats inherit `HOME` as well as `PATH`. A preset's `env` still names any further variables. The value is forwarded at launch and is not stored in the plan.
- A seat's instruction file includes its persona's text.
- The operator skill describes the template fields, the candidate catalog, and a few principles for picking a harness and a model. It tells the operator to peek after a resume, not only after a spawn.

### Changed

- A seat whose persona file is missing fails to spawn or resume with an error that names the file. `foil seat resume` with no name names each seat it could not restart, restarts the others, and exits 1.
- A fresh `init` used to pick `grok` first when several CLIs were installed. It now picks the first id in code-point order, so `claude` is first when it is installed. Re-creating the templates uses that new order.
- Both READMEs lead with the one sentence the human gives their coding agent. The by-hand commands stay as a second section. The agent asks the human to run each harness once in this repository and accept its folder-trust prompt as well as its first-run and opt-in dialogs, because trust is per folder, or to confirm that is already done, before the first spawn.
- [CONTRIBUTING.md](CONTRIBUTING.md) has a self-review to run before asking for review, and a new [AGENTS.md](AGENTS.md) points coding agents to it.
- Earlier documentation that had not been released: both READMEs carry the product's two names, the "loyal opposition" tagline, three problems Foil solves compared with a single agent session, a labeled diagram, how Foil is built, and badges for Python, platforms, and runtime dependencies. A demo per language ([docs/demo.md](docs/demo.md), [docs/demo.zh-CN.md](docs/demo.zh-CN.md)). [docs/architecture.md](docs/architecture.md) matches the scan, the persona copy, the inlined persona, and the launch environment.

### Known issues

- Claude Code, and any CLI that asks whether to trust a folder, waits on that prompt in a folder it has not trusted, and a waiting seat looks like a working one. Run the CLI once in the repository and accept the prompt before the first fleet.
- The live tier, pointed at Codex, passed two of six scenarios. The other four stopped on Codex's workspace-trust prompt in a fresh directory.
- The Claude Code operator-skill path (`~/.claude/skills/foil-operator/SKILL.md`) has not been checked by hand. The table still says no. The pointer line is the path that does not depend on that check.
- A harness's own first-run or opt-in dialog can still stop a seat, including after `foil seat resume`. The docs now say to dismiss those dialogs before the fleet starts, and to peek after a resume.
- With `permission = "auto"`, a Claude Code seat can still stop on Claude Code's own command-approval prompt. Peek new seats, and answer the prompt in that window.

## [0.2.0] - 2026-09-27

The command surface is `foil init`, `foil seat`, `foil send`, and
`foil memory`. `foil --version` prints 0.2.0.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.0.md](docs/plan-v0.2.0.md) is the plan for this release.

### Added

- Templates for `lead`, `implementer`, and `reviewer`, and harness presets for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake`.
- A Git worktree and `foil/<seat>` branch for seats whose template asks for one. Killing a seat leaves the branch and the worktree in place.
- Board mail. `foil send` writes the file, then types one nudge line: the sender and the mail path. The body is never typed.
- Generated instruction files, project memory, and the operator, lead, and worker skills. Each seat's first launch prompt carries its full instruction text, including its role skill.
- An onboarding path in both READMEs and the operator skill: install, `foil init` (which prints the operator's pointer line and the permission setting), a first-run check with the lead, then the goal. Persistent harness install paths are marked unverified.
- Operator scenarios that run against the fake harness, and the same scenarios against a real harness CLI when `FOIL_E2E_LIVE=1`. The default test run skips the live tier.
- [docs/architecture.md](docs/architecture.md) and a text walkthrough in [docs/demo.md](docs/demo.md). This changelog, the contributor guide, and the documentation index.

### Removed

- The 0.1 roster, catalog, status, doctor, dispatch, and mailbox commands, and the skills that described them.
- The 0.1 design documents. They remain at the v0.1.1 tag.

### Changed

- `foil seat list` reports `alive`, `dead`, or `killed`. A seat is alive only while a tmux window still carries its fleet and seat markers. Foil does not interpret pane text.
- `foil seat resume` restarts dead seats with the harness named by the template now.

### Live runs

Both runs used grok (`FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live`).

- With the default `permission = "ask"`: 2 of 6 scenarios passed. Scenarios 1, 3, 4, and 5 timed out after 600 seconds. The run left no pane captures, but the same scenarios passed once seats ran with `auto`, so the seats were most likely waiting for approval.
- With `permission = "auto"`: 5 of 6 passed. Scenario 3 (resume after the tmux session dies) timed out: the resumed lead stopped on grok's own "Help improve Grok" opt-in dialog, not on a Foil or tool-approval prompt.

### Known issues

- A harness's own first-run or opt-in dialog can stop a seat, including after `foil seat resume`. Check new and resumed seats with `foil seat peek`.
- A seat's persona is referenced by path in its instructions rather than included in the text.
- Persistent install paths for the operator skill are unverified for every harness; the pointer line works everywhere.

## [0.1.1] - 2026-09-25

### Changed

- In an empty directory, run `git init` before `foil init`, so later
  commands find this project's only live fleet. `foil init --help`, the
  README, and the skills now say so. (#4)
- The skills no longer claim that `--seat` alone fails; lifecycle commands
  default to this project's Foil state and its only live fleet. (#4)
- A new end-to-end test follows the README Quick Start with fake agents on
  the real `foil` CLI.

### Known issues

- The package metadata and `foil --version` still report 0.1.0.

## [0.1.0] - 2026-08-27

First release. Foil runs a fleet of local CLI agents ("seats") in tmux,
with mail and status kept in files on disk.

- `foil init` sets up an existing Git repository or an empty directory:
  eight role templates, a starter seat roster in `.foil/seats.toml`, and
  an empty fleet.
- A lead-owned fleet: `foil seat spawn`, `list`, `inspect`, `stop`,
  `remove`, and `wake`. Only the lead, or a caller outside the fleet, can
  change membership.
- Seat recipes with `foil seats list`, `show`, and `set`. Built-in presets
  for `grok` and `opencode`; other CLIs join through a declarative profile.
- Worker seats get their own checkout by default: a local clone of the
  project, or a `git worktree` of another repository with `--from`.
- A durable file mailbox with an opt-in tmux wake: `foil send-message`,
  `ack-message`, and `message-status`.
- Shared notepads and reviewed memory lessons.
- `foil status`, `poll-status`, and `resume` (live tmux first, then the
  CLI's own session, then a fresh start), plus `doctor`, `dispatch`,
  `set-state`, and `catalog-list`/`catalog-map` for local persona files.

[0.2.2]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.2
[0.2.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.1
[0.2.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.0
[0.1.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.1
[0.1.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.0
