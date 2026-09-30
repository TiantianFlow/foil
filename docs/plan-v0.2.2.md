# Foil v0.2.2 plan: a progress checklist

Target: [requirements.md](requirements.md). Issue: TiantianFlow/foil #11
(Progress report). Baseline: `main` at 0.2.0 plus the README work, with
0.2.1 (issue #8, AI-native onboarding) expected to merge first. Requirement
IDs (F, N, D) refer to the requirements document.

Status: proposed, awaiting cross-review. Nothing below is implemented yet.

## 1. What the issue asks for

Issue #11: "Skill or foil built-in. Find a way to read status. The lead
should keep track of a progress doc. Best to use checklist/action item
style, so we can know where things are. It could just about making the
operator skill report more status if the tracker is already there."

| # | Need |
|---|---|
| N-a | The lead keeps one progress document for the goal. |
| N-b | It is a checklist of action items, so anyone can see what is done and what is left. |
| N-c | The human has a way to read it. |
| N-d | The operator reports it to the human, not only the lead's questions. |

The tracker is already there: the lead keeps `board/status.md` under the
`status/v1` contract. Its front matter is fixed, but its body is not
specified, and the operator skill reads the file without reporting
progress from it.

## 2. Decisions

- Progress stays in `board/status.md` (`status/v1`). The body is a
  `## Checklist` of `- [ ]` and `- [x]` items. The front matter keys do not
  change, so the contract stays `status/v1`.
- No command, subcommand, or flag is added (F1, section 6). Foil still never
  reads contracts (F16) or panes (F12), and `seat list` still reports only
  facts Foil owns (F10). A `foil status` command would break all three.
- No behavior change in the package's Python. The only `.py` edit in the
  package is the version string in `src/foil/__init__.py` (R2). Tests do
  change.
- Requirements text changes: 7.4 describes the checklist, and the
  section 8 operator and lead rows each gain one sentence (CONTRIBUTING
  rule 1).
- Version 0.2.2 assumes 0.2.1 (#8) merges first. This work does not base on
  the unmerged #8 branch.
- Out of scope: everything #8 and #10 own (onboarding, the `init` report,
  harness discovery and choice, personas and candidate roles, roster
  management).

## 3. Baseline in numbers

| Measure | Now | After 0.2.2 |
|---|---|---|
| Top-level commands and actions | 4 and 11 | 4 and 11 |
| Package Python (N4 target 2,500) | 2,005 lines on `main` | unchanged by this release |
| Skills | 3 | 3 |
| Contracts | `mail/v1`, `status/v1`, `task/v1`, `result/v1` | the same |

## 4. Action items

Each item lists what to do and how to tell it is done. Proposed wording is
in section 5; reviewers may tighten it, but not widen the scope.

### Requirements (Q)

| ID | Action | Done when |
|---|---|---|
| Q1 | In 7.4, add a checklist paragraph below the contracts table (5.1). | 7.4 defines the `## Checklist` body, `- [ ]` and `- [x]`, the task id on an item, and `state: done` meaning every item is ticked. The `status/v1` front matter row is unchanged. |
| Q2 | Append one sentence to the section 8 `operator` row (5.2). | The row ends with that sentence; nothing earlier in the row changes. |
| Q3 | Append one sentence to the section 8 `lead` row (5.3). | The row ends with that sentence; nothing earlier in the row changes. |

### Skills (S)

| ID | Action | Done when |
|---|---|---|
| S1 | Lead skill, "Board and contracts": after the `status/v1` sentence, add the checklist rules and one example (5.4). Edit `src/foil/defaults/skills/lead.md`; `skills/lead.md` is a link to it. | The lead skill covers Q3. No other section of the file changes. |
| S2 | Operator skill, "Check in": add the report paragraph at the end of that section (5.5). | The operator skill covers Q2 using only commands it already lists. No other section of the file changes. |
| S3 | Worker skill: no change. Workers do not write `status.md`, and its `status/v1` sentence stays true. | `src/foil/defaults/skills/worker.md` is unchanged. |

### Documentation (D)

| ID | Action | Done when |
|---|---|---|
| D1 | `README.md`: in the paragraph that says `status.md` is the lead's own report, add that it carries a checklist of what is done and what is left, and that the operator reports it when asked. | One paragraph changes. |
| D2 | `README.zh-CN.md`: the same change (D3). | The paragraph matches D1. |
| D3 | `docs/demo.md` section 6: show the checklist in the sample `status.md`, taken from the scenario 1 fixture (T4). | The sample equals what scenario 1 writes. |
| D4 | `docs/demo.zh-CN.md`: the same sample. | The sample matches D3. |
| D5 | `docs/README.md`: list this plan (D8). | The index test passes. Done in the commit that adds this plan. |
| D6 | `docs/architecture.md`: no change. It already says Foil does not read `status.md`. | Re-read after S1 and S2; still accurate. |

### Tests (T)

New tests go at the end of `tests/test_skills.py`, after the tests #8 adds.

| ID | Action | Done when |
|---|---|---|
| T1 | `test_lead_skill_keeps_a_checklist`: the lead skill contains `## Checklist`, `- [ ]`, `- [x]`, and `every item is ticked`. | Fails if S1 is reverted. |
| T2 | `test_operator_reports_the_checklist`: the operator skill's "Check in" section (up to the next `## ` heading) mentions the checklist, "whenever the human asks", and not guessing progress from the pane. | Fails if S2 is reverted or moved out of "Check in". |
| T3 | `test_requirements_define_the_checklist`: 7.4 contains `## Checklist`; the section 8 `operator` and `lead` rows each contain "checklist". | Fails if Q1, Q2, or Q3 is reverted. |
| T4 | Scenario fixtures: `tests/fixtures/scenario-1/scripts/lead.json` writes a status body with a ticked checklist (5.6). Add a test that this body appears verbatim in both demo files. Scenario assertions do not change: they still check real outcomes. | Scenario 1 passes; the demo test fails if the demo and fixture drift. |
| T5 | Standing checks unchanged: `tests/test_command_surface.py`, the size report, and `tests/test_docs_hygiene.py` pass with no edits to them. | CI's four commands in CONTRIBUTING pass. |

### Release (R)

| ID | Action | Done when |
|---|---|---|
| R1 | Once 0.2.1 (#8) is on `main`, rebase this work onto it and resolve the overlaps in section 6. If #8 has not merged when this work is accepted, stop and ask the human. | The branch sits on a `main` that contains 0.2.1; every check passes after the rebase. |
| R2 | Set the version to 0.2.2: `pyproject.toml`, `src/foil/__init__.py`, `uv.lock`, and `tests/test_version.py`. | `foil --version` prints 0.2.2. |
| R3 | Add the 0.2.2 entry to `CHANGELOG.md` above 0.2.1, with its release link at the bottom (D6). | It says what changed and that no command, flag, or contract version was added. |
| R4 | Run the CONTRIBUTING self-review (added by 0.2.1) and scan every commit, not only the tree, for secrets, personal paths, private hosts, and personal emails (N9). | Both are clean. Nothing is pushed until the human asks. |

## 5. Proposed wording

### 5.1 Q1, requirements 7.4 (below the contracts table)

> **Checklist.** Once the lead has a plan, the `status/v1` body holds a
> `## Checklist` of Markdown task items, one per line: `- [ ]` open,
> `- [x]` done. An item starts with its task id when it has one.
> `state: done` means every item is ticked. The front matter is unchanged,
> and Foil does not read the body (F16).

### 5.2 Q2, section 8 `operator` row (appended)

> Reporting progress to the human from the `status.md` checklist, at each
> check-in and whenever the human asks, quoting the file rather than the
> pane.

### 5.3 Q3, section 8 `lead` row (appended)

> Keeping the `status.md` checklist: one item per task and per lead step,
> ticked when done, with `state: done` only when every item is ticked.

### 5.4 S1, lead skill

After "Keep `board/status.md` current. Contract `status/v1`: ...":

> Once you have a plan, the body holds a `## Checklist` of action items,
> one line each: `- [ ]` open, `- [x]` done. Give each task file one item
> that starts with its id and names its owner. Add your own steps too:
> integrate, review, accept. Tick an item when its result says `pass` or
> the step is done. A blocked item says `blocked` on its line, its question
> goes in `questions`, and `state` is `blocked`. Set `state: done` only when
> every item is ticked. Refresh `updated` on every edit.

```text
## Checklist

- [x] t1 Fix the failing test (implementer-1)
- [ ] t2 Review the fix (reviewer-1)
- [ ] Merge foil/implementer-1
```

### 5.5 S2, operator skill, end of "Check in"

> After each check-in, and whenever the human asks for status, tell the
> human in a few lines: `state` and `updated` as the file says them; how
> many checklist items are ticked out of the total, and the open items as
> written; any `questions`; and which seats `foil seat list` shows alive,
> dead, or killed. Quote the file. Do not guess progress from the pane. If
> `updated` has not changed over several check-ins, nudge the lead as
> below.

### 5.6 T4, scenario 1 status body

```text
## Checklist

- [x] Fix the failing test (implementer-1)
- [x] Review the fix (reviewer-1)
- [x] Merge foil/implementer-1

The tests pass.
```

## 6. Overlap with 0.2.1 (#8)

These files are edited by both releases. Keep each 0.2.2 hunk inside the
named section so the R1 rebase stays mechanical.

| File | 0.2.2 edits | 0.2.1 edits | At rebase |
|---|---|---|---|
| `docs/requirements.md` section 8 | append one sentence to the `operator` and `lead` rows | rewrites both rows | keep 0.2.1's row text and re-append the sentence |
| `docs/requirements.md` 7.4 | checklist paragraph | none | no conflict expected |
| `src/foil/defaults/skills/lead.md` | "Board and contracts" | adds a personas paragraph just above that heading | keep both |
| `src/foil/defaults/skills/operator.md` | end of "Check in" | new sections before "Start"; "Check in" text unchanged | no conflict expected |
| `tests/test_skills.py` | new tests at the end | a new test in the middle | keep both |
| `docs/README.md` | a plan row | a plan row and an AGENTS line | keep both rows, in version order |
| `CHANGELOG.md` | 0.2.2 entry | turns "Unreleased" into 0.2.1 | put 0.2.2 above 0.2.1 |
| version files | 0.2.2 | 0.2.1 | take 0.2.2 |

`README.md`, `README.zh-CN.md`, both demos, and the scenario 1 fixture are
not touched by 0.2.1 in the paragraphs this plan edits.

## 7. Dependencies

- Q1 to Q3 first (requirements before behavior).
- S1 and S2 after Q; T1 to T3 with them.
- T4 before D3 and D4, so the demo copies the fixture.
- D1 and D2 after S1 and S2.
- R1 once 0.2.1 is on `main`; R2 and R3 after R1; R4 last.

## 8. Definition of done

- [ ] Q1 to Q3, S1 and S2, D1 to D5, T1 to T5, and R1 to R4 are done; S3
      and D6 are confirmed unchanged.
- [ ] The CONTRIBUTING checks pass: unit tests, fake-harness scenarios,
      lint, and build.
- [ ] Section 6 of the requirements is unchanged, and
      `tests/test_command_surface.py` passes without edits.
- [ ] Against `main`, the only change to package `.py` files is the
      version string in `src/foil/__init__.py`.
- [ ] The branch is rebased on a `main` that contains 0.2.1. If it is not
      there at acceptance, the work stops and the human is asked.
- [ ] An independent reviewer accepts the result.
- [ ] Nothing is pushed, merged, or tagged until the human asks.
