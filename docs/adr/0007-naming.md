# ADR-0007: Dual-name product identity

Status: Superseded by ADR-0010
Date: 2026-08-23
Supersedes: `docs/adr/name-choice.md`
Requirements: frozen product-intent naming clause; CAP-019 remains unchanged

## Context

The product needed a Chinese primary name and a coequal English/international name on public surfaces. Frozen requirements originally assumed one public international name; this ADR amended only the public naming clause.

## Decision

A dual-name public identity policy was accepted: Chinese primary **运筹** together with a coequal English/international name, shown together on public-facing surfaces in locale-appropriate order. One product kept localized and international names without splitting identity.

## Supersession

ADR-0010 supersedes this dual-name policy's English/international name, locale headings, and technical identity. Current public names and tracked identifiers are specified there.

## Frozen-requirements amendment

`docs/spec/requirements.md` advanced from frozen v0.1 to frozen v0.2 solely to replace the single-name product-intent sentence with this dual-name rule.

Acceptance impact:

- Public-facing documentation must show both names in locale-appropriate order.
- CAP-019 and every runtime naming acceptance criterion remain unchanged.
- No functional, quality, architecture, filesystem, package, CLI, tmux, registry, or session requirement is weakened.
