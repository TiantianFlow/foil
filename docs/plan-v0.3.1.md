# Plan for Foil 0.3.1: Role roster management

Issue #10. The roster is the project's template files. This release adds
`foil roster` so the operator and the lead can view and change it without
hand-editing TOML, and teaches both skills the new commands.

## Done when

- `foil roster list [--json]` shows every template and every persona that has no template.
- `foil roster show ROLE [--json]` shows one template.
- `foil roster add ROLE [--from FILE]` creates a template. Without `--from`, it needs `personas/<ROLE>.md` on disk and writes a template for it, using the first installed harness. It fails if ROLE exists.
- `foil roster update ROLE FIELD=VALUE` changes one of `harness`, `model`, `persona`, `worktree`, `permission`. An invalid result is rolled back.
- `foil roster remove ROLE` deletes a template. It refuses `lead`, `implementer`, and `reviewer`.
- Workers cannot run any roster command. The outside caller and the lead can. Only the outside caller may set `permission`, including `add --from` when the file's permission is not `ask`. Remove and a harness change fail while a seat of that template has a stored state other than `killed`.
- `foil init` names the personas that have no template.
- The operator skill teaches the roster commands, a first-run roster review, and importing a role file. The lead skill teaches the roster commands.
- requirements.md (F1, F21, sections 6.1 and 6.2) and the tests match the command surface.
- CHANGELOG.md lists the change under Unreleased.

## Decisions

- The three default templates stay. Extra roles are added with `roster add`.
- `init` stays non-interactive. First-run review is operator-skill guidance.
- Importing from another role library is `roster add ROLE --from FILE` with a file in Foil's template format. No conversion is built.

## Out of scope

- Interactive prompts in `roster add`.
- Format conversion for other role libraries.
- Validating that a template's persona file exists at `add --from` time.

## Version note

This file is the 0.3.1 plan. Another fleet builds mail and board commands (#12) as 0.3.0. If that order changes, retitle this plan to the version it actually ships.
