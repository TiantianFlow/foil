# Issue #66: global settings are the settings

## The earlier finding was wrong

Issue #18 asked for global role templates and skills. The survey in
[issue-18-project-vs-global.md](issue-18-project-vs-global.md) answered:
do not add a machine-wide settings directory. Keep roles, personas, and
harness choices in the project. Read a packaged file from the install
only when a project copy would be a cache.

That finding is wrong. It treated machine-wide settings as a new product
and as a compatibility problem. Foil's goals are the same. The settings
shape is what is wrong.

A later plan for this issue repeated the mistake. It made global files
optional, created none of them by default, and left existing project
copies in place so they would keep hiding the global layer. That is a
backward-compatible workaround for the wrong shape. This change does not
keep it. Matching old project files is not a requirement.

**Global settings are the settings. A project override is optional. A
fleet override is optional.**

The behavior is in `docs/requirements.md`. This file is why each closed
decision was made. It does not add requirements.

## Closed decisions

Lookup is packaged, then global, then project, then fleet. A higher layer
replaces one whole harness, one whole role, or one role's whole pair
list. A missing entry falls through. A symlink or a `..` path is refused
and does not fall through.

Reason: project copies as the settings meant a global file could never
win. Replacing one whole object avoids a field-by-field merge. The lookup
stays in the existing preset loader and roster writes stay in the roster
module, so this adds no new module.

`foil init` still exists. It writes only the missing global default roles
`lead`, `implementer`, and `reviewer`, with the model omitted and F28's
harness choice. It writes no project settings, no persona, and no skill.

Reason: init still has to create the project folder and `fleets/main`.
Writing a default role only when that global row is missing keeps a
re-run from resetting a roster the operator already changed. Project
copies of settings, personas, and skills are what hid the global layer.

On every init that completes, including a same-checkout re-run, init
deletes the project `roles.toml`, `mapping.toml`, `harnesses.toml`,
`harnesses/`, `templates/`, `skills/operator.md`, `skills/lead.md`, and
`skills/worker.md` without reading them. A symlink is unlinked and not
followed. Adopt or a legacy move finishes first when it happens. A
non-zero exit deletes nothing. `project.json`, `fleets/`, `memory/`, and
`worktrees/` stay.

Reason: a same-checkout re-run that left those files would keep them
winning over the global layer. Not reading them is the migration. A
symlink is unlinked so the delete cannot follow a link outside the
project. Adopt and the legacy move finish first so those files are in the
fleet before the old paths go. A non-zero exit deletes nothing, so a
blocked in-repo `.foil/` and a failed worktree repair stay. The four
paths that stay are not settings copies.

That deletion is one requirements sentence, shipped at three existing
sites. `init_project` unlinks after `prepare_project` returns, only once
the steps that can still exit non-zero have succeeded, and the report is
after the unlink. The loader's init writer writes missing global defaults
only and does not open or rewrite those project paths. The skeleton does
not create `templates/personas` or `harnesses` on that run. The skills
directory stays. The writer does not put the operator skill back under
the project.

Reason: one sentence keeps the writer from putting the files back and the
skeleton from creating them. The order means a failure before the unlink
deletes nothing, and the report describes a deletion that already
happened.

When the global default roles are missing, the harness check reads the
packaged catalog and the global layer. Inside the global layer, file
beats list. It does not open a project harness file. A global user preset
whose command is on PATH is enough. `fake` stays ineligible. A failure
writes nothing and deletes nothing. Then `prepare_project`, then the
global default write, then the unlink, then the report.

Reason: a packaged-only check would exit `foil: no harness installed`
whenever the only installed harness is a global user preset. File beats
list inside that layer, the same rule as lookup. The check does not open
a project harness file, because init is about to delete that file unread
and must not use it to pass the check.

There is no `--global` flag. Unflagged `roster add`, `update`, and
`remove` write the global layer, including from inside a checkout.
`--project` writes the project override and needs a checkout. Inside a
fleet, `--project` is refused. No other command, subcommand, or flag is
added.

Reason: the unflagged command writes the settings, and the settings are
global. A separate `--global` flag would make the default look optional.
`--project` is the override and needs a checkout so it cannot write a
project that is not the one in use. Inside a fleet it is refused so a
seat cannot write a project override by accident.

Outside a fleet, `persona`, `worktree`, and `permission` write the global
role, and `harness` and `model` write the global mapping. Inside a fleet,
`harness` and `model` write only that fleet's `overrides.toml`. `add`,
`remove`, `persona`, `worktree`, `permission`, and `--project` are
refused there.

Reason: a fleet overrides one role's pair list. It has no role row, so
role fields and role add or remove stay outside the fleet. The lead
edits fleet pairs. The operator edits roles.

`remove --project ROLE` deletes whichever of the project role row and the
project mapping rows exist, including a mapping-only override, and fails
in one line only when the project has neither. That rule is one edit in
the roster module. It does not use the protected-role rejection or the
in-use check. The three default roles are included. The global role
stays.

Reason: a `--project` harness or model write is a mapping list and no
role row. Failing when the role row is missing would leave that override
in place. Skipping the protected-role rejection and the in-use check is
what lets the three defaults, and a mapping-only override, be removed.
The global role stays, so the project override is what goes.

A persona file is `personas/<role>.md`. The package is read live. Init
does not copy personas. A global file exists only when someone writes a
change. A project file is an optional whole-file override. A fleet has
no persona file.

Reason: the path matches a harness file, one name per role. Reading the
package live means init does not keep a cache. A fleet overrides pairs,
not the role text, so it has no persona file.

Fingerprint migration is closed. Init does not read a project harness
file to keep, fingerprint, or promote an edited entry. That prohibition
stays in the preset loader. The shared lookup still reads a project
harness file that a later override created. Within one layer the file
beats the list. A lower file does not beat a higher list entry.

Reason: reading a project harness in order to keep it would reopen the
decision that init does not read those files. An override someone writes
later is still read by the shared lookup, and the file-beats-list rule
is the same inside every layer.

## Files

```text
<root>/
  roles.toml                 the roles
  mapping.toml               each role's pair list
  harnesses.toml             only harnesses someone changed
  harnesses/<id>.toml        one harness; beats the global list entry
  personas/<role>.md         only personas someone changed
  skills/operator.md         only when someone changed the skill
  projects/<key>/
    project.json
    roles.toml               optional whole-role overrides
    mapping.toml             optional whole-list overrides
    harnesses.toml           optional
    harnesses/<id>.toml      optional
    personas/<role>.md       optional
    memory/  worktrees/
    fleets/<id>/
      overrides.toml         optional pair lists
      board/  run/
```

## Proposed, not closed

The sentences below are a proposal. The room has not accepted them. They
do not reopen the closed decisions above. Issue #66's triage is not
followed where it keeps old project copies, makes global optional, adds a
`--global` flag, or migrates a project harness by fingerprint. That
migration stays closed: init still deletes a project harness file without
reading it.

### Packaged model ids

Proposed in F23 and in CONTRIBUTING under "Before you tag".

The packaged model ids are read live from the installed package. Init
already does not copy that list, so an upgrade of the package refreshes
the ids with no project copy and no fingerprint. Where the CLI's current
docs list an alias, the packaged list uses the alias instead of a pinned
id. The ids are not guessed. `fake` has an empty list and no vendor doc.

Reason: a project copy froze the ids at the version that first ran init.
Reading the installed package is how an upgrade refreshes them without
reading the project file init deletes.

Docs read for this proposal, which the checklist re-checks before tagging:

- `claude`: <https://code.claude.com/docs/en/model-config>. It lists the
  aliases `sonnet`, `opus`, and `haiku`, and says an alias tracks the
  latest model for that family. A full model name is how you pin.
- `codex`: <https://developers.openai.com/codex/models>. It is the
  recommended-model page for the CLI. It also records retirements, so the
  packaged id is whatever that page recommends at the check, not a copy
  kept here.
- `gemini`: <https://geminicli.com/docs/cli/model/>. The picker it
  documents names `gemini-3-pro-preview`, `gemini-3-flash-preview`,
  `gemini-2.5-pro`, and `gemini-2.5-flash`.
- `grok`: <https://docs.x.ai/developers/models> and
  <https://docs.x.ai/build/cli/reference>. The models page says a family
  name and `<name>-latest` are aliases, and it leads with `grok-4.7`.
  The CLI reference says `--model` takes a model id and `grok models`
  lists the ids. It has no short alias table.
- `opencode`: <https://opencode.ai/docs/cli/>. A model id is
  `provider/model`, and `opencode models` lists them. The docs do not
  give a vendor alias list to copy.

### Pair cards, the Rotation line, and the spawn refusal

Proposed in F3 and F38.

Each pair is one card, harness on top and model below, not two columns.
An empty model shows `harness default`, with the tip that no `--model`
is passed, so the CLI uses its own default. Under each role, a Rotation
line lists the seats in order and marks the next one. On the fleet page
that line starts from that fleet's stored seat count.

`seat spawn` refuses a model-only row when the pair list that row
replaces has more than one entry. The refusal is one line and names
`foil roster update ROLE harness=ID/MODEL,ID/MODEL`. It writes no seat
record. On a one-entry pair list the older row stays valid, because the
pairing is clear.

Reason: a model-only row on a longer list is paired with a different
harness on each spawn, so the seat does not get the pair the file looks
like. One entry has only one harness, so the older row still names one
pair. The card and the Rotation line are how the page shows that pair
and which seat is next.

### Viewer

Proposed in F37 and F38. F39 is unchanged.

`/settings` is the global layer and needs no project. A layer strip at
the top of `/settings` and of a project's settings page reads Packaged,
then Global, then Project, then Fleet. Each settings row has a source
badge of `packaged`, `global`, `project`, or `fleet`.

Audit item 12: the fleet page section is titled Fleet overrides and links
to the pair list it replaces, the project mapping when that project has
one, otherwise the global mapping on `/settings`. An empty section is one
line: `None. Spawn uses the pair list this fleet replaces.` The audit's
line "None. Every role uses the project mapping." assumed project files
were the settings. That assumption is closed the other way.

Audit item 18: Settings is a link at the right end of the header and is
not a breadcrumb. The trail stays Foil, the project, the fleet, the seat,
or the file.

A viewer-gap item other than these changes only when it is cheap and in
the same code. This proposal does not require the rest of the audit.

The Magic Patterns mock is a reference only. The page stays read-only,
server-rendered, plain links, and one nonce script. Where the mock
differs, the requirements win.

Reason: the closed lookup has to be the same page the CLI writes.
Settings beside the project made the fleet look like a child of Settings.
The empty-state line has to name the list spawn actually uses.

### Skill text

Proposed in F25 and section 8. This proposal does not edit `skills/` or
the packaged skill files. It says what they must say.

The packaged operator, lead, and worker skills, the files under `skills/`,
and the instructions given to a worker seat describe the same lookup and
the same commands as section 6 and section 8. `tests/test_skills.py` and
`tests/test_command_surface.py` fail when those files drift. The test is
not written in this commit.

Reason: a skill that still names a project copy, or a command the
requirements do not list, sends the operator and the lead at different
layers. The existing tests are where that check belongs.

## What follows

These proposed sentences are what the room attacks. Tests and code follow
a close, not this commit. The default test run is the one that counts.
The live tier stays opt-in. This document does not change behavior by
itself.
