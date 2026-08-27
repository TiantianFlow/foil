# Domain Model

Status: Proposed  
Requirements baseline: `docs/spec/requirements.md` frozen v0.5

## Bounded contexts

### Fleet Planning

Owns desired fleet composition and dispatch policy.

- **Fleet** (aggregate root): a versioned desired configuration containing seats, challenge relationships, usage pools, and policy.
- **SeatSpec** (entity): stable seat ID, one specialization, role/context references, adapter reference, model reference, pool reference, and worktree intent.
- **UsagePool** (entity): independently metered capacity source. It is deliberately separate from model and adapter.
- **ChallengeEdge** (value object): directs one differently specialized seat to challenge another.
- **DispatchPolicy** (value object): ranks eligible seats using capability, availability signal, active load, and explicit operator preferences.

This context does not launch processes. Its output is a deterministic placement decision with recorded factors. Traces: CAP-003–CAP-007, CAP-024–CAP-025, CAP-029.

### Adapter Catalog

Owns declarative descriptions of external CLI behavior.

- **AdapterDefinition** (aggregate root): version, kind ID, executable discovery, launch/resume/shutdown command templates, native-session capture rule, structured probe/hook contract, and skill references.
- **CommandTemplate** (value object): argv tokens with typed placeholders; never an interpolated shell string.
- **SessionCaptureRule** (value object): a bounded structured source from which an opaque native session ID may be recorded.
- **ProbeDefinition** (value object): explicit process, command, file, or hook probe; terminal-text regex is not a valid probe kind.

Core services consume adapter capabilities and templates generically. They do not compare adapter IDs. Traces: CAP-008–CAP-011, CAP-033–CAP-034.

### Runtime Continuity

Owns the durable identity and current process attachment for each seat.

- **SeatRecord** (aggregate root): stable seat/fleet identity plus working directory, adapter kind, native session ID, tmux target, git/worktree identity, pool ID, current incarnation, and extension metadata.
- **SeatIncarnation** (entity): one live or historical process lineage for a seat.
- **TmuxTarget** (value object): generated session/window names plus observed tmux IDs.
- **NativeSessionRef** (value object): opaque, non-secret resume identifier and capture provenance.
- **ResumeDecision** (value object): action, reason code, and evidence summary.
- **ResumeResolver** (domain service): applies deliberate-fresh override and safe tmux/native/fresh precedence.
- **TmuxNameCodec** (domain service): derives shell-safe display labels from stable identities.

The registry preserves desired identity even when no process is live. A seat is not its tmux window or native session. Traces: CAP-015–CAP-019, CAP-024, CAP-030, CAP-033.

### Observability and Collaboration

Owns facts that the outside controller polls and the file protocols seats use to coordinate.

- **StatusSnapshot** (aggregate root): current structured state for one fleet or seat.
- **AuditEvent** (entity): immutable, redacted fact with stable causal IDs.
- **Mailbox** (aggregate root): append-safe messages and acknowledgements addressed by stable IDs.
- **MailboxWake** (value object): bounded advisory result for a generic tmux
  notification when durable messages are queued.
- **Notepad** (aggregate root): versioned shared content updated atomically.
- **MemoryLesson** (aggregate root): scoped proposed/accepted/superseded/rejected conclusion linked to evidence.

Status is a projection of lifecycle operations, liveness/probe results, and events. Terminal scrollback is outside the model. Traces: CAP-012–CAP-014, CAP-020–CAP-021, CAP-029–CAP-030, CAP-036.

### Browser Boundary

Owns optional, explicitly authorized browser actions.

- **BrowserGrant** (aggregate root): bounded origins, actions, lifetime, and operator approval reference.
- **BrowserActionRequest** (entity): target, action, arguments, and causal task ID.
- **BrowserActionResult** (value object): minimal redacted result and audit reference.
- **RedactionPolicy** (value object): forbidden secret classes and output limits.

The bridge is an optional external local process. Raw browser credentials and unrestricted page state never enter the other contexts. Traces: CAP-022–CAP-023, CAP-034.

### Onboarding and Packaging

Owns distributable skills, default roles, prerequisite discovery, and generated starter configuration.

- **SkillPackage** (aggregate root): portable instructions plus CLI/file fallback.
- **RoleProfile** (entity): one specialization, context contract, and challenge obligations.
- **SetupPlan** (value object): discovered non-secret prerequisites, selected pools/roles, and dry-run output.

Traces: CAP-001–CAP-002, CAP-010, CAP-025–CAP-028, CAP-031.

## Ubiquitous language

| Term | Meaning |
|---|---|
| Controller | The user-selected outside agent or shell that invokes Foil. |
| Manager | The controller responsibility that decomposes work, staffs seats, dispatches, polls, intervenes, and synthesizes. It is a skill, not an embedded model. |
| Fleet | A desired coordinated set of seats and policies. |
| Seat | A durable role slot with stable identity and exactly one primary specialization. |
| Incarnation | One process lineage of a seat; deliberate fresh context creates a new incarnation without changing seat identity. |
| Challenge edge | An explicit obligation for one specialization to question or verify another. |
| Usage pool | Independently constrained quota/capacity, separate from CLI, provider, account label, and model. |
| Availability signal | Manual, probe, cooldown/rate-limit, or unknown evidence about a usage pool. |
| Adapter | Declarative external-CLI behavior plus skill documentation. |
| Native session ID | Opaque agent-owned resume token that is not an authentication secret. |
| Tmux target | The expected session/window identity and observed live tmux IDs for a seat. |
| Registry | Durable per-seat continuity records. |
| Status snapshot | Current structured, pollable state projection. |
| Audit event | Append-only redacted fact explaining a transition or decision. |
| Relaunch | Reattach, native-resume, or fresh-start a seat according to precedence. |
| Fresh-context respawn | Operator-directed new incarnation that bypasses existing live/native context while retaining history. |
| Tool skill | Portable instructions for a narrow operation with a plain CLI/file fallback. |
| Headroom | Operator-observable evidence that a usage pool can accept work; never invented quota. |

## Domain events

- `FleetConfigured`
- `SeatPlanned`
- `ChallengeAssigned`
- `PoolSignalObserved`
- `DispatchDecisionRecorded`
- `SeatLaunchRequested`
- `SeatLaunched`
- `TmuxTargetRevived`
- `NativeResumeAttempted`
- `NativeResumeSucceeded`
- `NativeResumeFailed`
- `FreshStartSelected`
- `FreshContextRespawnRequested`
- `SeatIncarnationStarted`
- `SeatStatusChanged`
- `MessageAppended`
- `MessageWakeAttempted`
- `MessageAcknowledged`
- `NotepadUpdated`
- `MemoryLessonProposed`
- `MemoryLessonAccepted`
- `MemoryLessonSuperseded`
- `BrowserGrantIssued`
- `BrowserActionDenied`
- `BrowserActionCompleted`

Every event carries event ID, schema version, timestamp, causal fleet/seat/task IDs where applicable, event type, and a redacted payload.

## Invariants

1. A seat has one and only one primary specialization. (CAP-003)
2. Stable IDs define fleet and seat identity; display names and tmux labels do not. (CAP-013, CAP-019)
3. A challenge edge cannot target the same seat and must connect different specializations. (CAP-004)
4. A usage pool is not inferred from adapter or model. (CAP-005)
5. Unknown pool headroom remains unknown until evidence changes it. (CAP-007)
6. Core behavior is selected by adapter capabilities, not adapter-name conditionals. (CAP-008–CAP-009)
7. Terminal output never mutates structured status. (CAP-014)
8. Unknown extension metadata survives registry round trips. (CAP-015)
9. Relaunch first honors an explicit fresh-context request; otherwise it safely tests live tmux, native resume, then fresh start. An indeterminate liveness probe blocks rather than guesses. (CAP-017–CAP-018, CAP-030)
10. A live tmux name alone is insufficient evidence; the target must match stable fleet/seat markers. (CAP-017, CAP-019)
11. Browser, registry, status, event, message, and memory outputs contain no credential material. (CAP-015, CAP-021, CAP-023, CAP-029, CAP-034)
12. Malformed durable state cannot trigger process launch, resume, message delivery, browser action, or teardown. (CAP-030)
13. Worktree and branch observations must match registry intent before a mutating worker is dispatched. (CAP-024)
14. A new incarnation never erases prior incarnation evidence. (CAP-018, CAP-029)
15. A mailbox wake is attempted only for queued mail, injects only fixed bounded
    Foil text through literal tmux argv, and never establishes acknowledgement.
    (CAP-020, CAP-034, CAP-036)

## Cross-context contracts

1. Fleet Planning emits a placement decision; Runtime Continuity resolves how the selected seat should exist.
2. Adapter Catalog supplies typed argv templates and probes to Runtime Continuity; it never owns seat state.
3. Runtime Continuity emits events; Observability projects them into pollable snapshots.
4. Collaboration records causal task/seat IDs but does not infer process liveness.
5. Browser Boundary accepts only explicit grants and minimal action requests from the controller; no adapter can widen a grant.
6. Onboarding generates desired configuration only. Launch remains a separate, inspectable operation.
