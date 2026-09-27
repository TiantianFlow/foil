# Foil v0.2.0 plan: closing the gaps

Target: [requirements.md](requirements.md). Baseline: the v0.1.1 tag.
This plan lists every gap between the two and the action items that
close them. Requirement IDs (F, N, D) refer to the requirements document.

## 1. Baseline in numbers

| Measure | v0.1.1 | Target |
|---|---|---|
| Top-level commands | 22 (two are groups: `seat` with 6 subcommands, `seats` with 3; the other 20 are flat, e.g. `memory-propose`) | 4 (`init`, `seat`, `send`, `memory`) |
| Distinct actions | 29 | 11 |
| Python in the package source | 7,339 lines | at most 2,000 |
| Skills | 7 skills, including 2 adapter skills (541 lines) | 3 |
| Design docs | 1 spec, 11 ADRs, 6 design docs, 3 other docs | Requirements, architecture, one plan per release, and an index (D4–D8) |
| Tests | 9,892 lines | Scenarios plus standing checks |

## 2. Command mapping

| v0.1.1 | v0.2.0 |
|---|---|
| `init` | `init` |
| `seat spawn` | `seat spawn` (takes a template name) |
| `seat stop`, `seat remove` | `seat kill` |
| `resume` | `seat resume` |
| `status`, `seat list`, `seat inspect`, `poll-status` | `seat list` |
| (none) | `seat peek` (new) |
| `send-message`, `seat wake` | `send` |
| `memory-propose` | `memory add` |
| `memory-supersede` | `memory add --replaces` |
| `memory-accept`, `memory-reject` | `memory accept`, `memory reject` |
| `memory-status` | `memory list --all` |
| `notepad-write`, `notepad-read`, `notepad-ack` | Removed: notes are board files (F15) |
| `ack-message`, `message-status` | Removed |
| `set-state` | Removed: agents report their own state on the board (F12) |
| `seats list`, `seats show`, `seats set` | Removed: edit template files (F21) |
| `catalog-list`, `catalog-map` | Removed: a template's persona can point at any Markdown file (F22) |
| `dispatch`, `doctor` | Removed; `init` checks prerequisites |

## 3. Gaps

| Area | Today | Requirement | Actions |
|---|---|---|---|
| Size | 7,339 lines; `runtime.py` alone is 1,855 and the CLI is 981 | N4: at most 2,000, enforced by a test | L1–L8 |
| Command surface | 29 flat and grouped actions; nothing stops new ones being added | Section 6, enforced by a test | L1, L3 |
| Peek | No way to see a seat's screen | F11: `seat peek` | C5 |
| Mail delivery | The wake line says "poll your mailbox", but a seat has no command to find its mail | F13: the nudge carries the mail file's path | C6 |
| Mail to outside callers | Sending to `operator` crashes with a traceback | F14, N8 | C6, C10 |
| Spawn by role | Role, harness, and model are split across a role file and a seat roster keyed by seat ID; a second implementer needs a new roster entry | F3, F20–F22: spawn by template name, auto-numbered | C1, C2 |
| Harnesses | Only `grok` and `opencode` presets | F23: `claude`, `codex`, `gemini`, `opencode`, `grok`, `fake` | C1 |
| Worktrees | Seats get a `git clone --local` on the main branch; instructions say "one worktree per fleet" | F6, F7: a git worktree on a unique branch per implementation seat | C3 |
| Seat state | `working` means "tmux alive"; `set-state` records operator-declared states | F8, F12: `alive`, `dead`, `killed` only | C4 |
| Seat instructions | The lead is told it owns spawning but not how; no roster; no memory | F24 | C9 |
| Memory | Stored and reviewed, but never given to seats; five flat commands | F17–F19: one `memory` group; accepted lessons reach every seat | C8 |
| Notes | Three commands over hidden JSON files | F15: a notes folder on the board | C7 |
| Errors | Some failures print Python tracebacks | N8 | C10 |
| Init | Writes 8 thin role files (7 lines of metadata each) and a roster hard-coded to `grok`/`opencode` | F20: 3+ templates with real personas, using an installed harness | C11 |
| Skills | 7 skills; none for the operator; the manager skill makes the outside agent the manager | Section 8: operator, lead, worker | S1–S4 |
| Tests | Large suites for features that are being removed; the fake CLI imitates only `grok`/`opencode` | Section 10 | V1–V4 |
| README | Pitches "loyal opposition"; quick start needs `grok` and `opencode`; no demo | D1–D3 | R1, R2 |
| Version | 0.1.1 | 0.2.0 | R4 |
| Publishing safety | Clean at v0.1.1 | N9 on every commit | P1 |
| Documentation | No architecture document for the new design; the changelog and contributor guide describe 0.1.x | D5–D7 | R3, R5 |
| Stale references | Code docstrings, tests, and `foil poll-status --help` cite 0.1 requirement IDs (`CAP-…`) whose documents no longer exist | D4 | L9 |

## 4. Action items

Status: all items in this section are done as of `107b22f`.

Each item lists what to do and how to tell it's done.

### Lightweight (L)

| ID | Action | Done when |
|---|---|---|
| L1 | Add a **command-surface test**. It walks the CLI's parser and compares every command, subcommand, and flag to section 6 of the requirements. | The test fails if any command or flag is added, removed, or renamed. |
| L2 | Add a **size test** that counts lines of Python in the package source. | The test fails above 2,000 lines. |
| L3 | Remove every command not in section 6 (see the mapping in section 2). | L1 passes. |
| L4 | Delete modules with no remaining use: dispatch.py (105 lines), catalog.py (73), doctor.py (423), status.py (162), notepad.py (198), seats.py (438), onboarding.py (363), known_clis.py (15), runtime_config.py (30), resume.py (96). | The files are gone and nothing imports them. |
| L5 | Replace adapters.py and profiles.py (593 lines together) with one template-and-preset loader. | One loader of about 200 lines. |
| L6 | Rewrite runtime.py (1,855 lines) as a seat lifecycle module, and the old cli package (981 lines) as a thin CLI. Replace mailbox.py (402 lines) with a small board module. | Lifecycle at most about 600 lines, CLI at most about 300, board at most about 120. |
| L7 | Trim what's kept: registry.py (406), memory.py (333), fleet.py (230), and delivery.py (169), keeping only what the requirements use. Keep tmux.py, runner.py, and fold naming.py into tmux. | L2 passes. |
| L8 | Delete non-code that has no remaining use: the schemas, adapters, and profiles folders; packaged resources; packaged role templates (replaced by the new defaults); and the tests for removed features. | Only files the requirements need remain. |
| L9 | Remove 0.1 requirement and decision IDs (`CAP-…`, `ADR-…`) from code, tests, and CLI help. | A search of the package and tests finds none. |

Size budget (a guide for L4–L7, not a requirement):

| Part | Lines |
|---|---|
| CLI | ~300 |
| Seat lifecycle (spawn, kill, resume, list, peek, worktrees, instructions) | ~600 |
| Tmux layer | ~300 |
| Registry and atomic storage | ~200 |
| Templates and presets | ~200 |
| Board and mail | ~120 |
| Memory | ~150 |
| Launcher | ~90 |
| **Total** | **~1,960** |

### Core features (C)

| ID | Action | Done when |
|---|---|---|
| C1 | Implement templates and harness presets (section 7.2–7.3), and ship presets for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake`. Check each real CLI's flags against its documentation, and mark any flag that couldn't be verified. | Each preset loads; each template resolves to a launch command. |
| C2 | Implement `seat spawn TEMPLATE [--name] [--task]`: the lead rules, auto-numbered worker names that are never reused, and `--task` delivered as first mail. | F2, F3 are met; scenario 1 spawns by role. |
| C3 | Implement worktrees: `git worktree add` on a branch with a unique name, for templates with `worktree = true`. Killing never deletes the branch or silently loses uncommitted work. Foil's files never show in `git status`. | F6, F7 are met; `git status` in the project is clean after a full scenario. |
| C4 | Implement `seat kill [--all]`, `seat resume`, and the `alive`/`dead`/`killed` state model, including native session resume where a preset supports it. | F8, F9 are met; scenario 3 passes. |
| C5 | Implement `seat peek NAME [--lines N]` using tmux's pane capture on the exact window ID. Print the text unchanged. | F11 is met; scenario 4 shows raw pane text. |
| C6 | Implement `send TO TEXT` (and `-` for stdin): an atomic mail file, then one nudge line containing the sender and the absolute path. Outside callers send as `user`. An unknown recipient gives a clear error. | F13, F14 are met; issues 1 and 2 in section 5 are gone. |
| C7 | Create the board layout, including the notes folder, and describe notes and contracts in seat instructions. | F15, F16 are met. |
| C8 | Implement `memory add/accept/reject/list` with the authority rules, `--replaces`, and the credential check. | F17–F19 are met; scenario 5 passes. |
| C9 | Generate each seat's instruction file with everything in F24, and have the launch prompt point to it. | A lead's file lists templates and exact commands; every file lists accepted lessons. |
| C10 | Add one top-level error handler: a one-line message on stderr and a non-zero exit. | N8 is met; scenario 6 passes. |
| C11 | Rewrite `init`: check prerequisites, create the Foil folder and git ignore entry, and write the `lead`, `implementer`, and `reviewer` templates with real personas and an installed harness. Never overwrite existing templates. | F20 is met; re-running `init` changes nothing. |

### Skills (S)

| ID | Action | Done when |
|---|---|---|
| S1 | Write the operator skill (section 8). | Covers every point in section 8. |
| S2 | Write the lead skill, including roster management by editing templates. | Covers every point in section 8. |
| S3 | Write the worker skill. | Covers every point in section 8. |
| S4 | Delete the old skills: controller, manager, worker (replaced by S3), poll-status, memory-update, shared-notepads, and the two adapter skills. Add a test that every command in a skill exists in section 6. | Only the three new skills remain; the test passes. |

### Verification (V)

| ID | Action | Done when |
|---|---|---|
| V1 | Build the fake harness (section 10), replacing the old e2e fake CLI shim. | It launches through the normal preset path and follows a script. |
| V2 | Write scenarios 1–6 with their fixture repositories and seat scripts. | All six pass. |
| V3 | Keep the standing checks: L1, L2, tmux window identity, atomic writes, and the public-safety tests (`tests/test_docs_hygiene.py`, `tests/test_artifacts.py`). Update the version test for 0.2.0. | All pass. |
| V4 | Make the live tier runnable by opt-in with real harness CLIs. | The scenarios run against real CLIs when enabled, and are skipped otherwise. |

### Documentation and release (R, P)

| ID | Action | Done when |
|---|---|---|
| R1 | Rewrite the README to D1 and D2. | Value line, diagram, demo recording, one-minute quick start with a mainstream harness, comparison, and limits are all present. |
| R2 | Rewrite the Chinese README to match (D3). | Same sections and commands as the English README. |
| R3 | Write the architecture document (D5): components, data flow, and module boundaries. Start once C1–C11 exist and revise it as the code settles. List it in the docs index. | It describes the implemented components and agrees with the code. |
| R4 | Set the version to 0.2.0 in the package and CLI. | `foil --version` prints 0.2.0. |
| R5 | Add the 0.2.0 entry to `CHANGELOG.md`, and update `CONTRIBUTING.md` with the new test commands and the live-tier switch (D6, D7). | Both match the release. |
| P1 | Before publishing, scan every commit (not just the current tree) for secrets, personal paths, private hosts, and personal emails. | The scan is clean (N9). |

## 5. Known issues in v0.1.1

| # | Issue | Closed by |
|---|---|---|
| 1 | A woken seat can't find its mail: `message-status` needs a message ID the seat doesn't have. | C6 |
| 2 | Sending to a non-seat (e.g. `operator`) crashes with a Python traceback. | C6, C10 |
| 3 | The lead's instructions say it owns spawning but give no commands or roster. | C9 |
| 4 | `seats set --profile` fails on starter seats with a CLI conflict. | L3 (command removed) |
| 5 | `seats set` has no authority check, so a worker can add a lead row. | L3 (command removed) |
| 6 | Only `grok` and `opencode` presets. | C1 |
| 7 | Design docs contradict the README about required flags. | Closed: the 0.1 design docs are gone |
| 8 | Seat isolation is a `git clone --local` on the main branch. | C3 |
| 9 | Seat instructions say "one worktree per fleet", contradicting per-seat isolation. | C9 |
| 10 | The nudge is typed even when the pane isn't at an input prompt. | By design; document it in R1 |
| 11 | The Chinese README is maintained separately and drifts. | R2 |

## 6. Dependencies

- L1 and L2 come first, so the size and command limits hold throughout.
- C1 before C2; C2 before C3, C4, C9.
- C6 and C8 before C9 (instructions describe mail and include lessons).
- V1 before V2; V2 needs C2–C11.
- S1–S3 after the command surface is final (L3).
- R1 and R2 after S1–S3.
- R3 after C1–C11.
- R5 last: it describes the finished release.

## 7. Definition of done

Status at `107b22f`:

- [ ] Every requirement in requirements.md is met. Open: section 9.
- [x] Every action item in sections 4 and 8 is done.
- [x] Scenarios 1–6 and all standing checks pass with the fake harness.
- [ ] Scenarios 1–6 pass with a real harness. Open: section 9 (G1).
- [x] The package source is within the N4 target (1,999 lines).

## 8. Acceptance review (2026-09-26)

Reviewed at commit `e4a014f`: the full test suite, lint, build, a hand run
of the CLI, every requirement, and the definition of done above.

**Verdict: not accepted yet.** One blocker (A1). Everything else in
sections 4–7 is done or nearly done.

### 8.1 Checks

| Check | Result |
|---|---|
| Command surface (section 6) | Pass, enforced by `tests/test_command_surface.py` |
| Package size | Pass: 1,922 of 2,000 lines |
| Lint and build | Pass; the wheel builds as 0.2.0 |
| Tests | 85 pass, 6 live tests skip as designed, **scenario 3 fails every run** |
| Publishing safety | Pass: every commit uses the noreply identity; no personal paths, private hosts, or secrets |

### 8.2 v0.1.1 known issues (section 5)

All eleven are closed:

| # | Status |
|---|---|
| 1 | Closed: the nudge carries the mail file's path, and the fake harness exercises it. |
| 2 | Closed: an unknown recipient gets `foil: unknown seat`; other failures print one line. |
| 3 | Closed: the lead's instruction file lists its commands and the templates. |
| 4, 5 | Closed: the roster commands are gone. |
| 6 | Closed: six presets. The `codex` and `gemini` flags are marked unverified. |
| 7 | Closed: the 0.1 design docs are gone. |
| 8 | Closed: each worktree seat gets a git worktree on its own `foil/<seat>` branch. |
| 9 | Closed: seats are told to stay in their own worktree. |
| 10 | Closed: the README, skills, and demo say the nudge is typed even when the pane is not at a prompt. |
| 11 | Closed: both READMEs have the same sections. |

### 8.3 New defects

| # | Defect | Effect |
|---|---|---|
| B1 | Seat state, nudges, and peek use the bare tmux window ID. Window IDs restart at `@0` when the tmux server restarts, so after a crash or reboot one seat's stored ID can name another seat's window. | Scenario 3 fails: after `seat resume` the implementer gets `@0`, the lead's stale `@0` looks alive, and the lead is never restarted. Until fixed, `send` and `peek` can hit the wrong seat after any restart. Breaks F8, F9, and N6. |
| B2 | The `opencode`, `codex`, and `gemini` presets resume with "continue the last session here" flags. The lead has no worktree, so it runs in the project folder, which is where the operator's harness usually runs too. | Resuming the lead can pick up the operator's own conversation. |
| B3 | Spawn refuses when the `foil/<seat>` branch already exists instead of choosing another name. | Spawning fails in a repository that still has a branch from an earlier fleet. F6 asks for a unique name. |
| B4 | Worktrees are created in a sibling folder, `<project>.foil/<seat>`. | Contradicts section 7.1 of the requirements ("everything lives in one Foil folder inside the project"), and creates folders next to the user's repository. |
| B5 | The default lead persona says "The operator owns the goal". | The workflow says the lead doesn't know an operator exists. |

### 8.4 README and skills: bootstrapping a harness

The requirements ask the README to sell the product (D1) and the skills to
cover each role (F25, section 8). They don't say how a skill reaches the
harness that reads it. The branch shows the gap:

- **The operator skill is hard to get.** It isn't in the installed
  package, so after `uv tool install` the user has no copy. It is a plain
  Markdown file with no name or description header, so harnesses that load
  skills from a folder (for example Claude Code) won't pick it up.
- **The README skips the operator.** The quick start has the human run
  `foil` commands directly. It never shows the core workflow: load the
  operator skill into your harness and ask it for the goal. The skills are
  linked only as "what each role runs".
- **The lead and worker skills never reach seats.** At runtime a seat
  reads its generated instruction file and its template's persona. The
  default lead persona repeats most of the lead skill, so the same guidance
  lives in two places that can drift.
- **No demo recording** (D1). The README says so, which reads as a
  weakness on a page meant to sell the product.

### 8.5 Test suite

The fake-harness scenarios are the right design. Nudges really go through
the pane, and the assertions check real outcomes. Scenario 3 caught B1.
Gaps:

- No scenario checks that each seat points at its own window after a
  restart; B1 surfaced only as a timeout.
- The fake answers instantly. Real agents are often busy when a nudge
  arrives.
- No scenario sends to a killed or dead seat.
- The operator side calls the CLI in-process, never through the installed
  `foil` command.
- Only the live tier can show that real agents follow the instruction
  files and skills, and nothing requires running it before a release.

### 8.6 Action items

Status: A1–A8 are done as of `107b22f`. A9 is done: the run is recorded in the changelog, but its result is open in section 9 (G1).

Requirement changes come first, per the contributor guide.

| ID | Action | Done when |
|---|---|---|
| A1 | Fix B1: check each window's fleet and seat markers (the tmux layer already sets and verifies them for kill) before treating a seat as alive, and before nudging or peeking. | Scenario 3 passes, and A7's first check passes. |
| A2 | Fix B2: resume with "continue the last session" flags only for seats with their own worktree; other seats restart fresh. | A test shows the lead restarting fresh under those presets. |
| A3 | Fix B3: when `foil/<seat>` exists, pick the next free branch name. | Spawning succeeds with a leftover branch present. |
| A4 | Resolve B4: either move worktrees inside the Foil folder (it is already git-ignored) or change section 7.1 of the requirements to allow the sibling folder. | Code and requirements agree. |
| A5 | Fix B5: remove the operator from the lead persona. | No mention of the operator in any seat-facing text. |
| A6 | Requirements: add that the README shows how to load the operator skill into a harness (D1), and that skills reach their readers: `foil init` writes all three into the Foil folder with a name and description header, and each seat's instruction file points to its skill (F25). Then implement: package the skills, have `init` write them, point instruction files at them, and make the lead and worker skills the single source for role guidance. | After a fresh `uv tool install` and `foil init`, the operator skill is on disk in a loadable form, and the instruction files reference the seat skills. |
| A7 | Add scenarios: after a restart, each seat's peek shows its own window and a nudge to the lead reaches the lead; a busy fake (it waits before answering) receives two nudges; `send` to a killed seat does not type into any window; one scenario drives the operator through the installed `foil` command. | All pass. |
| A8 | Rewrite the README quick start around the operator: install Foil, run `foil init`, load the operator skill into your harness, give it the goal. Keep the direct commands as a secondary path. Replace the "no demo recording" line with a recording, or drop the line until one exists. | The quick start starts from the operator skill; the Chinese README matches. |
| A9 | Run the live tier with at least one real harness before tagging 0.2.0, and record the result in the changelog entry. | The 0.2.0 entry names the harness and the scenarios that passed. |

## 9. Second acceptance review (2026-09-27)

Reviewed at commit `107b22f`. The fake-harness suite passes (99 passed,
6 live tests skipped), lint is clean, every commit uses the noreply
identity, and no commit adds personal paths, emails, or secrets.
Requirements N4 (now a 2,500-line target, not a hard limit) and D1 (a demo
recording is optional) changed in this review.

### 9.1 Gaps

| # | Gap | Why it matters |
|---|---|---|
| G1 | The only live run (grok) is inconclusive: 4 of 6 scenarios timed out, and the 2 that passed need no agent work. The run left no logs. Two causes look identical from outside: the harness was not logged in, or a seat stopped at an approval prompt because the live tier keeps the default `permission = "ask"`. | Nothing yet shows that a real agent can complete the happy path. |
| G2 | There is no working onboarding procedure. The operator skill has no install steps for any harness. Seats reach their instructions only through a chain of file pointers, and nothing verifies that a real agent can follow it. | A new user cannot get from install to a working fleet, and the operator and seats may never see their skills. |
| G3 | Two copies of each skill, one in the top-level `skills` folder and one in `src/foil/defaults/skills`, with no check that they match. | The copies will drift. |
| G4 | `tests/test_package_size.py` still fails above 2,000 lines, but N4 is now a 2,500-line target. | The next change over 2,000 lines turns CI red for a limit the requirements no longer set. |

### 9.2 Onboarding flow (design)

Three readers need their instructions: the **operator** (the human's
harness), the **lead**, and the **workers**. Foil launches the seats, so it
controls how their instructions arrive. It does not launch the operator, so
the operator's skill has to be installed by the human or by the harness.

**Step 1: prerequisites.** Python, Git, tmux, and at least one harness CLI
that is **already logged in**. Foil never handles logins. The check is
running the harness once by hand. Seats run as the same user, so they
inherit that login.

**Step 2: install and initialize.**

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
cd your-repo
foil init
```

`foil init` writes `.foil/` (templates, skills, board) and picks an
installed harness. Its output should end with the next steps: the exact
pointer line for step 3 and the permission choice from step 4.

**Step 3: give the operator its skill.** Two paths, in this order:

1. **Pointer (works in every harness, nothing to install).** In the
   harness, in the repo:
   `Read .foil/skills/operator.md and follow it. My goal: <goal>.`
2. **Persistent install (optional).** Copy the skill where the harness
   discovers skills or always-on instructions, so later sessions load it
   without the pointer. The README and the operator skill carry one row per
   harness, with the exact path for the operator's CLI. For example, for
   Claude Code: `~/.claude/skills/foil-operator/SKILL.md`. Paths for the
   other harnesses must be checked against each CLI's documentation before
   they are published, the same way preset flags are. Install to the user
   level, not the repository, so nothing appears in `git status` (F7).

**Step 4: choose how seats get permission.** With `permission = "ask"`
(the default), every seat stops at its first approval prompt and waits
for the human in that pane. That is safe but not unattended. With
`permission = "auto"`, the fleet runs on its own. The README and the
operator skill state this choice plainly, and the operator skill tells the
operator to check a new seat for an approval prompt with `foil seat peek`.

**Step 5: seats get their skills from Foil (no install).** Foil launches
each seat, so it delivers the instructions itself, deterministically and
without harness-specific flags:

1. **Inline in the first prompt.** The launch prompt carries the full
   instruction text (the seat's role skill, persona, commands, and board
   conventions) instead of only a path. Every preset already passes a
   prompt argument, so this adds no coupling to any CLI.
2. **Re-read on wake.** The instruction text tells the seat to re-read its
   instruction file whenever it is woken, so the role survives a harness
   compacting a long conversation.
3. **Spawners pass only the goal.** The operator and lead skills each say,
   in one line, to put only the goal in `--task`, because Foil delivers the
   role instructions. Agents never copy skill text themselves.

The instruction file stays the single source and is what gets inlined.
Harness system-prompt flags (for example Claude Code's
`--append-system-prompt-file`) are not used. Revisit them only if live
runs show seats losing their role in long sessions. They would then be an
optional preset field with the first prompt as the fallback, which keeps
presets data-only.

**Step 6: first-run check.** The operator skill's first task is a smoke
test: spawn the lead with "Write `board/status.md` with `state: done`",
then wait a few minutes. If the file doesn't appear, `foil seat peek lead`
shows why: a login prompt, an approval prompt, or an error. The operator
reports that to the human instead of waiting.

### 9.3 Action items

| ID | Action | Done when |
|---|---|---|
| O1 | Requirements: describe the onboarding flow (9.2): the pointer line `init` prints, instructions inlined in the first prompt, re-read on wake, and the first-run check. Mark which operator-skill install paths are verified. | Requirements and the plan agree. No new command or flag. |
| O2 | Inline the instruction text in each seat's first prompt: the role skill, persona, commands, and board conventions, plus the line to re-read the instruction file on every wake. No preset changes. | A test shows every preset's launch argv carrying the full instruction text, and the fake harness receives it. |
| O3 | `foil init` prints the next steps: the operator pointer line and the permission choice. | A test checks the printed pointer line. |
| O4 | README and operator skill: a short onboarding section following 9.2, with the per-harness install table. Unverified paths are marked. The operator and lead skills say to pass only the goal in `--task`. | A new user can follow it from install to a first `status.md` without other docs. |
| O5 | Live tier (G1): a preflight scenario first (the step 6 smoke test, with a 3-minute limit); throwaway repos use `permission = "auto"`; on any timeout, the failure message includes `foil seat peek` output for every seat. | A failed live run says whether it hit a login prompt, an approval prompt, or something else. |
| O6 | Re-run the live tier with at least one logged-in harness (and a second if available), and record the result in the changelog. | Scenarios 1–6 pass live, or each failure is explained by its captured pane. |
| O7 | G3: keep one copy of each skill (the packaged one) and link the README to it, or add a test that the copies are identical. | One source, or a failing test when copies differ. |
| O8 | G4: turn the size test into a report against the 2,500-line target (N4). It prints the count and does not fail CI. | CI shows the count; exceeding it does not fail the build. |

### 9.4 Notes from similar tools

- **CLI Agent Orchestrator (CAO)** passes each role prompt through the
  harness's own system-prompt flag, gives agents tools through an MCP
  server, and delivers messages only when it reads the pane as idle. The
  pane reading is the fragile part Foil avoids.
- **Maestri** gives agents a CLI (`maestri ask`, `maestri list`) and
  installs its skill automatically into every agent it connects, with a
  "use the Maestri skill" nudge as the fallback. Foil follows the same
  shape: a CLI plus skills, delivered by the tool that launches the seat.
