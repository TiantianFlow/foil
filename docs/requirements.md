# Foil requirements

## 1. Summary

Foil is a lightweight command-line tool for running a fleet of CLI coding
agents in tmux. A human works through their own agent harness, the
*operator*, which uses Foil to start a *lead* agent and hand it a goal.
The lead spawns worker agents from role templates, directs them, and
integrates their work. Agents talk through short messages and shared
files. Anyone can peek at an agent's screen, but Foil never interprets
what it shows. All project work is done by the fleet, never by the
operator.

## 2. Glossary

| Term | Meaning |
|---|---|
| Operator | The human's own harness, outside the fleet. It is never a seat. Foil sees it as "a caller outside the fleet". |
| Fleet | All seats Foil runs for one project. One fleet per project. |
| Seat | One agent process in its own tmux window. |
| Lead | The one seat allowed to spawn and kill other seats. |
| Worker | Any seat that isn't the lead. |
| Template | A role definition: harness, model, persona, worktree, and permission mode. The project's templates form its roster. |
| Harness preset | How to launch one agent CLI (for example `claude` or `codex`). |
| Board | A shared folder all seats can read and write, holding mail, notes, and contract files. |
| Mail | A message to one seat, stored as one file on the board. |
| Nudge | The one line Foil types into a seat's pane to announce new mail. |
| Note | An unaddressed shared file on the board (the notepad). |
| Contract | A board file with an agreed, versioned format. |
| Memory | Reviewed lessons for the project. They outlive fleets and are given to every new seat. |
| Peek | Printing the raw last lines of a seat's pane. |

## 3. Workflow

```text
human ──▶ operator (outside the fleet)
             │  foil init · foil seat spawn lead · foil send lead
             │  foil seat list · foil seat peek lead
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
   `foil seat spawn lead --task "<goal>"`.
3. The lead receives the goal as mail and plans the work.
4. The lead spawns workers by role (`foil seat spawn implementer`) and
   kills them when they're done. Implementation seats get their own git
   worktree and branch.
5. Seats send short messages with `foil send`, and put durable work
   products (task briefs, results, reviews, status) on the board.
6. Every few minutes the operator checks in: it lists seats, peeks at the
   lead, and reads the lead's status file. It passes the lead's questions
   to the human, sends the human's answers to the lead, and nudges the
   lead if it looks stuck. It does no project work. The lead doesn't know
   an operator exists; it acts as if a human is talking to it.
7. The lead integrates the workers' branches, has the result reviewed,
   and reports completion in its status file.
8. After a crash or reboot, `foil seat resume` restarts dead seats.

## 4. Functional requirements

### Fleet and seats

| ID | Requirement |
|---|---|
| F1 | The command set is exactly the one in section 6. |
| F2 | The template named `lead` defines the lead, and its seat is always named `lead`. A fleet has at most one live lead, and the first seat spawned must be the lead. A new lead may be spawned only after the previous one was killed. |
| F3 | `seat spawn` takes a template name, not a harness command. Worker names are auto-numbered from the template name (`implementer-1`, `implementer-2`) unless `--name` is given. A worker name is never reused within a fleet. |
| F4 | Only the lead or a caller outside the fleet may spawn, kill, or resume seats, or review memory (section 6.2). |
| F5 | A seat's identity comes from an environment variable Foil sets when launching it. A seat can't claim another identity through any flag. This protects against mistakes by cooperating agents, not against hostile processes, and the documentation says so. |
| F6 | When a template asks for a worktree, spawning creates a git worktree on a new branch whose name is unique in the repository. Killing a seat never deletes its branch and never silently destroys uncommitted work. |
| F7 | Files and worktrees Foil creates never appear as untracked or modified files in the user's repository. |
| F8 | Seat state is `alive` (its exact tmux window exists), `dead` (the window is gone and the seat wasn't killed), or `killed` (stopped with `foil seat kill`). |
| F9 | `seat resume` restarts `dead` seats, never `killed` ones. It uses the harness's own session resume when the preset supports it; otherwise it starts the seat fresh and tells it that it was restarted and should re-read its mail. A seat with no worktree resumes in the project directory. |

### Visibility

| ID | Requirement |
|---|---|
| F10 | `seat list` reports only facts Foil owns: each seat's name, template, state, and worktree. |
| F11 | `seat peek` prints the raw last lines of one seat's pane, exactly as tmux captures them. |
| F12 | Foil never parses, classifies, or interprets pane contents. Whether an agent is busy, idle, blocked, or done is stated by the agents themselves in board files. |

### Communication

| ID | Requirement |
|---|---|
| F13 | `send` stores the message as a mail file on the board, then types one nudge line into the recipient's pane: the sender and the mail file's absolute path. The message body is never typed into a pane. |
| F14 | Mail sent from outside the fleet shows the sender as `user`. There is no operator mailbox. |
| F15 | The board has a notes area for unaddressed shared files. Seats read and write notes with ordinary file tools; notes wake no one. |
| F16 | Board contracts (section 7.4) are Markdown files with versioned front matter. Foil defines them and tells seats about them, but never reads them. |

### Memory

| ID | Requirement |
|---|---|
| F17 | Memory lessons belong to the project and survive any fleet. |
| F18 | Any seat or the operator may propose a lesson and list lessons. Only the lead or the operator may accept or reject a proposal. A proposal may name a lesson it replaces; accepting it marks the replaced lesson superseded. |
| F19 | All accepted lessons are included in every seat's instruction file at launch. |

### Templates, presets, and instructions

| ID | Requirement |
|---|---|
| F20 | Templates are per project. `init` creates default templates for at least `lead`, `implementer`, and `reviewer`, each with a real persona prompt and a harness that is installed on the machine. `implementer` asks for a worktree. `init` never overwrites existing templates. |
| F21 | The roster is managed by editing template files. There are no roster commands. |
| F22 | A template's persona may be inline text or a path to a Markdown file, which is used untouched. |
| F23 | Harness presets ship for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake` (a test double, section 10). Users can add their own presets in the same format. |
| F24 | Each seat gets a generated instruction file at launch, and its launch prompt tells it to read that file first. The file contains the seat's name, the lead's name, the board path, the exact commands the seat may run, the board and contract conventions, all accepted memory lessons, and, for the lead only, the available templates. |

### Skills

| ID | Requirement |
|---|---|
| F25 | Three skills ship in the package, as plain Markdown, matching the command reference exactly (section 8). `init` writes them into the project's Foil folder, and each seat's instruction file points at that seat's role skill. |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | Python 3.11+ with the standard library only; no runtime dependencies. |
| N2 | External programs are limited to tmux 3.2+, git, and the harness CLIs. |
| N3 | Supported platforms: Linux and macOS. |
| N4 | **Lightweight:** at most 2,000 lines of Python in the package source. The size limit and the exact command surface are both checked by automated tests. |
| N5 | Foil never stores, prints, or logs credentials. Environment variables are forwarded to seats by name only. Memory lessons and mail are refused if they contain credential-shaped text. |
| N6 | Tmux windows are always targeted by exact window ID, never by name. |
| N7 | Foil's own state is written atomically and carries a schema version. |
| N8 | Every error is a one-line message on stderr with a non-zero exit code. No Python tracebacks. |
| N9 | Every tracked file and every commit is safe to publish: no secrets, personal paths, private hosts, or personal emails. Automated tests check the tracked tree. |

## 6. Commands

### 6.1 Command reference

These are the only commands, subcommands, and flags. Every command also
accepts `--help`, and `foil --version` prints the version.

| Command | Flags | Behavior |
|---|---|---|
| `foil init [DIR]` | none | Checks that tmux and git exist and that DIR (default: current directory) is a git repository. Creates the Foil folder, default templates, the three skills, and the git ignore entry. Safe to re-run. |
| `foil seat spawn TEMPLATE` | `--name NAME`, `--task TEXT` | Creates a seat from a template (F2, F3, F6, F24). `--task` is delivered as the seat's first mail. |
| `foil seat kill NAME` | `--all` (no NAME) | Stops the seat's window and marks it `killed` (F6). `--all` stops every seat. |
| `foil seat resume [NAME]` | none | Restarts `dead` seats (F9). |
| `foil seat list` | `--json` | Lists seats (F10). |
| `foil seat peek NAME` | `--lines N` (default 40) | Prints the pane tail (F11). |
| `foil send TO TEXT` | none; `TEXT` = `-` reads stdin | Writes mail and nudges the recipient (F13, F14). |
| `foil memory add TEXT` | `--replaces ID`; `TEXT` = `-` reads stdin | Proposes a lesson and prints its ID (F18). |
| `foil memory accept ID` | none | Accepts a proposal. |
| `foil memory reject ID` | none | Rejects a proposal. |
| `foil memory list` | `--all`, `--json` | Lists accepted lessons; `--all` lists every lesson with its state. |

That's four top-level commands (`init`, `seat`, `send`, `memory`) and
eleven actions.

### 6.2 Authority

| Caller | `init` | `seat spawn` | `seat kill` | `seat kill --all` | `seat resume` | `seat list` / `peek` | `send` | `memory add` / `list` | `memory accept` / `reject` |
|---|---|---|---|---|---|---|---|---|---|
| Outside the fleet | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Lead | no | yes | yes, not itself | no | yes | yes | yes | yes | yes |
| Worker | no | no | no | no | no | yes | yes | yes | no |

## 7. Data design

### 7.1 Layout

Everything lives in one Foil folder inside the project, which is
git-ignored (F7). The concepts below are fixed; exact names may vary.

```text
<foil folder>/
  templates/<role>.toml        role templates (the roster)
  harnesses/<id>.toml          optional user harness presets
  memory/                      lessons; survive fleets
  board/
    mail/<seat>/<time>-<from>-<rand>.md
    notes/                     unaddressed shared files
    status.md                  lead's status (status/v1)
    tasks/<id>.md              task/v1
    results/<id>.md            result/v1
  run/                         registry and generated instruction files
  skills/<role>.md             operator, lead, and worker skills
  worktrees/<label>            one directory per seat worktree
```

### 7.2 Template

The file name is the role name.

| Field | Required | Meaning |
|---|---|---|
| `harness` | yes | Preset id. |
| `model` | no | Passed to the harness's model option. |
| `persona` | no | Inline text, or a path to a Markdown file. |
| `worktree` | no, default `false` | Give each seat its own worktree and branch. |
| `permission` | no, default `ask` | `ask` or `auto`, mapped to the preset's flags. |

### 7.3 Harness preset

| Field | Meaning |
|---|---|
| `id` | Preset name. |
| `command` | Launch argv. Placeholders: `{model}`, `{prompt}`, `{session_id}`. |
| `permission.ask`, `permission.auto` | Extra argv for each mode. |
| `resume` | Optional resume argv, using `{session_id}`. |
| `session_id` | `generated` (Foil creates a UUID and passes it at launch) or `none`. |
| `env` | Names of environment variables to forward. |

Built-in presets use each CLI's documented flags. A flag that could not
be verified is marked in the preset file.

### 7.4 Board files

**Mail:** Markdown with front matter `contract: mail/v1`, `from`, `to`,
`time` (UTC, ISO 8601), and an optional `re` (the mail file it answers),
followed by the body. Written atomically; file names never collide.

**Contracts:**

| Contract | Front matter |
|---|---|
| `status/v1` | `state: working \| blocked \| done`, `updated`, `questions` (list, may be empty) |
| `task/v1` | `id`, `owner`, `state: open \| doing \| done`, `acceptance` |
| `result/v1` | `task`, `author`, `branch` (if any), `outcome: pass \| fail` |

### 7.5 Foil-owned state

**Memory lesson** (schema-versioned): id, text, proposer, time, state
(`proposed`, `accepted`, `rejected`, `superseded`), reviewer, and an
optional `replaces`.

**Registry** (schema-versioned JSON): fleet id, tmux session, lead name,
and per seat: name, template, harness, tmux window id, state, worktree
path, branch, and session id.

## 8. Skills

| Skill | Reader | Must cover |
|---|---|---|
| `operator` | The human's harness | The workflow: `init`, spawn the lead with the human's goal, then let the fleet work. Checking in every few minutes with `seat list`, `seat peek lead`, and the lead's `status.md`. Relaying between the human and the lead with `send`, and nudging a lead that looks stuck. Reviewing memory proposals when asked. Teardown with `seat kill --all`. The rule that the operator never does project work. |
| `lead` | The lead seat | Its responsibilities: plan the goal, staff the fleet, delegate, integrate workers' branches, get the result reviewed, keep `status.md` current, and review memory proposals. How to spawn, kill, and resume seats. That implementation work belongs in seats with worktrees. How to manage the roster by editing template files. Board and contract conventions. |
| `worker` | Every non-lead seat | Its responsibilities: do the assigned task, stay in its own worktree, report results to the lead. Its commands: `send`, `seat list`, `seat peek`, `memory add`, `memory list`. Board and contract conventions. That it may not spawn or kill. |

## 9. Documentation

| ID | Requirement |
|---|---|
| D1 | The README sells the product: a one-line value proposition, the workflow in a diagram, a demo recording, a quick start with a mainstream harness that works in under a minute, and a short comparison with doing the same work in one agent session. |
| D2 | The README states the limits plainly: cooperative-only authority (F5), the risk of `auto` permission, and that Foil never interprets agent screens. |
| D3 | A Chinese README carries the same content as the English one. |
| D4 | This is the only requirements document. Other documents describe design, plans, or how to contribute, and never add requirements. |
| D5 | An architecture document describes Foil's components, data flow, and module boundaries as implemented. |
| D6 | Each release has a plan named `plan-vX.Y.Z.md` and an entry in the changelog. |
| D7 | A contributor guide explains setup, the checks CI runs, and the rules every change keeps: the command surface (section 6), the size limit (N4), and publishing safety (N9). |
| D8 | A documentation index lists every document in the docs folder, and an automated test checks that none is missing. Documents describe the current state; earlier versions live in git history and at release tags. |

## 10. Verification

Principle: **real Foil, real tmux, real git, a small real task; only the
agent is replaced.** Tests check real outcomes (tests pass, a branch
exists, a window is gone), not Foil's reports about itself.

**Fake harness** (the `fake` preset) is a test double for an agent CLI:

- It runs interactively in its pane and reads typed lines, like a real TUI.
- When a nudge arrives, it reads the mail file it names.
- It follows a per-seat script, in a format the standard library can
  parse. Reactions include running a shell command (for example
  `foil seat spawn`, `git commit`, the fixture's test runner), writing a
  board file, and `foil send`.
- On start it handles any mail it hasn't handled yet, so restarts work.
- It keeps a debug log; tests never assert on it.
- It launches through the normal preset path, so templates, launch,
  environment, instruction files, worktrees, and nudges are exercised
  exactly as with a real CLI.

**Required scenarios** (each is a fixture repository plus seat scripts;
the test plays the operator):

| # | Scenario | Assert |
|---|---|---|
| 1 | Happy path: the fixture has one failing test. The test runs `init` and `seat spawn lead --task "make the tests pass"`. The lead spawns an implementer and a reviewer; the implementer commits a fix on its branch; the reviewer runs the tests there and approves; the lead merges and sets `status.md` to done. | Tests pass on the merged result; the branch is merged; `status.md` says done; `seat kill --all` leaves no Foil windows. |
| 2 | Authority: a worker tries `seat spawn` and `seat kill lead`. | Both fail; the fleet is unchanged. |
| 3 | Crash: kill the tmux server mid-scenario, then `seat resume`. | Scenario 1's outcome is still reached. |
| 4 | Operator loop: the lead writes a question to `status.md`; the test answers with `foil send lead`. | The lead's final status reflects the answer; `seat peek lead` shows raw pane text. |
| 5 | Memory: a worker proposes a lesson and fails to accept it; the lead accepts it. | A seat spawned afterwards has the lesson in its instruction file; `memory list` shows it accepted. |
| 6 | Errors: send to an unknown seat, spawn a missing template, run outside a git repository. | Each gives a one-line error and a non-zero exit, with no traceback. |

**Standing checks:** the command surface matches section 6 exactly; the
package source is within the N4 limit; tmux window identity, atomic
writes, public-safety checks (N9), and the documentation index check (D8)
pass.

**Live tier (optional):** the same scenarios with real harness CLIs in
place of `fake`, judged by the same assertions. It needs authenticated
CLIs, so it's opt-in and excluded from the default test run.

## 11. Out of scope

- Any command, subcommand, or flag not in section 6. Changing the command
  surface requires changing this document first.
- Parsing or interpreting pane contents.
- More than one fleet per project, remote machines, and Windows.
- A UI, a server, MCP, or a hosted service.
- Message read receipts, usage-based dispatch, and persona catalog tools.
