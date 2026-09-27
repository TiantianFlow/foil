# Changelog

Notable changes to Foil, newest first.

## Unreleased

### Added

- An onboarding section in both READMEs and the operator skill: install, `foil init`, the pointer line, each template's permission, the lead's first prompt, and a check that the lead is working. After that check, the human's goal is sent to the existing lead. Persistent harness install paths are marked unverified.
- A second live run used grok, the harness `foil init` selects when grok is on `PATH`. The live templates were `permission = "auto"`. `FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live` reported 1 failed, 5 passed, 11 deselected in 1644.66s (0:27:24). Scenarios 1, 2, 4, 5, and 6 passed. Scenario 3 timed out after resume, waiting for `state: done`. The recorded lead pane was Grok 4.6 (high) with `always-approve`, stopped on "Help improve Grok" with `[Opt out]` and `[Opt in]`: "Off by default. Opt-in to allow SpaceXAI to retain coding data, e.g., prompts, traces, & metrics, for training and debugging purposes." The inlined instruction had started (`You are seat lead`). This was not a tool-approval prompt.

## [0.2.0] - 2026-09-26

The command surface is `foil init`, `foil seat`, `foil send`, and
`foil memory`. `foil --version` prints 0.2.0.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.0.md](docs/plan-v0.2.0.md) is the plan for this release.

### Added

- Templates for `lead`, `implementer`, and `reviewer`, and harness presets for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake`.
- A Git worktree and `foil/<seat>` branch for seats whose template asks for one. Killing a seat leaves the branch and the worktree in place.
- Board mail. `foil send` writes the file, then types one nudge line: the sender and the mail path. The body is never typed.
- Generated instruction files, project memory, and the operator, lead, and worker skills.
- Operator scenarios that run against the fake harness. The same scenarios run against a real harness CLI when `FOIL_E2E_LIVE=1`; the default test run skips that tier. One live run used grok, the harness `foil init` selects when grok is on `PATH`. `FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live` reported 4 failed, 2 passed, 10 deselected in 2401.76s. Scenarios 2 and 6 passed. Scenarios 1, 3, 4, and 5 timed out after 600 seconds waiting for the seat to write the expected board file.
- [docs/architecture.md](docs/architecture.md) and a text walkthrough in [docs/demo.md](docs/demo.md). This changelog, the contributor guide, and the documentation index.

### Removed

- The 0.1 roster, catalog, status, doctor, dispatch, and mailbox commands, and the skills that described them.
- The 0.1 design documents. They remain at the v0.1.1 tag.

### Changed

- `foil seat list` reports `alive`, `dead`, or `killed` from the stored window id. Foil does not interpret pane text.
- `foil seat resume` restarts dead seats with the harness named by the template now.

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
