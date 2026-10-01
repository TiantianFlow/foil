# Plan: Foil 0.3.0

**Goal:** one release that lets a seat read mail and board files through
`foil` (issue #12) and lets the operator and the lead view and change the
roster through `foil` (issue #10).

#12 and #10 were written on separate lines. #12's plan was already named
0.3.0. #10's plan was named 0.3.1 because it did not know which number it
would ship under. They ship together here. There is no 0.3.1.

## Mail and board reads

A seat in a worktree (`.foil/worktrees/<name>`) reads mail and board files
that live in `.foil/board/`, outside its working directory. Harnesses ask
for approval on those reads, which stalls a seat that is meant to run
unattended. A `foil` command can be allowed once by the harness.

Four read commands, each with `--json`:

| Command | Behavior |
|---|---|
| `foil mail read PATH` | Reads one mail file under `.foil/board/mail/`. PATH must be absolute. Human output is the body; JSON is the `mail/v1` contract plus `from`, `to`, `time`, `re`, and `body`. |
| `foil mail list` | Lists the calling seat's mail, newest first. Needs `FOIL_SEAT_ID`. |
| `foil board read PATH` | Reads one file under `.foil/board/`, by absolute path or relative to the board folder. JSON carries the front matter fields and `body`, or only `body` for a file with no front matter. List values stay JSON arrays. |
| `foil board list PATTERN` | Lists board files matching a glob relative to the board folder, sorted. JSON is `{"files": [...]}`. |

Paths are checked before any read: a `..` part is refused, and so is any
path that resolves outside the allowed folder. Errors are one line on
stderr (N8). Writing stays with ordinary file tools and `foil send`.

## Roster

The roster is the project's template files. `foil roster` lets the operator
and the lead view and change it without hand-editing TOML.

- `foil roster list [--json]` shows every template and every persona that has no template.
- `foil roster show ROLE [--json]` shows one template.
- `foil roster add ROLE [--from FILE]` creates a template. Without `--from`, it needs `personas/<ROLE>.md` on disk and writes a template for it, using the first installed harness. It fails if ROLE exists.
- `foil roster update ROLE FIELD=VALUE` changes one of `harness`, `model`, `persona`, `worktree`, `permission`. An invalid result is rolled back.
- `foil roster remove ROLE` deletes a template. It refuses `lead`, `implementer`, and `reviewer`.
- Workers cannot run any roster command. The outside caller and the lead can. Only the outside caller may set `permission`, including `add --from` when the file's permission is not `ask`. Remove and a harness change fail while a seat of that template has a stored state other than `killed`.
- `foil init` names the personas that have no template.

The three default templates stay. Extra roles are added with `roster add`.
`init` stays non-interactive. Importing from another role library is
`roster add ROLE --from FILE` with a file in Foil's template format. No
conversion is built.

## Changes

- `docs/requirements.md`: section 6.1 rows, section 6.2 columns, F21, F29 to
  F32, and the counts (seven top-level commands, twenty actions).
- `src/foil/mail_ops.py`, `src/foil/board_ops.py`, and `src/foil/roster_ops.py`,
  wired in `cli.py`.
- The lead and worker skills name the mail and board commands. The lead and
  operator skills name the roster commands.
- `CHANGELOG.md`: one 0.3.0 section for both features.

## Done when

1. The four read commands work from a worktree and from outside the fleet,
   with `mail list` failing without a seat identity.
2. The five roster commands match F21, including who may set `permission`
   and when remove or a harness change is refused.
3. Requirements, skills, and tests agree with the command tree.
4. The suite passes.

## Out of scope

- Changing `foil send`, or any command that writes the board.
- Deleting or managing mail.
- Interactive prompts in `roster add`.
- Format conversion for other role libraries.
- Validating that a template's persona file exists at `add --from` time.
