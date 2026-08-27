# ADR-0003: Seat resume precedence

Status: Accepted  
Date: 2026-08-23  
Requirements: CAP-017, CAP-018, CAP-029, CAP-030

## Context

Tmux may preserve a live process, agent CLIs may preserve native conversations after process loss, and either source may be stale. Relaunch behavior must be deterministic and must not silently attach to the wrong process or discard context.

## Decision

For each validated seat record:

1. An explicit operator fresh-context request wins. Start a new incarnation, preserve lineage, and record the bypassed evidence.
2. Probe the recorded tmux target and stable fleet/seat markers.
3. If the matching target is alive, revive it.
4. If tmux liveness or marker identity is indeterminate, return `blocked`; do not guess.
5. If tmux is absent/stale and a validated native session ID plus adapter resume capability exist, attempt native resume.
6. If native resume is unavailable, or a recorded attempt has failed definitively, start fresh and record the precise reason.

The pure resolver chooses among `revive_tmux`, `resume_native`, `start_fresh`, and `blocked`. Side-effecting orchestration records attempt/result events and invokes the resolver again with new evidence after a failed native attempt. There is no blind retry loop.

## Consequences

Live context is preserved first, durable native context second, and fresh work remains a logged fallback. Uncertain probes can require operator action instead of maximizing availability at the cost of attaching incorrectly.

## Rejected alternatives

- Always invoke native resume: can duplicate a still-live tmux process.
- Always start fresh when tmux is gone: discards recoverable context.
- Trust tmux name alone: can attach to a collision or stale manual session.
- Automatically kill mismatched sessions: unsafe and outside recovery authority.
