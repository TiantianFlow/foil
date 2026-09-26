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

Each item lists what to do and how to tell it's done.

### Lightweight (L)

| ID | Action | Done when |
|---|---|---|
| L1 | Add a **command-surface test**. It walks the CLI's parser and compares every command, subcommand, and flag to section 6 of the requirements. | The test fails if any command or flag is added, removed, or renamed. |
| L2 | Add a **size test** that counts lines of Python in the package source. | The test fails above 2,000 lines. |
| L3 | Remove every command not in section 6 (see the mapping in section 2). | L1 passes. |
| L4 | Delete modules with no remaining use: `src/foil/dispatch.py` (105 lines), `src/foil/catalog.py` (73), `src/foil/doctor.py` (423), `src/foil/status.py` (162), `src/foil/notepad.py` (198), `src/foil/seats.py` (438), `src/foil/onboarding.py` (363), `src/foil/known_clis.py` (15), `src/foil/runtime_config.py` (30), `src/foil/resume.py` (96). | The files are gone and nothing imports them. |
| L5 | Replace `src/foil/adapters.py` and `src/foil/profiles.py` (593 lines together) with one template-and-preset loader. | One loader of about 200 lines. |
| L6 | Rewrite `src/foil/runtime.py` (1,855 lines) as a seat lifecycle module, and `src/foil/cli/__init__.py` (981 lines) as a thin CLI. Replace `src/foil/mailbox.py` (402 lines) with a small board module. | Lifecycle at most about 600 lines, CLI at most about 300, board at most about 120. |
| L7 | Trim what's kept: `src/foil/registry.py` (406), `src/foil/memory.py` (333), `src/foil/fleet.py` (230), and `src/foil/delivery.py` (169), keeping only what the requirements use. Keep `src/foil/tmux.py`, `src/foil/runner.py`, and `src/foil/naming.py` mostly as they are. | L2 passes. |
| L8 | Delete non-code that has no remaining use: the `schemas`, `adapters`, and `profiles` folders; `src/foil/resources`; `src/foil/templates` (replaced by the new defaults); and the tests for removed features. | Only files the requirements need remain. |
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
| V1 | Build the fake harness (section 10), replacing `tests/e2e/fake_cli.py`. | It launches through the normal preset path and follows a script. |
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

- [ ] Every requirement in requirements.md is met.
- [ ] Every action item above is done.
- [ ] Scenarios 1–6 and all standing checks pass.
- [ ] The package source is at most 2,000 lines of Python.
