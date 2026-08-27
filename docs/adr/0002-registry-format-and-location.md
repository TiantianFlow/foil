# ADR-0002: Registry format and location

Status: Accepted  
Date: 2026-08-23  
Requirements: CAP-012, CAP-013, CAP-015, CAP-016, CAP-020, CAP-030, CAP-033

## Context

The outside controller and recovery commands need durable state shared by all worktrees. A checked-in worktree-local registry can diverge, leak operational metadata, or be committed accidentally. A database adds a daemon/tooling boundary and makes simple polling harder.

## Decision

Use versioned JSON records and JSONL events under this precedence:

1. Explicit `FOIL_STATE_DIR`.
2. `<git-common-dir>/foil/` in a Git project.
3. `$XDG_STATE_HOME/foil/projects/<project-id>/`, with a documented platform fallback, outside Git.

Each fleet has separate records for fleet metadata, each seat, each status projection, mailboxes, notepads, memory lessons, events, and locks. Per-seat files are the registry; no monolithic mutable registry file is required. Every record contains `schema_version`, stable IDs, and an RFC 3339 UTC update timestamp. Unknown future values are allowed only inside an `extensions` object and survive round trips.

Writers use narrow advisory locks, a same-directory temporary file, flush/fsync, atomic replacement, and directory fsync. Directories default to mode `0700`; state files default to `0600`. Readers reject symlinks, non-regular/oversized records, malformed JSON, unsupported versions, and invariant failures.

## Consequences

State is inspectable with ordinary tools, shared across worktrees, and recoverable without a service. JSONL event append requires locking and rotation policy later. Network filesystems with weak rename/locking semantics are not supported until explicitly tested.

## Rejected alternatives

- Worktree-local `.foil/state`: splits one fleet across worktrees.
- Checked-in registry: leaks runtime details and creates merge conflicts.
- SQLite: robust but obscures the minimum pollable contract and is unnecessary for the initial local scale.
- One fleet-wide JSON file: creates avoidable concurrent-write contention.
