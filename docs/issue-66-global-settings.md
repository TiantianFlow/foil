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

The CLI, the operator skill, the viewer, and the files use that order. A
higher layer replaces one whole harness, one whole role, or one role's
whole pair list. The same layer wins in all four places. A screen that
shows a project list, a command that writes a different list, and a skill
that names a third path are the bug this change closes.

## What stays the product

Same tool. An operator outside the fleet, seats in tmux, roles, harness
presets, a pair rotation, a board per fleet. Packaged harness presets and
personas still ship with Foil and are read live. Trust is still once per
harness per repository. The viewer stays read-only.

| Layer | What it is | When it exists |
|---|---|---|
| Packaged | Harness presets and personas in the install | Always. Never copied. |
| Global | The roles, the pair lists, and any harness or persona someone changed | The settings. `init` creates the default roles here. |
| Project | A role, a pair list, a harness, or a persona this repository replaces | Only when someone sets an override. |
| Fleet | A role's pair list for that fleet | Only when someone sets an override. |

Lookup is packaged, then global, then project, then fleet. A missing
entry falls through. A symlink or a `..` path is refused and does not
fall through.

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

No `templates/` directory. No per-project copy of the operator skill. A
persona path is `personas/<role>.md`. For a harness id, the project file
wins over the project list entry, then the global file, then the global
list entry, then the package. Within one layer the file beats the list.
A lower file does not beat a higher list entry.

`init` adds each missing default role on the global layer: `lead` and
`implementer` get the first installed harness, `reviewer` gets the second
when there is one, and the model is omitted. It does not overwrite a row
that is already there. With no harness installed it writes nothing. It
writes no project settings. If a project still has the old `roles.toml`,
`mapping.toml`, `harnesses.toml`, `harnesses/`, `templates/`, or
`skills/operator.md`, `init` deletes them and says so. It does not read
them. Board, memory, fleets, and worktrees stay.

## The CLI edits those layers

Outside a fleet, `roster add`, `update`, and `remove` write the global
files and do not need a checkout. `--project` writes the project override
and needs a checkout. `list` and `show` print the effective row and two
layers, the role's and the mapping's, with or without a checkout. Inside
a fleet they show a fleet mapping when this fleet has one.

Inside a fleet, `harness` and `model` write that fleet's pair list.
`add`, `remove`, `persona`, `worktree`, `permission`, and `--project` are
refused there. The lead edits fleet pairs. The operator edits roles.

A rotation is `harness=ID/MODEL,ID/MODEL`. `model=VALUE` is allowed on a
one-entry list and writes that one pair. Every stored row names a
harness. Spawn reads that list. A row with no harness makes `seat spawn`
refuse, in one line, and the line names
`foil roster update ROLE harness=ID/MODEL,ID/MODEL`.

`roster remove` of `lead`, `implementer`, or `reviewer` still fails.
`roster remove --project ROLE` deletes that role's project row and its
project mapping rows. The global role returns. This includes the three
default roles. A custom global role is refused while any fleet registry
in the store has a seat of that role that is not killed. A role that
exists only on a project is removed only with `--project`.

Harness launch lines and persona bodies stay files. The skill names
`<root>/harnesses.toml` and `<root>/personas/<role>.md` for every
project, and the same names under the project folder for one project.
The operator pointer prints `<root>/skills/operator.md` when that file
exists, otherwise the skill in the installed package. The lead's
instructions name the global files, the project files that exist, and
this fleet's `overrides.toml`.

The operator skill is rewritten to these commands and paths. It stops
telling the agent to edit three lists in the project folder or to copy
personas there. `--project` is added to the command surface in
`docs/requirements.md` in the same change as the code.

## Viewer

`/settings` is the global layer: packaged harnesses with global edits,
the global roles, and the global mapping. Inside a project, Settings
shows the effective rows, with the role's layer and the mapping's layer,
and a project row links to the global row it replaces. When the project
has no override files, the page says so. The fleet page shows that
fleet's pairs and marks the next seat from its stored count. With no
fleet override it says that spawn uses the project mapping, and it links
there. Settings is a text link at the right end of the header. The
breadcrumb stays Foil / project / fleet / seat.

Other viewer gaps can be rewritten to match. They are not a reason to
keep project files as the settings.

## How this is built

Requirements first, then tests, then the code. The tests use the offline
harness already in the tree: real Foil, real tmux, real git, and the
`fake` preset instead of an agent CLI (requirements section 10). The
default test run is the one that counts. The live tier stays opt-in. New
tests cover global defaults, a project override hiding one role, a fleet
pair list hiding that role's mapping, a model-only row refused at spawn,
`roster list` printing the two layers, and the operator skill naming the
global paths. The layer lookup belongs in the existing preset and
template loader. Roster writes stay in the roster module. No new module.

Packaged model ids are updated to each CLI's current aliases in the same
work, and the release checklist re-checks them. The ids are read from the
package, so an upgrade refreshes them without a copy in the project.

This document records the decision. It does not change behavior.
`docs/requirements.md` changes with the code.
