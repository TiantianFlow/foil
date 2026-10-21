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

## Not decided

These stay unwritten. This edit does not decide them: packaged model ids
read live, alias refresh, the CONTRIBUTING release checklist, pair cards,
the Rotation line, the spawn refusal for a model-only row, `/settings`,
viewer audit items 12 and 18, and the skill text in `skills/` and the
packaged skill files.

Section 8 of the requirements changes only a path or a command this close
replaced. It is not a rewrite of the skill text.

The viewer stays as it is until a later task. F38 and F39 are unchanged
for that reason.

## What follows

Requirements are recorded in this commit. Tests and code follow. The
default test run is the one that counts. The live tier stays opt-in.
This document does not change behavior by itself.
