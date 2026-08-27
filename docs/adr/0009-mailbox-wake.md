# ADR-0009: Bounded wake for queued mailbox messages

Status: Accepted
Date: 2026-08-23
Requirements: CAP-001, CAP-012–CAP-014, CAP-020, CAP-030, CAP-034, CAP-036

## Context

CAP-020 requires durable mailbox messages, acknowledgements, and pollable delivery
state. A live CLI agent waiting at an input prompt does not necessarily poll its
mailbox. In a tmux-backed fleet, a file-only protocol can therefore leave valid
queued work starved indefinitely even though the target seat is alive.

This is a post-freeze requirement decision. It adds CAP-036; it does not change or
silently broaden CAP-020.

## Decision

Accept the candidate requirement: a seat that is idle with queued messages must be
wakeable by the controller.

- The immutable message is committed before any wake attempt.
- A wake is attempted only while at least one message is queued and only against a
  validated native tmux session/window target from the seat registry.
- Liveness is checked through tmux's structured command result. Terminal content is
  never read.
- The wake payload is a short Foil-owned constant that tells the seat to poll its
  mailbox. Message bodies and other caller-controlled values are never injected.
- Tmux is invoked with literal argv, without a shell. The wake has fixed input and
  timeout bounds and no retry loop.
- Core behavior is generic. No adapter, model, provider, or CLI name selects a wake
  path.
- Message durability does not depend on wake success. The controller receives a
  structured wake outcome and can poll queued versus acknowledged delivery state.

## Acceptance impact

Requirements advance from frozen v0.3 to frozen v0.4 and add CAP-036. Acceptance
now requires unit and CLI contract coverage for queue gating, fixed bounded literal
input, argv-only tmux execution, and structured wake outcomes. It also requires a
real-tmux integration test proving that an idle live seat receives a wake; that test
may skip only when tmux is unavailable.

## Consequences

Mailbox delivery remains durable and agent-agnostic while live tmux seats can be
nudged to observe queued work. A wake is advisory rather than an acknowledgement:
only an immutable acknowledgement record changes delivery to acknowledged. Offline
or failed wakes leave the message queued for later polling or intervention.

## Rejected alternatives

- File-only delivery: permits indefinite queue starvation at an idle input prompt.
- Injecting the message body: expands the command-injection and prompt-injection
  boundary and makes input unbounded.
- Provider-specific wake commands: violates the generic adapter boundary.
- Polling terminal text to infer idleness or receipt: violates CAP-014.
- A daemon or MCP notification transport: violates the short-lived CLI and no-MCP
  architecture.
