# ADR: Product naming

Status: Superseded by `docs/adr/0007-naming.md`; current names specified by ADR-0010
Date: 2026-08-23
Requirements: product naming constraint, CAP-019

## Decision

The product needs a short, typeable public identity that expresses coordinated allocation and adversarial complementarity. Bare package and command names need not be globally exclusive; qualified distribution coordinates are enough.

The current public names, completed by ADR-0007 and ADR-0010, are:

- Chinese primary name: **运筹**
- English/international name: **Foil**

**运筹** names deliberate coordination and allocation before execution. **Foil** names the product's loyal-opposition role: independent seats that challenge one another.

Public surfaces show both names in locale-appropriate order. Active technical identity (`foil`, `foil-`, `foil-orchestrator`) is specified by ADR-0010. Runtime tmux grammar remains CAP-019.

## Consequence

Later ADRs record the dual-name policy and the Foil technical identity. This document retains only the durable naming rationale.
