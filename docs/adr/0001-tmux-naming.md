# ADR-0001: Tmux naming convention

Status: Accepted
Date: 2026-08-23
Requirements: CAP-015, CAP-017, CAP-019, CAP-029

## Context

Tmux names are operator-facing locators, can collide across fleets, and may contain shell-hostile characters if derived directly from display names. They cannot be durable identity because names may be truncated or changed.

## Decision

- Session grammar: `foil-<fleet-slug>-<fleet-id8>`.
- Window grammar: `<seat-slug>-<seat-id8>`.
- Slugs are lowercase ASCII, collapse non-alphanumeric runs to one hyphen, trim hyphens, and fall back to `fleet` or `seat`.
- `<fleet-id8>` and `<seat-id8>` are the first eight lowercase hexadecimal characters of a digest of the complete stable ID, not a truncated user label.
- Session names are bounded to 80 characters and window names to 60 characters. The slug is truncated before the separator and ID suffix.
- Registry records store the generated names and tmux-native session/window IDs.
- A live name is accepted for revival only when stable fleet/seat markers also match the registry.

## Session prefix scope

Only tmux **session** names require the `foil-` prefix. Window names follow this ADR's `<seat-slug>-<seat-id8>` grammar and remain nested under their generated session. Acceptance checks the `foil-` prefix on session names only, plus the documented window grammar and session nesting.

## Consequences

Names are typeable and recognizable while stable IDs prevent ordinary display-name collisions. Renaming a display label can change a future tmux label but not seat identity. Extremely rare digest-prefix collisions are detected against the registry and fail closed rather than silently attaching.

## Rejected alternatives

- Raw display names: unsafe and collision-prone.
- UUID-only names: safe but hostile to operators.
- Tmux names as primary keys: continuity would depend on mutable display syntax.
