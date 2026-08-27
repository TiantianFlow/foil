# ADR-0005: Portable skill packaging

Status: Accepted  
Date: 2026-08-23  
Requirements: CAP-001, CAP-002, CAP-010, CAP-025, CAP-027

## Context

Agent CLIs differ in skill directories, command discovery, metadata, and instruction precedence. Foil needs reusable manager/tool guidance without making one CLI's packaging convention the canonical runtime.

## Decision

The repository canonical format is one directory per skill:

```text
skills/<skill-name>/
  SKILL.md
  references/
  scripts/
```

`SKILL.md` contains conservative YAML metadata (`name`, `description`) followed by provider-neutral Markdown instructions. Every operation is expressed first as a `foil` CLI command or documented file contract. Provider-specific installation paths and invocation syntax live in generated/install mapping tables and adapter skill documents, not in core domain code.

Ship top-level `controller` and `manager` skills plus `poll-status`, `shared-notepads`, `memory-update`, and `browser-bridge`. Default role profiles are separate from tool skills so one specialization can select only the context it needs. Plain `AGENTS.md`-style instructions and CLI help are the lowest-common-denominator fallback.

## Consequences

The same source guidance can be copied or linked into multiple CLI conventions. Packaging tests must detect unsupported metadata and broken references. Some CLIs will not offer first-class skill invocation; their users still receive the documented instruction-file and CLI fallback.

## Rejected alternatives

- One generated prompt containing every role/tool: defeats context differentiation.
- Provider-native packaging as source of truth: privileges one CLI and spreads drift.
- Slash commands only: not portable and often interactive-only.
