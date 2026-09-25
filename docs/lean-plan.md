# Lean Foil: workflow, requirements, and plan

Status: proposal. This replaces the current requirement set. Anything not
listed here gets deleted, not kept around "just in case".

## 1. The intended workflow

```text
human ──talks to──▶ operator (the human's own harness, OUTSIDE the fleet)
                        │  foil init / foil spawn lead / foil send lead / foil status --peek
                        ▼
                   ┌──────────── tmux session: one fleet ────────────┐
                   │  lead  ──foil spawn/kill──▶  implementer-1       │
                   │   │                          implementer-2       │
                   │   └──foil send──▶            reviewer            │
                   └──────────────────────────────────────────────────┘
                        ▲ all seats read/write the board (.foil/board/)
```

1. **Human → operator.** The human opens any harness (Claude Code, Codex,
   ...) with the operator skill, usually on a cheap model. The operator
   never edits project code. It is not a seat and has no tmux window.
2. **Operator → Foil.** `foil init` (once per repo), then
   `foil spawn lead --task "<goal>"`.
3. **Lead gets the goal.** The goal arrives as the lead's first mail. The
   lead writes a plan to the board.
4. **Lead staffs the fleet.** `foil spawn implementer`, `foil spawn reviewer`,
   ... from templates. Implementation seats get their own git worktree and
   branch automatically. The lead kills seats it no longer needs.
5. **Seats talk.** Short messages with `foil send`; anything durable
   (task briefs, results, review findings, status) as files on the board.
6. **Operator checks in** every few minutes: `foil status --peek`, reads
   `board/status.md`, relays questions to the human, sends the human's
   answers to the lead. It does no project work itself.
7. **Lead integrates.** It merges seat branches, has a reviewer check the
   result, then writes the final report to `board/status.md`.
8. **Crash or reboot:** `foil resume` brings dead seats back.

## 2. Answers to the open questions

**Mail vs. JSON (Q1).** Agree: mail belongs to the note board. Proposal:

- **Mail is a file on the board**: `.foil/board/mail/<to>/<time>-<from>.md`,
  Markdown with a small front matter (`from`, `to`, `time`, `re`). Seats
  read it with `cat`/`ls`. No read or ack commands.
- **File contracts are board files too**: `board/tasks/<id>.md`,
  `board/results/<id>.md`, `board/status.md`. Markdown with front matter
  including `contract: task/v1`, so it's versioned, but agents write
  Markdown far more reliably than hand-written JSON.
- **Versioned JSON stays for Foil's own state** (the seat registry,
  written only by Foil code). That's where schema versions pay off.

**Peek (Q2).** Agree: peek is a window into a seat, not an interpreter.
`foil status --peek [N]` prints the raw last N lines of each pane.
Foil never parses pane text. Seat state = tmux window alive or dead,
nothing more.

**Command sprawl (Q3).** Agree, and yes, my last suggestion made it
worse. New rule: **the command count may only go down.** Peek becomes a
flag on `status`, and the inbox command is not needed because the wake
nudge carries the mail file's path.

## 3. Requirements (lean)

| ID | Requirement |
|---|---|
| R1 | Exactly six commands: `init`, `spawn`, `kill`, `send`, `status`, `resume`. |
| R2 | `init` writes `.foil/templates/` (committed) and ignores `.foil/run/` and `.foil/board/`. It checks for tmux and git and nothing else. |
| R3 | A **template** is one TOML file: `harness`, `model`, `persona` (inline text or a path to an untouched Markdown file), `worktree` (bool), `permission`. The file name is the role name. |
| R4 | `spawn TEMPLATE [--name N] [--task TEXT]` creates a tmux window; names auto-number (`implementer-1`, `-2`). The first seat must be the lead. |
| R5 | Only the lead or a caller outside the fleet (the operator) can `spawn` or `kill`. Identity comes from `FOIL_SEAT` in the seat's environment. This only stops cooperating agents, and the docs say so. |
| R6 | `worktree = true` runs `git worktree add -b foil/<name> worktrees/<name>` from the lead's HEAD. `kill` keeps the branch so the lead can merge it. |
| R7 | `send TO TEXT` writes the mail file, then types one line into the recipient's pane: `New mail from <from>: <path>`. Messages to `operator` are written to `mail/operator/` with no tmux typing. |
| R8 | `status [--peek N]` lists seats (name, template, alive/dead, worktree, unread mail count) and optionally the raw pane tail. No parsing. |
| R9 | `resume` restarts dead seats using the harness's native resume when its preset declares one, and otherwise starts them fresh with a "you were restarted, read your mail" note. |
| R10 | **Harness presets** (one TOML each, same format for users' own): `claude`, `codex`, `gemini`, `opencode`, `grok`, plus `fake` for tests. |
| R11 | Every seat gets a generated `FOIL.md`: its name, the lead's name, board path, templates it may spawn (lead only), the exact commands it may run, and the file-contract conventions. |
| R12 | Three skills: `operator` (outside), `lead`, `worker`. |
| R13 | Safety kept from today: exact tmux window IDs (never kill by name), atomic state writes, no credentials stored (env forwarded by name only). |
| R14 | Size budget: at most ~2,000 lines of Python in `src/` (today ~7,900). |

**Deleted as requirements:** memory review workflow, notepads, usage-based
dispatch, manual state setting, the poll-status reader, message acks,
persona catalog browsing, the doctor command, the separate seat roster
(`seats.toml`), the 36 numbered CAP requirements, and the 10 ADRs.

## 4. Command audit (22 top-level commands + 9 subcommands → 6)

| Today | Fate |
|---|---|
| `init` | Keep (simplified, R2) |
| `seat spawn` | Becomes `spawn` |
| `seat stop`, `seat remove` | Merged into `kill` |
| `seat wake`, `send-message` | Merged into `send` |
| `status`, `seat list`, `seat inspect`, `poll-status` | Merged into `status [--peek]` |
| `resume` | Keep (simplified, R9) |
| `ack-message`, `message-status` | Delete: mail is a file; reply is the ack |
| `notepad-write`, `notepad-read`, `notepad-ack` | Delete: board files |
| `memory-propose/accept/reject/supersede/status` | Delete |
| `dispatch` | Delete |
| `set-state` | Delete: status is alive/dead |
| `doctor` | Delete: `init` checks prerequisites |
| `catalog-list`, `catalog-map` | Delete: `persona = "path.md"` in a template |
| `seats list/show/set` | Delete: edit `.foil/templates/*.toml` |

Modules to delete or collapse: `memory.py`, `notepad.py`, `dispatch.py`,
`catalog.py`, `doctor.py`, `status.py`, `seats.py`, `onboarding.py` (into
`init`), `mailbox.py` (rewritten to about 60 lines), `adapters.py` +
`profiles.py` (merged into one preset loader). Reused: `tmux.py`,
`runner.py`, the atomic-write/lock helpers from `registry.py`.

Other deletions: skills `controller`, `manager`, `poll-status`,
`memory-update`, `shared-notepads`, and `adapters/*`; the `docs/spec`,
`docs/adr`, and `docs/assignments` folders; the walking-skeleton and
exam docs. These are replaced by one `DESIGN.md`.

## 5. Plan

Each step is one reviewable PR. Tests are re-run from scratch at the end.

0. **Freeze.** Tag the current tip `legacy-0.1` for reference.
1. **Design contract.** Write `DESIGN.md` (sections 1–3 above, plus the
   file layout and template format). Delete the spec, ADR, and assignment
   docs.
2. **Test harness first.** Build the fake harness and the scenario runner
   (section 7), with the "fix a failing test" scenario. It fails for now.
3. **New core.** Implement the six commands on top of `tmux.py`/`runner.py`:
   templates, presets, worktrees, board mail, generated `FOIL.md`.
4. **Delete.** Remove the old commands, modules, schemas, and their tests.
   Check the size budget (R14).
5. **Skills.** Write the `operator`, `lead`, and `worker` skills.
6. **Presets.** Add `claude`, `codex`, and `gemini` (check each CLI's model,
   permission, and resume flags against its current docs).
7. **README.** Rewrite around "one operator outside, one lead inside, a
   fleet in tmux", with a terminal recording, a Claude/Codex quick start,
   and one diagram. Regenerate the Chinese README from it.
8. **Release.** Live smoke test with real CLIs, make the repo public,
   publish to PyPI. Optionally squash history to one initial commit.

## 6. Known bugs (re-check after the plan)

| # | Bug | Likely fate |
|---|---|---|
| 1 | A woken seat can't find its mail: `message-status` needs an ID the seat doesn't have; `MailboxStore.pending()` isn't exposed. | Gone (R7: the nudge carries the path) |
| 2 | `send-message --seat operator` crashes with a raw Python traceback. More generally, some errors aren't caught and print tracebacks. | Operator mail defined (R7); add a catch-all error handler |
| 3 | The lead's `FOIL.md` says it owns spawning but never shows the commands or the roster. | Fixed by R11 |
| 4 | `seats set --profile` on a starter seat fails ("cli grok conflicts with profile cli bash"). | Gone with `seats` |
| 5 | `seats set` has no permission check: a worker can add a `lead = true` row. | Gone; templates are plain files (still cooperative only) |
| 6 | Only `grok` and `opencode` presets. | Fixed by R10 |
| 7 | `docs/design/profiles.md` still says `--seat` alone is not enough. | Doc deleted |
| 8 | Per-seat isolation is a `git clone --local` on `main`, not a worktree or branch. | Fixed by R6 |
| 9 | The lead is told "one worktree per fleet", contradicting per-seat isolation. | Fixed by R6/R11 |
| 10 | The wake text is typed even when the pane isn't at an input prompt (in a plain shell it runs as a command). | Stays; documented: Foil types, the harness decides |
| 11 | History: 47 of 50 commits don't build (path-filtered), PR refs point to an earlier repo, old name "capstan". | Cosmetic; squash at release |
| 12 | The repo is private, so the README install command and CI badge don't work for anyone else. | Fixed at release |
| 13 | The Chinese README is maintained separately by hand. | Regenerated in step 7 |
| 14 | Internal process notes (`docs/assignments/`, the live exam doc) are in the public tree. | Deleted in step 1 |

## 7. Testing Foil truthfully

Principle: **real Foil, real tmux, real git, a real (tiny) task; only the
model is mocked.** Assertions check outcomes in the world (tests pass, a
branch exists, a window is gone), never Foil's own JSON claims about
itself.

**The fake harness** (`fake` preset) is the dependency mock. It's a small
interactive program that:
- shows a prompt and reads lines from its pane, like a real TUI;
- on a `New mail ...: <path>` line, reads that file;
- follows a per-seat script (YAML) of reactions: run a shell command
  (`foil spawn implementer`, `git commit`, `pytest`), write a board file,
  or `foil send` a reply;
- logs what it saw and did to its own file, for debugging only.

It uses the same launch path as any preset, so spawn, env, `FOIL.md`,
worktrees, and nudges are exercised exactly as with a real CLI.

**Scenarios** (each is a fixture repo plus seat scripts):
1. *Happy path.* The repo has one failing test. The test acts as the
   operator: `init`, `spawn lead --task "make tests pass"`. The fake lead
   spawns an implementer and a reviewer. The implementer applies the fix
   on its `foil/implementer-1` branch and mails the lead. The reviewer runs
   `pytest` on that branch and mails "approve". The lead merges and writes
   `status.md`. **Assert:** `pytest` passes on main, the branch is merged,
   `status.md` says done, `kill` leaves no windows.
2. *Authority.* A worker script runs `foil spawn` and `foil kill lead`.
   **Assert:** both are refused and the fleet is unchanged.
3. *Crash.* Kill the tmux server mid-task, run `foil resume`. **Assert:**
   the scenario still finishes.
4. *Operator loop.* The operator mails a question; the lead answers in
   `mail/operator/`. **Assert:** the file exists; `status --peek` shows
   raw pane text.

**Live tier.** The same scenarios with real CLIs replacing `fake`
(opt-in, `FOIL_LIVE=1`), judged only by the same outcome assertions. It
isn't run in CI; run it before each release.

**Unit tests** stay only for tmux window identity, atomic writes, and
template/preset parsing. The other ~9,900 lines of tests go with the
features they test.
