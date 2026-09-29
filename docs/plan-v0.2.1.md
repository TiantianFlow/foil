# Foil v0.2.1 plan: AI-native onboarding

Target: [requirements.md](requirements.md). Baseline: the v0.2.0 tag.
The release turns onboarding from a human procedure into one a coding
agent performs, makes `foil init` report the whole machine instead of one
harness, ships a catalog of candidate roles, and closes the small gaps
carried out of [plan-v0.2.0.md](plan-v0.2.0.md). Requirement IDs (F, N, D)
refer to the requirements document.

The driver is issue 8, *AI native onboarding flow*
(<https://github.com/TiantianFlow/foil/issues/8>).

## 1. Baseline in numbers

| Measure | v0.2.0 | Target |
|---|---|---|
| Top-level commands | 4 (`init`, `seat`, `send`, `memory`) | 4, unchanged |
| Distinct actions | 11 | 11, unchanged |
| Python in the package source | 2,005 lines | at most 2,500 (N4); this release budgets about 60 |
| Built-in harness presets | 6 | 6, unchanged |
| Harnesses `init` reports | 1 (the first match of a fixed order) | every installed one, built-in and user |
| Packaged personas | 3 (`lead`, `implementer`, `reviewer`) | 8 (those three plus five candidates) |
| Templates `init` writes | 3 | 3, unchanged |
| Skills | 3 (operator, lead, worker) | 3, unchanged |
| Environment a seat inherits | `FOIL_SEAT_ID`, `PATH`, plus the preset's `env` | the same, plus `HOME` |
| Onboarding reader | a human, with one step handed to an agent | a coding agent throughout |
| Carried gaps | K1–K4 open | K1–K3 closed, K4 stated as unverified |

## 2. Issue 8, traced

Every line of issue 8 lands on a requirement and on action items that say
how to tell they are done.

| Issue 8 asks for | Requirement | Actions |
|---|---|---|
| "Convert the onboarding instructions to be AI first. Use a coding agent to onboard." | Workflow → Onboarding, rewritten | Q1, A1, A2 |
| "Installation." | Onboarding steps 1–2 | Q1, A1 |
| "Create the initial set of default role templates." | F20 | Q3, T1, T3 |
| "Scan and collect the available harnesses." | F27, F28, and the `init` row of section 6.1 | Q3, H1, H2, A2 |
| "The operator skill should describe how to manage the default role templates." | Section 8, operator row | Q4, S1 |
| "Describe the few principles of picking harness and model." | Section 8, operator row | Q4, S1 |
| "Add few more candidate roles … documentation-writer, domain-designer, memory-curator etc." | F20 and section 7.1 | Q3, Q5, T2, T3 |

Two changes in this release are not in issue 8. Seats losing `HOME` (E1)
is a defect: seats started without it cannot find their login, and K1–K4 are the small
gaps v0.2.0 carried forward.

## 3. Gaps

| Area | Today | Requirement | Actions |
|---|---|---|---|
| Onboarding audience | Steps 1, 2, and 4 are shell work and file editing for a human; only step 3 addresses an agent, and it tells the human to paste a line | Onboarding, AI-first | Q1, A1 |
| Install step | The human installs Foil and runs `init` before any agent is involved | Onboarding step 1 | Q1, A1 |
| Init output | Two lines: the pointer and a bare `permission = "..."`. An agent that runs `init` learns nothing about the machine | Section 6.1, `init` row | Q3, A2, K3 |
| Harness discovery | `installed_harness` in `src/foil/presets.py` walks a fixed order and returns the first match; it collects nothing and never sees user presets | F27 (new) | Q3, H1 |
| Harness order | The fixed order leads with one vendor for no stated reason, and nothing says where a user preset sorts | F28 (new) | Q3, H2 |
| Harness assignment | All three default templates get the same harness, so the reviewer sits behind the same harness as the implementer even on a machine with several installed | F28, and the clean-context check the README sells | T1 |
| Role catalog | Three personas ship; the roles issue 8 names do not exist | F20 | T2 |
| Candidate roles | Nothing tells a user or an agent that other roles are possible, or how to add one | F20, section 7.1 | Q5, T3, S1 |
| Operator skill | Templates appear once, in the permission step. No word on creating one, and no principle for picking a harness or a model | Section 8, operator row | Q4, S1 |
| Seat environment | `_open` in `src/foil/lifecycle.py` forwards the preset's `env` plus `PATH`; every built-in preset sets `env = []`, so a seat starts without `HOME`. Harness CLIs keep their login under the user's home directory, so they prompt for a login or exit | F26 (new), N5 | Q2, E1 |
| K1 | Onboarding never says to run each harness once and dismiss its first-run and opt-in dialogs; the operator skill peeks only after `seat spawn`, not after `seat resume` | Onboarding step 1, section 8 | Q1, K1, S1, V3 |
| K2 | `_persona_line` returns `Read <path> untouched.`; F24 asks for the persona's text | F24 | K2 |
| K3 | `init` prints `permission = "ask"` with no explanation of the choice | Section 6.1, `init` row | K3 |
| K4 | The Claude Code operator-skill path has never been checked by hand | Onboarding table | K4 |
| Version and release notes | 0.2.0 | 0.2.1 | R2, R3 |
| Docs index | Lists one plan | D6, D8 | R1 |

## 4. Decisions

These were open when the plan started. They are closed here; the code
follows them.

### 4.1 Onboarding is AI-first, with no new command or flag

The human's whole interface becomes one sentence typed into the coding
agent they already use: `Install Foil from https://github.com/TiantianFlow/foil, onboard this repository with it, and start a fleet. My goal: <goal>.` The agent installs Foil, runs `foil init`, reads what
`init` prints, edits templates, and spawns the lead. Nothing in section 6
changes.

That makes `init` output the onboarding interface. A human reading a
README can look things up; an agent that runs `init` sees only stdout, so
`init` has to say what it found, what it wrote, and what to decide. That
is why A2 grows the output rather than adding a command.

The by-hand path stays in the README as a second section for people who
want it. Rejected: a `foil onboard` command, and flags such as
`init --harness`. Both change section 6, which is closed, and neither adds
anything the agent cannot do by editing a four-line TOML file.

### 4.2 Init reports every installed harness, and one order decides

Two words carry the rule, and F27 and F28 define both.

**Eligible** is a built-in preset or a user preset in the Foil folder's
`harnesses` directory, apart from `fake`, which is a test double and is
never reported and never chosen. A user file that reuses a built-in id
replaces that built-in: it is one id, not two, which is already how preset
loading works.

**Installed** is that preset's own `command[0]` being on `PATH`.
Detection tests the program the preset runs, not the preset id, so a user
preset that wraps some other program is found correctly.

`init` reports every installed eligible preset. Default templates still
pick one harness each, because a template needs one, and one total order
decides which:

- Sort the eligible ids by **ascending Unicode code point of the id
  string, case preserved**. One order over built-in and user ids alike,
  so no rule is needed for where user presets sort relative to built-ins.
- `lead` and `implementer` get the **first installed** id.
- `reviewer` gets the **second installed** id when one exists, and the
  first otherwise.
- `init` prints which id each template was given and why.

The comparator is spelled out because "alphabetical" is not one order
over the ids Foil actually allows. `SAFE_ID` permits uppercase, digits,
`.`, `_`, and `-`, so a second implementer could reasonably reach for a
case-folded or locale-aware collation and get a different roster from
the same folder. Code-point order over the id string is Python's default
for strings and needs no key function. Over the built-in ids it reads
`claude`, `codex`, `gemini`, `grok`, `opencode`; the cases that separate
it from the alternatives are `Alpha` before `alpha`, and `a-b` before
`a.b` before `a_b`.

Alphabetical is a tiebreak, not a ranking. Foil states no opinion about
vendors, and the order shipped today, which leads with one of them, reads
as one.

What the reviewer's second id guarantees is **two harness ids**, and
nothing more. Two ids can still run the same program or model: two presets may
wrap the same program, and two harnesses may reach the same model. Foil
cannot see which model a harness will use, so it cannot promise model
diversity, and F28 does not. The default is best-effort harness
diversity, worth having because it is free, and the operator changes it
by editing two lines.

Foil ships no vendor or model blocklist. An operator that wants one edits its templates.

### 4.3 Candidate roles ship as personas, not as templates

`init` keeps writing exactly three templates and copies **all** packaged
personas into the project. Adding a role is then writing a four-line TOML
file next to a persona that is already on disk.

Rejected: writing a template per candidate role, which leaves a project
with eight seats in its roster that nobody asked for, and `foil seat list`
and the lead's instruction file both grow accordingly. Also rejected: a
separate `candidates` folder, which adds a concept to section 7.1 to save
nothing.

The catalog, and why each role is in it:

| Role | For | Worktree | Source |
|---|---|---|---|
| `documentation-writer` | User-facing documents, the changelog, and keeping them true to the code | yes; it commits | Named in issue 8 |
| `domain-designer` | Domain boundaries, invariants, architecture, and stated tradeoffs | yes; it commits plans and requirements | Named in issue 8; shipped in v0.1.1 |
| `memory-curator` | Distilling reviewed lessons, proposing them, and superseding stale ones | no; it works through `foil memory` and the board | Named in issue 8; shipped in v0.1.1 |
| `researcher` | Bounded evidence, with verified facts separated from unknowns | no; it writes board notes | Issue 8 says "etc."; shipped in v0.1.1 |
| `verifier` | Running the checks end to end on a branch and reporting what passed | no; it reads a worktree the lead names | Issue 8 says "etc."; shipped in v0.1.1. Distinct from `reviewer`, which reads a change and argues about it |

`init` never overwrites a persona or a template that exists. Re-running it
after the operator has edited a role changes nothing.

### 4.4 Seats inherit the logged-in user

Foil always forwards `PATH` and `HOME`, and a preset's `env` adds names on
top. This is the fix for seats that die or stall at a login prompt.

The forwarding happens in the seat launch path, not in the six built-in
preset files. A user preset written by hand gets the fix too, which is the
case that produced the defect. Nothing else is added: `USER`, `SHELL`,
`TERM`, and the rest stay out unless a preset names them.

What Foil promises here is narrower than the first draft of F26 claimed.
Forwarding a variable means reading its value and handing it to the child
process; the launcher already does exactly that. Foil cannot forward
without reading. What it does not do is store, print, or log the value,
and which variables travel is declared by name, which is all N5 asks for.
F26 says it that way.

Rejected: adding `env = ["HOME"]` to each built-in preset. It fixes the
six files Foil ships and none of the files users write.

### 4.5 The command surface stays closed

Section 6 is unchanged: four commands, eleven actions. The work in this
release is behavior inside `init`, one line in the seat launch path, data
files, and documents.

The package source is 2,005 lines against N4's 2,500-line target. The
budget for this release, which keeps a margin of about 430 lines:

| Item | Lines |
|---|---|
| Harness scan and report (H1, H2) | ~30 |
| Reviewer's second harness (T1) | ~8 |
| Copying candidate personas (T3) | ~6 |
| `HOME` in the launch environment (E1) | ~3 |
| Persona text inlined (K2) | ~0, it replaces a branch |
| The permission sentence (K3) | ~2 |
| **Total** | **~50** |

## 5. Action items

Each item says what to do and how to tell it is done.

### Requirements (Q)

Behavior changes start in the requirements. Q1–Q5 are done in the same
commit as this plan, and nothing else in section 5 has started.

| ID | Action | Done when |
|---|---|---|
| Q1 | Rewrite Workflow → Onboarding for an AI-first flow: the human asks their coding agent, the agent installs Foil and runs `init`, reads the report, sets harnesses and permission, adds any candidate roles, runs the first-run check, and sends the goal. Step 1 says to run each harness once by hand and dismiss its first-run and opt-in dialogs (K1). | The onboarding section addresses the agent throughout and adds no command or flag. |
| Q2 | Add F26: seats run as the same user and inherit that login; `PATH` and `HOME` are always forwarded and a preset's `env` names more. Forwarding is declared by name, and Foil reads a named variable's value only to hand it to the child process at launch, never storing, printing, or logging it. Say the same in the `env` row of section 7.3. | F26 exists, 7.3 agrees with it, and neither claims Foil does not read a value. |
| Q3 | Keep F20 to templates and personas. Add F27 for eligibility (built-in and user presets, `fake` excluded, a user file reusing a built-in id being one id) and for installation (`command[0]` on the path). Add F28 for the one total order, its exact comparator, and the first-and-second assignment, stating that the guarantee is two harness ids, and that two ids can still run the same program or model. Every sentence that points at the choice of harness cites F28, not F27. Extend the `init` row of section 6.1 to match. | F20, F27, F28, and 6.1 describe what A2, H1, H2, T1, and T3 implement; two implementers reading them alone produce the same roster, comparator included. |
| Q4 | Extend the operator row of section 8: managing default role templates, the candidate role catalog, the principles for picking a harness and a model, and peeking a lead after `seat resume` as well as after `seat spawn`. | Section 8 names all four. |
| Q5 | Show `templates/personas/<role>.md` in the section 7.1 layout. | The layout matches what `init` writes. |

### AI-first onboarding (A)

| ID | Action | Done when |
|---|---|---|
| A1 | Rewrite the README's get-started section around the agent: one sentence the human gives their coding agent, then what the agent does. Keep the by-hand path as a second section. Mirror it in the Chinese README (D3). | A reader who only types the sentence reaches a working fleet, and both READMEs carry the same steps. |
| A2 | Make `init` print what an agent needs: every installed eligible harness in H2's order, which id each default template got and why, that the guarantee is two harness ids rather than two models, the templates and personas it wrote versus the ones it left alone, the permission sentence (K3), and the pointer line. | A test asserts the report lists every installed harness when more than one is installed, names the templates written, and states the id each one was given. |

### Harness scan (H)

| ID | Action | Done when |
|---|---|---|
| H1 | In `src/foil/presets.py`, add a scan that returns every eligible preset, built-in and user, whose own `command[0]` is on `PATH`, with `fake` excluded and a user file reusing a built-in id counted once. Keep the single-harness helper, implemented on top of the scan. | A test sees a user preset in the scan, sees `fake` excluded, sees a user file that reuses a built-in id appear once, and gets one clear error when nothing is installed. |
| H2 | Sort the scan's result by ascending Unicode code point of the id string, case preserved, as one total order over built-in and user ids. That is Python's default string order, so no key function is needed; it must not be case-folded or locale-dependent. Say in the preset module and in `init`'s output that the order is a tiebreak, not a ranking. | Tests pin the comparator, not just the built-ins: `grok` sorts before `opencode`; a user id sorts into the same list; the mixed-case pair `Alpha` and `alpha` sorts `Alpha` first; the punctuation ids `a-b`, `a.b`, and `a_b` sort in that order. No document calls the order a preference. |

### Templates and roles (T)

| ID | Action | Done when |
|---|---|---|
| T1 | Give `lead` and `implementer` the first installed id in H2's order, and `reviewer` the second installed id when one exists and the first otherwise. The guarantee recorded in the report is two different harness ids. Two ids can still run the same program or model. | Tests cover a mix of built-in and user presets: a user id that sorts between two built-ins takes its place in H2's order, `fake` on the path changes nothing, two presets wrapping the same program still count as two ids, and with one installed all three templates match and nothing fails. |
| T2 | Package personas for `documentation-writer`, `domain-designer`, `memory-curator`, `researcher`, and `verifier` in `src/foil/defaults/personas/`, in the shape of the three that are there. Each says what the role produces and what it does not do. | Eight personas ship, and each new one names its output. |
| T3 | Have `init` copy every packaged persona into the project's `templates/personas` folder, still writing only the three templates, and still overwriting nothing. | After `init`, all eight personas are on disk and three templates exist. Re-running after an edit changes no file. |

### Environment (E)

| ID | Action | Done when |
|---|---|---|
| E1 | Forward `HOME` alongside `PATH` in the seat launch path in `src/foil/lifecycle.py`. | A test reads a spawned seat's launch plan and finds `HOME`, for a built-in preset and for a user preset with an empty `env`. |

### Carried gaps (K)

| ID | Action | Done when |
|---|---|---|
| K1 | Say in the requirements (Q1), the README, and the operator skill that each harness is run once by hand and its first-run and opt-in dialogs dismissed. Tell the operator to peek a lead after `seat resume`, not only after `seat spawn`. Then re-run the crash scenario live (V3). | All three documents say it, and the live crash scenario either passes or fails for a reason other than an opt-in dialog. |
| K2 | Inline the persona's text in the instruction file instead of a path to it (F24). | A seat's instruction file contains its persona's words, and a scenario asserts on a line from the persona. |
| K3 | Print a sentence with the permission setting: what `ask` does, what `auto` does, and which file to edit. | The existing init-output test checks the sentence, not a bare assignment. |
| K4 | Check the Claude Code operator-skill path by hand, or leave it marked unverified. | The table is either marked verified with the check recorded in the changelog, or still says no. Unverified is an acceptable outcome. |

### Skills (S)

| ID | Action | Done when |
|---|---|---|
| S1 | Rewrite the onboarding part of the operator skill for the agent that runs it, and add two sections: managing role templates (the five fields, where personas live, adding a candidate role, changing a harness or model on an existing one, and that `init` overwrites nothing), and the principles for picking a harness and a model. Keep the principles few: match the model to the work, keep the reviewer off the implementer's harness whenever at least two eligible harnesses are installed, give a large context to roles that read a lot, and pair `auto` with a worktree. Say plainly that a different harness is not a different model, and that an operator who wants two models sets `model` on the templates. Add the peek after `seat resume` (K1). Do not touch the command surface. | The skill covers the catalog and the principles, and the check that every command in a skill exists in section 6 still passes. |

### Verification (V)

| ID | Action | Done when |
|---|---|---|
| V1 | Cover the new behavior with fake-harness tests: the init report, the reviewer's second harness, personas copied, re-running `init` after an edit, `HOME` in the launch plan, and the persona text in the instruction file. | All pass, and each fails when its change is reverted. |
| V2 | Report the package source size against the 2,500-line target with the new code in place. | `tests/test_package_size.py` reports a count under the target. |
| V3 | Re-run the live tier with at least one logged-in harness, including the crash scenario K1 blocked, and record the result in the changelog. | Every scenario passes live, or each failure is explained by its captured pane. |

### Documentation and release (R, P)

| ID | Action | Done when |
|---|---|---|
| R1 | Update the architecture document where the scan, the persona copy, and the launch environment changed it (D5). The docs index already lists this plan (D6, D8). | The architecture document matches the code, and the index check passes. |
| R2 | Set the version to 0.2.1 in the package and the CLI. | `foil --version` prints 0.2.1. |
| R3 | Add the 0.2.1 changelog entry: AI-first onboarding, the harness report, the candidate roles, the `HOME` fix, and K1–K4's outcome (D6). | The entry covers every action above and names the live harness from V3. |
| P1 | Before publishing, scan every commit for secrets, personal paths, private hosts, and personal emails. | The scan is clean (N9). |

## 6. Dependencies

- Q1–Q5 come first. Behavior changes start in the requirements.
- H1 before H2, T1, and A2. The report and the reviewer's harness both
  read the scan.
- T2 before T3 and S1. The skill documents personas that exist.
- K2 before V1's instruction-file assertion.
- E1 before V3. A live seat without `HOME` never starts.
- S1 after H1, T1–T3, and A2. The skill describes what `init` does.
- A1 after S1, so the README and the skill tell the same story.
- R1 after the code settles. R3 last: it describes the finished release.

## 7. Definition of done

- [ ] Every requirement in requirements.md is met, or the exception is
      written down here.
- [ ] Every action item in section 5 is done, except K4, which may stay
      unverified.
- [ ] Issue 8's five requests each trace to a requirement and to a closed
      action item (section 2).
- [ ] Scenarios 1–6 and all standing checks pass with the fake harness.
- [ ] Scenarios 1–6 pass with a real harness, or each failure is explained
      by its captured pane.
- [ ] The default roster is deterministic over its stated inputs: the
      same Foil folder and the same path, meaning each eligible preset's
      `command[0]` is found or missing the same way, produce the same
      three harness ids. A different path, or a folder holding different
      user presets, is a different input and may give a different
      roster; that is configuration, not nondeterminism.
- [ ] The documents fix the roster precisely enough that two
      implementers reading only them agree, down to the comparator.
- [ ] Every guarantee stated is one Foil can keep. Harness diversity is
      claimed; model diversity is not.
- [ ] The command surface is unchanged: four commands, eleven actions.
- [ ] The package source is within the N4 target.
- [ ] A fresh install, driven only by a coding agent given the one
      sentence in the README, reaches a lead that writes `state: done`.

## 8. Out of scope for 0.2.1

- Any new command, subcommand, or flag. Section 6 is closed.
- A vendor or model blocklist in the product.
- Templates for the candidate roles. `init` writes three; the operator
  adds the rest.
- Reading a harness's own configuration to learn which models it can run.
  Foil would then have to track five vendors' formats.
- Harness system-prompt flags. The launch prompt stays the delivery path,
  as decided in 0.2.0.

## 9. Acceptance review (2026-09-29)

Reviewed at commit `f984e6d` in this order: the requirements changes,
this plan, then the code, tests, skills, and documents. The checks were
the full test suite, lint, the package size report, a hand run of
`foil init` in a scratch repository with two fake harnesses on `PATH`,
and a scan of all 13 commits on the branch.

**Verdict: not accepted yet.** The requirements and the decisions in
section 4 are sound, and most of the code is small and correct. One
blocker remains: the one sentence the human types (C1) cannot start
onboarding for an agent that has never heard of Foil, and that sentence
is the headline of this release. Two further items (C3, C4) are
behavior bugs that an agent will hit on its first run. The rest are
small. Everything below fits in the line budget in section 4.5.

### 9.1 Checks

| Check | Result |
|---|---|
| Tests | Pass: 115 pass, 6 live tests skip as designed |
| Lint (`ruff check .`) | Pass |
| Package size | Pass: 2,100 of 2,500 lines |
| Command surface | Pass: four commands, eleven actions, unchanged |
| Hand run of `foil init` | Mostly pass. The report, the reviewer's second harness, and the persona copy all behave as written. C3, C4, and C9 were found here |
| Live tier (V3) | **Not run.** The changelog says so |
| Agent-driven fresh install (section 7, last item) | **Not run.** Scenario 7 is scripted, so it cannot show this |
| Publishing safety (P1) | Pass: no secrets, personal paths, private hosts, or personal emails. Every commit carries a `Co-authored-by: Cursor` trailer; that is the owner's call, not a defect. Three lines of this plan describe the fleet that built it rather than the product (C11) |

### 9.2 What is done well

- **Requirements.** F26 is honest about what forwarding means: Foil reads
  a value only to hand it to the child process. F27 and F28 make the
  default roster deterministic, and F28 says plainly that it promises two
  harness ids, not two models.
- **Plan.** Every line of issue 8 traces to a requirement and to action
  items (section 2). The rejected alternatives in section 4 are stated
  with reasons, and there is a line budget.
- **Code.** `installed_presets` in `src/foil/presets.py` is short and
  follows F27 exactly: `fake` is excluded, a user file that reuses a
  built-in id counts once, and detection checks the preset's own
  `command[0]`. `HOME` is forwarded in the launch path in
  `src/foil/lifecycle.py`, so hand-written presets get the fix too. The
  persona text is now inlined (K2). `init` copies every persona and
  overwrites nothing (T3).
- **Tests.** The comparator cases from H2 (`Alpha` before `alpha`; `a-b`,
  `a.b`, `a_b`) are pinned. Scenario 7 checks that `HOME` is forwarded by
  name and never stored in the plan file.

### 9.3 Action items

Do them in the order listed. C1 is the blocker. Each item names the
files to touch and how to tell it is done. Keep the command surface
unchanged, and run `uv run --frozen --extra dev pytest -q` and
`uv run --frozen --extra dev ruff check .` before every push.

| ID | Severity | Action | Done when |
|---|---|---|---|
| C1 | Blocker | **Make the human's sentence able to start onboarding.** Today it is `Onboard this repository with Foil and start a fleet. My goal: <goal>.` An agent that does not already know Foil has no install source, and the operator skill only exists after `foil init` has run, so the agent cannot find its instructions. Put the install source in the sentence. For example: `Install Foil from https://github.com/TiantianFlow/foil, onboard this repository with it, and start a fleet. My goal: <goal>.` Change it in four places, word for word: Onboarding step 2 in `docs/requirements.md`, the Get started code block in `README.md` and in `README.zh-CN.md` (keep the sentence in English in both, as the pointer line already is), and decision 4.1 in this plan. | The same sentence appears in all four places. A new test in `tests/test_skills.py` reads both READMEs and asserts that the sentence contains `github.com/TiantianFlow/foil`. |
| C2 | Medium | **Say who dismisses the first-run dialogs.** Onboarding step 1 in `docs/requirements.md` and step 1 of the operator skill (`src/foil/defaults/skills/operator.md`) tell the reader to run each harness once by hand and dismiss its dialogs. An agent cannot click through another CLI's interactive dialog. Reword both so that the agent asks the human to do it, or to confirm it is done, before the first spawn, and so that a dialog seen in `foil seat peek` is reported to the human, not answered by the agent. Edit only the packaged skill: `skills/operator.md` is a symlink to it. | Both texts name the human as the one who dismisses dialogs. |
| C3 | High | **One broken user preset must not break every `init`.** In `installed_presets` (`src/foil/presets.py`), the loop calls `load_preset(toplevel, harness_id)` on every file in `.foil/harnesses/`. If any one file is invalid, `load_preset` raises and `foil init` exits with `foil: invalid preset`, without saying which file. Reproduce it: write `id = "zz"` to `.foil/harnesses/zz.toml` and run `foil init`. Fix: wrap that one call in `try` / `except FoilError`, skip the preset, and return the skipped ids alongside the found presets (or collect them in a second list). In `_print_init_report` (`src/foil/lifecycle.py`), print one line per skipped file, for example `Skipped .foil/harnesses/zz.toml: invalid preset`. Do not change `load_preset` itself: `foil seat spawn` must still fail loudly when a template names a broken preset. | A test in `tests/test_presets.py` puts one invalid and one valid user preset on disk and gets the valid one back from the scan. A test in `tests/test_init.py` checks that `init` exits 0 and prints the skipped file's name. The existing spawn tests still pass. |
| C4 | Medium | **The permission sentence must cover all three templates.** `_print_init_report` prints only the lead's permission and ends with `Edit .foil/templates/lead.toml.` That tells the agent the opposite of the rule in the onboarding docs, which is that `auto` on the lead alone does not let the workers run unattended. Print each template's value, then one sentence. For example: `permission: lead ask, implementer ask, reviewer ask.` followed by `ask stops a seat at its first approval prompt; auto lets it run unattended. Set it in each .foil/templates/<role>.toml; auto on the lead alone does not let the workers run unattended.` Update the assertion at `tests/test_init.py` lines 93–94 to match. | The report names all three values and the per-template rule, and the updated test passes. |
| C5 | Medium | **Tell the lead that candidate roles exist.** The lead staffs the fleet, but `src/foil/defaults/skills/lead.md` never mentions `.foil/templates/personas/`. A lead that needs a documentation writer cannot know one is ready. After the roster paragraph (line 55), add two or three sentences: the packaged personas are in `.foil/templates/personas/`; to add a role, write `.foil/templates/<role>.toml` with `harness`, `persona = "personas/<role>.md"`, `worktree`, and `permission`; a role that commits needs `worktree = true`. Add the same point to the lead row of section 8 in `docs/requirements.md`, because behavior changes start in the requirements. Do not add a command: `test_skill_commands_exist_in_section_6` must still pass. | The lead skill and the requirements both say it, and the skill tests pass. |
| C6 | Medium | **Record the changed default order.** Before this release, a fresh `init` on a machine with several CLIs picked `grok` first. It now picks the first id in code-point order (`claude` when it is installed). An upgrading user who re-creates their templates will see a different harness. Add a bullet under `### Changed` in the 0.2.1 entry of `CHANGELOG.md` that says so. | The changelog names the old order and the new one. |
| C7 | Low | **Reword the harness-count line.** The report prints `The three default templates use two harness ids, not two programs and not two models.` A reader takes that as "these are definitely not two programs", which is not what F28 means. Print instead: `The three default templates use two different harness ids. Two ids can still run the same program or model; set model on a template to choose one.` Keep the counting logic in `_print_init_report` as it is. Update `tests/test_init.py` (lines 109, 162, 197), `tests/e2e/test_scenarios.py` (lines 617 and 652), and the sentence "uses that same count for programs and models" in `CHANGELOG.md` and `docs/architecture.md` line 101. | No document or test contains "not two programs", and the tests pass. |
| C8 | Low | **Fail on a persona path that does not exist.** `persona_text` in `src/foil/presets.py` returns the raw string when the file is missing, so `persona = "personas/verfier.md"` (a typo) silently becomes the persona text `personas/verfier.md`. This matters now that adding a role means writing a template by hand. When the value has no newline, ends in `.md`, and the file does not exist, raise `FoilError("foil: persona file not found: <value>")`. Single-line inline text that does not end in `.md` stays inline text (F22). | A test in `tests/test_spawn.py` spawns from a template with a misspelled persona path and gets that one-line error. The existing inline-persona tests still pass. |
| C9 | Low | **Handle a closed output pipe.** `foil init \| head -3` prints `foil: unexpected error`, because `main` in `src/foil/cli.py` turns the `BrokenPipeError` into that message. The report is now long enough that an agent may cut it off this way. Add an `except BrokenPipeError` clause before the generic one. It should point `sys.stdout` at `os.devnull` (the pattern the Python documentation gives under "Note on SIGPIPE") and return 1 without printing anything. | A test runs `foil init` through a pipe that closes after one line and finds nothing on stderr. |
| C10 | Medium | **Make the README's Get started readable, and fix the dangling reference.** The paragraph after the sentence in `README.md` is one block about id order, re-run behavior, and template fields. Keep the details in the operator skill, and rewrite the README part as the sentence (C1), then four short bullets: the agent installs Foil and runs `foil init`; it reads the report and sets each template's harness, model, and permission; it runs the first-run check and sends the goal; it never does the project work itself. Keep the sentence that contains "`auto` on the lead alone does not let the workers run unattended" (a test requires it), and keep the three template file names. The install table's row says "the pointer line above", but the README no longer shows the pointer line. Add it back as the fallback, for an agent that was started without the sentence, in its own code block above the table. Mirror all of it in `README.zh-CN.md` (D3), keeping the phrase `不会让工人席位无人值守`. | Both READMEs have the same bullets and the pointer line above the table, and `tests/test_skills.py` and `tests/test_docs_hygiene.py` pass. |
| C11 | Low | **Keep this plan about the product.** Three lines describe the fleet that built this release. In section 2, change "is a live defect found while running this fleet" to "is a defect: seats started without it cannot find their login". In section 4.2, delete the sentence "This fleet bans a specific model." and start the paragraph at "Foil ships no vendor or model blocklist." In the `researcher` row of the catalog table in section 4.3, delete ", and this release was planned from a digest written by one". | None of the three phrases remains. |
| C12 | Release gate | **Run what section 7 asks for, and record it.** (1) Run the live tier with at least one logged-in harness: `FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live`. Include scenario 3, which K1 was about, and use `permission = "auto"` as the live tier does. (2) In a fresh, disposable repository, give one real coding agent only the sentence from C1, and check that it reaches a lead whose `status.md` says `state: done`. (3) Write both results in the 0.2.1 entry of `CHANGELOG.md`, replacing the known issue that says the live tier was not run. If a scenario fails, record the reason shown in its pane capture. (4) Tick the boxes in section 7 that are now true. | The changelog names the harness used and both results, and section 7 is up to date. |

F28's wording in `docs/requirements.md` is precise but long: it repeats the
regular expression and the comparator examples from section 4.2 of this
plan. Trimming it is optional and can wait for a later release.

### 9.4 Decision

Do C1–C11, then C12, on this branch. After that, and a clean publishing
scan (P1), 0.2.1 can be merged and tagged. If a harness's own dialog
blocks the live run in C12, that alone does not block the release,
provided the pane capture shows the dialog and the changelog says so, as
in 0.2.0.

## 10. Second acceptance review (2026-09-29)

Reviewed at commit `aded578` on `foil/verifier-4`, which contains
section 9 and four commits after it. The checks were the full test
suite, lint, the package size report, a hand run of `foil init` and
`foil seat spawn` in a scratch repository, a reading of every changed
file against section 9's items, and a scan of the four new commits.

**Verdict: not accepted yet, but close.** C1–C11 are done, and done
well. The code is correct apart from one new edge case (G4). The two
release checks in C12 were run and reported honestly, but neither one
tested this release. The live tier stopped on Claude Code's
folder-trust dialog before any scenario ran. The fresh-agent check
installed the published 0.2.0 from `main`, not this branch. The
folder-trust dialog also matters beyond the tests: `claude` is now the
first harness in the default order, so it is the default for most
users (G1). Three release gates (G1–G3) and five small fixes remain.

### 10.1 Checks

| Check | Result |
|---|---|
| Tests | Pass: 122 pass, 6 live tests skip as designed |
| Lint (`ruff check .`) | Pass |
| Package size | Pass: 2,128 of 2,500 lines |
| Command surface | Pass: unchanged |
| Hand run of `foil init` | Pass. A broken user preset is skipped and named, the permission line lists all three templates, and `foil init \| head -1` prints nothing on stderr. One wording issue (G6) |
| Hand run of `foil seat spawn` with a misspelled persona | The error is correct, but it comes after the worktree is created (G4) |
| Live tier | **Did not test this release.** Scenario 1 stopped on Claude Code's folder-trust dialog; scenarios 2–6 were not started (G1, G2) |
| Fresh-agent onboarding | **Did not test this release.** The agent installed 0.2.0 from `main` (G3) |
| Publishing safety | Pass: the four new commits add no secrets, personal paths, private hosts, or personal emails. The three internal sentences are gone from the plan. They are still in older commits on this branch (G9) |

### 10.2 Section 9 items

| ID | Status |
|---|---|
| C1 | Done. The sentence names the install source in the requirements, both READMEs, and section 4.1, and `test_onboarding_sentence_names_the_install_source` checks both READMEs. |
| C2 | Done. The requirements and the operator skill say the human dismisses the dialogs, and a dialog seen in `foil seat peek` is reported to the human, not answered by the agent. G1 sharpens this for folder-trust dialogs. |
| C3 | Done. `installed_presets` returns the skipped files, `init` names them, and `foil seat spawn` still fails on a broken preset. Both cases are tested. |
| C4 | Done. The report prints each template's permission and the per-template rule. |
| C5 | Done, in the lead skill and in the requirements. |
| C6 | Done. |
| C7 | Done, apart from the one-id case (G6). |
| C8 | Done, and it also rejects a path that leaves the template folder. F22 says so. Two follow-ups are in G4. |
| C9 | Done and tested. |
| C10 | Done. The READMEs show four bullets and the pointer line above the install table. |
| C11 | Done. |
| C12 | Run and recorded, but neither check exercised this release. See G1–G3. |

### 10.3 Action items

Do G1–G3 before tagging. G4–G8 are small and can go in the same
commits. Run `uv run --frozen --extra dev pytest -q` and
`uv run --frozen --extra dev ruff check .` before every push.

| ID | Severity | Action | Done when |
|---|---|---|---|
| G1 | Release gate | **Find out whether Claude Code's folder-trust dialog blocks real use, and say what to do about it in the docs.** Claude Code asks "Is this project one you trust?" the first time it starts in a folder. `claude` is now the first harness in the default order, so this dialog is the first thing most users hit. There are two questions. (1) Does trusting the repository once cover the seats? Check by hand. In a real repository, run `claude` once in the repository root and accept the dialog. Then run `foil init`, set `permission = "auto"` on all three templates, `foil seat spawn lead --task "Write board/status.md with state: done"`, and `foil seat spawn implementer`, which starts in `.foil/worktrees/implementer-1`. Run `foil seat peek lead` and `foil seat peek implementer-1`, and write down whether either shows the trust dialog. (2) Fix the docs either way. Onboarding step 1 in `docs/requirements.md`, step 1 of the operator skill (`src/foil/defaults/skills/operator.md`), and the prerequisites paragraph of both READMEs say to run each harness once. Change that to run each harness once **in this repository** and accept its folder-trust prompt as well as its first-run and opt-in dialogs, because trust is per folder. If step (1) shows that the worktree seat asks again, add a known issue to the changelog saying so and how to answer it (`tmux attach`, then accept), and record it here as a gap for the next release. Do not add a harness flag in this release: that is a design decision for the owner. | Both results from step (1) are written in this plan, the four documents say "in this repository", and the changelog has a known issue if a worktree seat asks again. |
| G2 | Release gate | **Let the live tier run with a chosen harness.** `tests/e2e/test_live.py` always uses whatever `foil init` picks, so on a machine where `claude` is installed, every scenario starts `claude` in a fresh temporary folder and waits on the trust dialog. Add a test-only environment variable, `FOIL_E2E_HARNESS`. When it is set, the live tests rewrite the `harness` line of the three templates after `init`, the same way `_set_auto` rewrites `permission`. It is read only by the tests, so it adds nothing to the command surface. Then run `FOIL_E2E_LIVE=1 FOIL_E2E_HARNESS=codex uv run --frozen --extra dev pytest tests/e2e -m e2e_live`, or use any other harness that is logged in and has no folder-trust dialog. Record the result in the changelog: the harness used, and for each scenario, pass, or fail with the reason shown in its pane capture. | Scenarios 1–6 have all been started with at least one real harness, and each one either passed or has its pane's reason recorded. |
| G3 | Release gate | **Run the fresh-agent check against this branch, without touching the machine's own `foil`.** The sentence installs from `main`, which is 0.2.0 until this release is merged, so the last run tested the old code. It also replaced the machine's own `foil` command. For the check, change only the install source in the sentence: `Install Foil with uv tool install "git+https://github.com/TiantianFlow/foil.git@foil/verifier-4", onboard this repository with it, and start a fleet. My goal: <goal>.` Give it to one new coding-agent process, in a new disposable repository, with `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` set to folders inside a temporary directory and that bin folder first on `PATH`. The install then stays out of the machine's own tools. Choose a harness for which step (1) of G1 passed. Stop when `board/status.md` says `state: done`, or when a seat waits on a dialog, and capture that pane. Write in the changelog what `foil --version` printed (it must be 0.2.1), which harness was used, and where it stopped. | The record shows 0.2.1 and either `state: done` or the captured reason it stopped. |
| G4 | Medium | **Check the persona before creating the worktree.** `spawn_seat` in `src/foil/lifecycle.py` (line 398) creates the worktree and the `foil/<seat>` branch (line 412) before `_open` writes the instruction file, which is where `persona_text` raises. A template with a misspelled persona path therefore leaves an unregistered branch and worktree behind, and the next attempt gets `foil/implementer-2`. To reproduce: give the implementer template `persona = "personas/implementr.md"`, spawn the lead, then spawn `implementer`. You get the error, and `git worktree list` still shows `.foil/worktrees/implementer-1`. Fix: call `persona_text(loaded)` right after `load_template` in `spawn_seat`, and in `_restart`, and discard the result. Also give the "leaves the template folder" case in `persona_text` (`src/foil/presets.py`) its own message, for example `foil: persona path must stay inside .foil/templates: <value>`. Today an absolute or `..` path that exists is reported as "not found", which sends the reader looking for a missing file. | A test in `tests/test_spawn.py` spawns an implementer with a misspelled persona and finds no `foil/implementer-1` branch and no extra worktree. A second test checks the new message for a `..` path. |
| G5 | Medium | **Keep the changelog for users.** The 0.2.1 entry in `CHANGELOG.md` has a "C12 verification" subsection that names a plan item, a verifier seat, the agent program used, and cleanup of the verifier's machine. Readers of a changelog need the outcome, not the procedure. Move that subsection, as written, into this plan as section 10.4. In the changelog, keep only two short known-issue bullets: which harness the live tier used and how it ended, and how the fresh-agent check ended. Leave out plan IDs and tool names. Set the date in the `## [0.2.1]` heading on the day you tag, not before. | The changelog has no "C12", no verifier seat, and no agent program name, and this plan holds the full record. |
| G6 | Low | **Fix the report line when all three templates use one id.** With one harness installed, `init` prints `The three default templates use one different harness id. Two ids can still run the same program or model; ...`, which makes no sense for one id. In `_print_init_report` (`src/foil/lifecycle.py`), print only `The three default templates use one harness id.` when the count is 1, and keep the current two sentences otherwise. Update the one-harness assertion in `tests/test_init.py`. | With one harness installed, the report has no "different" and no "Two ids". |
| G7 | Low | **Tick section 7.** Every box there is still unticked, but most are now true: the command surface, the package size, the fake-harness scenarios, determinism, the traceability in section 2, and the guarantees. Tick those. Leave the real-harness and fresh-install boxes unticked until G2 and G3 are done, with one line after each that points at G2 or G3. | Section 7 matches the facts. |
| G8 | Low | **Fix one sentence in section 2.** "Seats losing `HOME` (E1) is a defect: seats started without it cannot find their login, and K1–K4 are the small gaps v0.2.0 carried forward." runs two points together. Make it two sentences: "Seats losing `HOME` (E1) is a defect: a seat started without it cannot find its login. K1–K4 are the small gaps v0.2.0 carried forward." | Section 2 reads as two sentences. |
| G9 | Owner's call | **Decide how to merge.** The removed internal sentences (section 9, C11) are still in older commits on this branch, and every commit has a `Co-authored-by: Cursor` line. Neither is a secret. A squash merge keeps both out of `main`'s history. A merge commit keeps them. | The owner has picked one. |

### 10.4 Decision

Do G1–G8, then merge with the method chosen in G9 and tag 0.2.1. As in
0.2.0, a harness's own dialog that stops a live scenario does not block
the release, provided the pane capture shows the dialog and the
changelog says how to get past it. What does block the release is
shipping a new default order without knowing whether its first harness
can start a seat in a worktree (G1).

## 11. Process review (2026-09-29)

This section is about how the work is checked, not about the code.
0.2.1 has now had three review rounds: C1–C12 in section 9, G1–G9 in
section 10, and this one. The code at `79000e8` is unchanged since
section 10, so G1–G9 still stand as that section's action items.

**Verdict: the rounds are costly because the implementation meets each
item's "done when" line without checking the goal behind it.** The
individual items were done well. Most of what the reviews found could
have been caught before the first review with a short self-review that
looked beyond the checklist.

### 11.1 What the rounds found, by cause

| Cause | Examples | A self-review would have caught it by |
|---|---|---|
| Not run as a user would run it | The onboarding sentence gave no install source (C1). The fresh-agent check ran 0.2.0 from `main` (G3). | Trying the documented sentence, word for word, in an empty folder with a coding agent that had no other context |
| Not run on failure paths | One broken preset stopped every `init` (C3). A misspelled persona left an orphaned worktree and branch (G4). `foil init \| head` printed an error (C9). | Giving each new code path a bad input and then checking what was left on disk |
| One change, several places | The permission line said "edit `lead.toml`" while the docs said each template has its own permission (C4). The pointer line was removed from the README but the table still pointed "above" to it (C10). | Searching every document, skill, and test for the old wording after each change |
| Tests that assert current output | The tests pinned the exact text "not two programs and not two models" and the lead-only permission sentence, so the tests passed while the behavior was wrong (C4, C7). | Asking what each test proves, and checking that it fails when the fix is reverted |
| Not written for a public reader | The plan described the fleet that built it (C11). The changelog recorded the verifier's procedure (G5). F28 repeats the plan's examples. | Reading every added line as a stranger to the project would |

### 11.2 Principles this project already states

Most of the above is ordinary engineering practice. Four points are
written rules in this repository, and they were missed:

- **Requirements, section 10:** "Tests check real outcomes (tests pass, a
  branch exists, a window is gone), not Foil's reports about itself."
  Many new tests assert the text of the `init` report. A check that ran
  against a different binary did not test this release at all.
- **Requirements, Onboarding:** the reader is a coding agent that has
  nothing but the sentence. Only a cold run shows whether that is
  enough.
- **N9, and "Everything you commit is public" in CONTRIBUTING.md:**
  documents are public, so they hold the product, not the process.
- **Every guarantee stated is one Foil can keep (section 7):** this
  also applies to reports about verification. "Checked" means the check
  exercised this release.

The reviews share part of the cost. Each round hand-ran only some paths,
so some findings appeared one round later than they could have (G4
follows from C8). Section 11.3 applies to review rounds too.

### 11.3 Action items

| ID | Action | Done when |
|---|---|---|
| M1 | Before asking for a review, run the self-review checklist below, and put a short record of it in the commit message or the request: what was run, against which version, and what was left unverified. | The next review request has that record. |
| M2 | For each test added or changed, revert the fix locally, run the test, and see it fail. Assert on files, branches, windows, and exit codes before asserting on printed text. | Every new test has been seen failing without its fix. |
| M3 | After a behavior or wording change, search the repository for the old phrasing (for example `grep -rn "lead.toml" README* docs skills src tests`) and update every hit in the same commit. | No document, skill, or test contradicts the change. |
| M4 | Report a check that did not exercise this release as "not run", with the reason, not as a result. | The changelog and this plan hold only results from this release's code. |

The self-review checklist for M1:

1. **Goal.** For each item, write one sentence on why it exists. Check
   the goal, not only the "done when" line.
2. **Run it as a user.** Use a fresh folder and install from this branch
   into a temporary tool directory. Use only the documented commands,
   or the documented sentence given to an agent with no other context.
3. **Break it.** Try a missing file, an invalid file, none, one, many, a
   second run, an interrupted run, and output sent to a pipe. After each
   failure, look at what is left on disk.
4. **Search for the old wording.** Every document, skill, test, and
   message must say the same thing.
5. **Test the tests.** Each new test fails without its fix and checks an
   outcome.
6. **Read the diff as a stranger.** Is it public-safe, free of process
   notes, and no longer than it needs to be?
7. **Report honestly.** Say what was verified, against which version,
   and what was not.
