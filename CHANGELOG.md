# Changelog

Notable changes to Foil, newest first.

## Unreleased

### Documentation

- Both READMEs: the product's two names in each title, the "loyal opposition" tagline, three problems Foil solves compared with a single agent session, a diagram that labels what each box is, a short section on how Foil is built, one getting-started path (replacing the separate quick start and onboarding) for any supported agent CLI rather than Claude only, with what to check when a seat makes no progress, and badges for Python, platforms, and runtime dependencies.
- A demo per language ([docs/demo.md](docs/demo.md), [docs/demo.zh-CN.md](docs/demo.zh-CN.md)), rebuilt from a hand replay of end-to-end scenario 1.
- Module and spawn/send diagrams in [docs/architecture.md](docs/architecture.md), and corrections where it had fallen behind the code: seat state and nudges check the window's markers, and instruction files include the role skill's text.

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

[0.2.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.0
[0.1.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.1
[0.1.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.0
