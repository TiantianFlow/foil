# Changelog

Notable changes to Foil, newest first.

## Unreleased

Foil 0.2.0 is in development. [docs/requirements.md](docs/requirements.md)
describes it, and [docs/plan-v0.2.0.md](docs/plan-v0.2.0.md) lists the
remaining work.

### Added

- The 0.2.0 requirements and plan.
- This changelog, a contributor guide, and a documentation index.

### Removed

- The 0.1 design documents: the requirements spec, ADRs, design notes, and
  the walking-skeleton and live-test runbooks. They remain available at the
  v0.1.1 tag.

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

[0.1.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.1
[0.1.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.0
