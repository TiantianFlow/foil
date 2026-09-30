# Plan: Foil 0.3.0

**Goal:** mail and board reads go through `foil` commands (issue #12), so a
seat working in a worktree can read them without an approval prompt for
paths outside its working directory.

## Problem

A seat in a worktree (`.foil/worktrees/<name>`) reads mail and board files
that live in `.foil/board/`, outside its working directory. Harnesses ask
for approval on those reads, which stalls a seat that is meant to run
unattended. A `foil` command can be allowed once by the harness.

## Solution

Four read commands, each with `--json`:

| Command | Behavior |
|---|---|
| `foil mail read PATH` | Reads one mail file under `.foil/board/mail/`. Human output is the body; JSON is `from`, `to`, `time`, `re`, `body`. |
| `foil mail list` | Lists the calling seat's mail, newest first. Needs `FOIL_SEAT_ID`. |
| `foil board read PATH` | Reads one file under `.foil/board/`, by absolute path or relative to the board folder. JSON carries the front matter fields and `body`, or only `body` for a file with no front matter. |
| `foil board list PATTERN` | Lists board files matching a glob relative to the board folder, sorted. JSON is `{"files": [...]}`. |

Paths are checked before any read: a `..` part is refused, and so is any
path that resolves outside the allowed folder. Errors are one line on
stderr (N8). Writing stays with ordinary file tools and `foil send`.

## Changes

- `docs/requirements.md`: section 6.1 rows, section 6.2 columns, F29 to F32,
  and the counts (six top-level commands, fifteen actions).
- `src/foil/mail_ops.py` and `src/foil/board_ops.py`, wired in `cli.py`.
- The lead and worker skills name the new commands in place of "open that
  file". The command-surface and skill tests know the new commands.
- `CHANGELOG.md`: a 0.3.0 entry.

## Done when

1. The four commands work from a worktree and from outside the fleet, with
   `mail list` failing without a seat identity.
2. Requirements, skills, and tests agree with the command tree.
3. The suite, `ruff check .`, the command-surface test, and the N4 size
   report pass, and a fresh install was checked as CONTRIBUTING.md asks.

## Out of scope

- Changing `foil send`, or any command that writes the board.
- Deleting or managing mail.
- Roster commands.
