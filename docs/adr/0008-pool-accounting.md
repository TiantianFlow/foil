# ADR-0008: Evidence-based usage-pool accounting

Status: Accepted  
Date: 2026-08-23  
Requirements: CAP-004–CAP-007, CAP-029

## Context

Provider plans expose inconsistent or no quota APIs. Model, CLI, provider, subscription, and account are not interchangeable capacity boundaries. Pretending exact quota exists would make scheduling brittle and misleading.

## Decision

Model usage pools explicitly as operator-defined, independently constrained capacity sources. A pool has a stable ID, label, optional concurrency limit, availability policy, signal observations, and an extensible metadata map. It does not contain credentials and is not inferred from adapter/model.

Supported evidence:

- manual state and optional expiry;
- bounded command probe with structured output;
- active assignment count versus configured concurrency;
- structured rate-limit/cooldown events;
- `unknown`.

Scheduling first filters by task capability and safety constraints. It then ranks eligible seats by operator preference, availability class, cooldown, active-load ratio, and stable tie-breaker. Policy defines how `unknown` compares; the system never converts it into invented remaining tokens or requests.

Every dispatch event records policy version, candidate pools/seats, redacted evidence and timestamps, rejection reasons, and the selected result.

## Consequences

Foil can balance across free, bundled, paid, and other pools without requiring billing integration. Decisions are explainable but only as fresh as their evidence. Operators may need to update manual signals or provide probes.

## Rejected alternatives

- Provider/model as pool identity: collapses separate plans/accounts.
- Exact universal quota counter: unavailable and often unverifiable.
- Round-robin alone: ignores depletion, cooldown, and concurrency.
- Hidden adaptive heuristic: undermines operator control and auditability.
