# ADR-0010: Foil dual-name and technical identity

Status: Accepted
Date: 2026-08-23
Supersedes: conflicting naming portions of ADR-0007
Amends: ADR-0001 technical prefix, ADR-0009 fixed wake ownership, frozen requirements v0.4
Requirements: product intent; CAP-001–CAP-002, CAP-010–CAP-011, CAP-014, CAP-019, CAP-023, CAP-025, CAP-028, CAP-030–CAP-031, CAP-036

## Context

The product needs one cohesive public and technical identity. The Chinese primary name 运筹 remains; the English/international name and tracked identifiers must match rather than splitting brand from runtime.

This is a post-freeze change. The frozen-requirements rule therefore requires an ADR that identifies the changed requirements, reason, and acceptance impact.

## Decision

The product has two coequal public names:

- Chinese primary name: **运筹**
- English/international name: **Foil**

Public headings use locale-appropriate order:

- English/international: **Foil · 运筹**
- Chinese: **运筹 · Foil**

The exact product tagline is `your agents' loyal opposition`.

The tracked technical identity is `foil`:

- Python import and bundled resource namespace: `foil`
- console binary: `foil`
- distribution identity: `foil-orchestrator`
- generated project configuration: `.foil`
- environment and state names: `FOIL_*`, `foil`, and platform display name `Foil`
- tmux session prefix and stable user-option markers: `foil-` and `@foil-*`
- fixed bounded mailbox wake ownership text: `Foil`
- schemas, tests, skills, CLI help, quickstarts, and current design documents

The generic adapter boundary, no-MCP constraint, declarative adapter records, and exact `grok_cli` / `grok-4.6` and `opencode` / `xai/grok-4.6` pairings do not change.

ADR-0007 recorded a dual-name public-identity policy. This ADR supersedes that policy's English/international name, locale headings, and technical identity. ADR-0001 and ADR-0009 state the current `foil-` prefix, markers, and Foil wake identity they govern.

## Frozen-requirements amendment

`docs/spec/requirements.md` advances from frozen v0.4 to frozen v0.5.

Changed requirements:

- The product-intent names, locale headings, tagline, and tracked technical identity use 运筹 and Foil.
- CAP-001, CAP-002, CAP-010, CAP-011, CAP-014, CAP-023, CAP-025, CAP-028, CAP-030, and CAP-031 use the current Foil product/command identity.
- CAP-019 uses the tmux session prefix `foil-`; its deterministic grammar, bounds, stable-ID suffix, window grammar, and registry mapping are unchanged.
- CAP-036's bounded fixed wake uses Foil ownership text; queue gating, literal-argv execution, byte bound, no-shell behavior, and delivery semantics are unchanged.

Reason: one public and technical identity avoids split install, import, config, state, and runtime terminology while the opposition metaphor states the product's adversarial-complementarity role directly.

## Acceptance impact

- Both locale READMEs show the accepted headings and exact tagline. The executable T1–T6 contract lives in `docs/walking-skeleton.md`.
- Build and installation expose `foil-orchestrator`, `foil`, and the `foil` import/resource namespace only.
- Initialization emits `.foil`, reads `FOIL_STATE_DIR`, resolves Foil state directories, and generates the unchanged two-seat declarative runtime with the exact accepted model pairings.
- Runtime acceptance observes `foil-` tmux sessions, `@foil-*` markers, the fixed Foil wake, durable messaging, and native resume continuity.
- Tests reject former technical identifiers from public documentation and active runtime surfaces.
- No MCP server, provider-specific core branch, credential handling, or terminal scraping is introduced.

## Consequences

Users install and invoke Foil consistently across package, CLI, config, state, and tmux surfaces.
