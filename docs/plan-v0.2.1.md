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
is a live defect found while running this fleet, and K1–K4 are the small
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
agent they already use: *onboard this repository with Foil and start a
fleet on this goal*. The agent installs Foil, runs `foil init`, reads what
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

- Sort the eligible ids **alphabetically**: `claude`, `codex`, `gemini`,
  `grok`, `opencode`, with any user id in its alphabetical place among
  them. One order over built-in and user ids alike, so no rule is needed
  for where user presets sort relative to built-ins.
- `lead` and `implementer` get the **first installed** id.
- `reviewer` gets the **second installed** id when one exists, and the
  first otherwise.
- `init` prints which id each template was given and why.

Alphabetical is a tiebreak, not a ranking. Foil states no opinion about
vendors, and the order shipped today, which leads with one of them, reads
as one.

What the reviewer's second id guarantees is **two harness ids**, and
nothing more. It is not two programs and not two models: two presets may
wrap the same program, and two harnesses may reach the same model. Foil
cannot see which model a harness will use, so it cannot promise model
diversity, and F28 does not. The default is best-effort harness
diversity, worth having because it is free, and the operator changes it
by editing two lines.

This fleet bans a specific model. That is a local operating rule for one
fleet and does not enter the product: Foil ships no vendor or model
blocklist. An operator that wants one edits its templates.

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
| `researcher` | Bounded evidence, with verified facts separated from unknowns | no; it writes board notes | Issue 8 says "etc."; shipped in v0.1.1, and this release was planned from a digest written by one |
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
| Q3 | Keep F20 to templates and personas. Add F27 for eligibility (built-in and user presets, `fake` excluded, a user file reusing a built-in id being one id) and for installation (`command[0]` on the path). Add F28 for the one alphabetical total order and the first-and-second assignment, stating that the guarantee is two harness ids and not two programs or models. Extend the `init` row of section 6.1 to match. | F20, F27, F28, and 6.1 describe what A2, H1, H2, T1, and T3 implement, and an implementer reading them alone produces one roster. |
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
| H2 | Sort the scan's result alphabetically by id, as one total order over built-in and user ids: `claude`, `codex`, `gemini`, `grok`, `opencode`, with user ids in their alphabetical place among them. Say in the preset module and in `init`'s output that the order is a tiebreak, not a ranking. | The order is alphabetical with `grok` before `opencode`, a user id sorts into the same list, and no document calls the order a preference. |

### Templates and roles (T)

| ID | Action | Done when |
|---|---|---|
| T1 | Give `lead` and `implementer` the first installed id in H2's order, and `reviewer` the second installed id when one exists and the first otherwise. The guarantee recorded in the report is two harness ids, not two programs and not two models. | Tests cover a mix of built-in and user presets: a user id that sorts between two built-ins takes its alphabetical place, `fake` on the path changes nothing, two presets wrapping the same program still count as two ids, and with one installed all three templates match and nothing fails. |
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
- [ ] The default roster is deterministic: the same machine and the same
      Foil folder produce the same three harness ids, and the documents
      say so precisely enough that two implementers agree.
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
