# Initialization Contract

Status: Implemented for `foil init`
Requirements: CAP-003–CAP-004, CAP-016, CAP-025

## Project initialization

`foil init [DIRECTORY]` accepts an empty directory or an existing Git
repository. It writes the eight provider-neutral role profiles to
`.foil/roles/` and initializes an empty live fleet registry under the
resolved versioned state root. No seats are created.

Existing repositories are onboarded without touching user data: every
tracked and untracked file and all Git state (no `git add`, no identity
commit, no checkout, no rewritten files) is preserved. A non-empty
directory outside Git is refused with an actionable error instead of a
silent `git init` over user files.

Initialization fails closed before any write when it finds a real
collision: fleet state that already exists for the project, a `.foil`
path that is not a directory, a role file that is not a regular file, or
a role file whose content conflicts with the packaged template. Role
files with identical packaged content are left untouched, so a retry
after a partial failure converges without mutating user files. Rollback
after a failed attempt deletes only artifacts that attempt created —
never user `.foil` extras or the project tree.

The role library contains manager, requirements owner, domain designer,
implementer, test/verifier, reviewer/challenger, researcher, and memory
curator. Each role file has one singular `primary_specialization`. Roles
are templates. The lead chooses which ones to spawn, and when.

The first live seat must be created with `foil seat spawn --lead`.
Workers may be spawned only after a lead exists. Workers isolate by
default into `worktrees/<seat_id>`. Shared directories require
`--shared-cwd`. Spawn is `--permission supervised` unless the operator
or lead selects `auto`. Isolated clones exclude `FOIL.md` and the parent
excludes `worktrees/` through Git's private exclude file. Membership is
persisted only so `foil resume` can continue after interruption, not as
a recipe to replay later.

## State-root precedence

Resolution is deterministic for the canonical project path and follows ADR-0002:

1. An absolute, nonempty `FOIL_STATE_DIR`.
2. `<git-common-dir>/foil/` when Git reports a common directory.
3. `$XDG_STATE_HOME/foil/projects/<project-id>/` when `XDG_STATE_HOME` is an
   absolute, nonempty path.
4. Outside Git, the platform fallback:
   - macOS:
     `~/Library/Application Support/Foil/state/projects/<project-id>/`
   - Linux and other Unix-like platforms:
     `~/.local/state/foil/projects/<project-id>/`

`<project-id>` is a stable hash-derived identifier of the canonical absolute
project path. The initialized shape is
`<state-root>/v1/fleets/<fleet-id>/{fleet.json,seats/,locks/}`. State directories
use mode `0700`, and the initial fleet record uses mode `0600`.
