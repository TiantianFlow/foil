# Foil · 运筹 Requirements

Status: **FROZEN v0.5**
Freeze date: 2026-08-23  
Change rule: any post-freeze requirement change requires a new ADR that identifies the changed requirement, reason, and acceptance-impact.

## Amendment record

| Version | Date | Change | Authority | Acceptance impact |
|---|---|---|---|---|
| v0.1 | 2026-08-23 | Initial frozen requirements. | Initial freeze commit | CAP-001–CAP-035 established. |
| v0.2 | 2026-08-23 | Adopted a dual public-name policy: Chinese primary 运筹 plus a coequal English/international name. | ADR-0007 | Public docs show both names; CAP-019 and all runtime acceptance criteria are unchanged. |
| v0.3 | 2026-08-23 | Clarified CAP-019 so the documented session prefix applies only to tmux session names; window names follow ADR-0001 and remain nested under their session. | ADR-0001 | Acceptance checks the session prefix, documented window grammar, and nesting; no runtime naming change. |
| v0.4 | 2026-08-23 | Added a bounded, generic controller wake for live tmux-backed seats with queued messages. | ADR-0009 | CAP-036 adds unit/contract/real-tmux acceptance showing queued mail can wake an idle live seat without terminal scraping, provider branches, or unbounded input injection. |
| v0.5 | 2026-08-23 | Locked the English/international public name to Foil, the tagline `your agents' loyal opposition`, and the tracked technical identity `foil`. | ADR-0010 | Public headings, package/import/binary/config/environment/state/resource/tmux/wake identities, schemas, tests, skills, help, design docs, and T1–T6 acceptance use Foil. |

## Product intent

Foil · 运筹 is a headless, local-first coordinator for fleets of CLI agents. A user-selected agent remains the outside controller and manager. The product supplies durable, pollable coordination mechanics without becoming another chat UI or depending on MCP. Its tagline is **your agents' loyal opposition**.

The Chinese primary name is **运筹** and the English/international name is **Foil**. Public-facing surfaces show **Foil · 运筹** in English/international contexts and **运筹 · Foil** in Chinese contexts. Active tracked identifiers, the CLI binary, package/import namespace, generated configuration, environment/state names, resource namespace, and runtime tmux resources use `foil` / `foil-` as specified by ADR-0010.

## Goals

- **G1 — Adversarial complementarity:** each seat has one specialization and deliberately different context/model assumptions; roles challenge one another.
- **G2 — Usage-pool load balancing:** work can move among independently metered provider, plan, or account pools according to headroom.
- **G3 — Controller outside:** the user's chosen primary agent is the manager and control interface.
- **G4 — Agent-agnostic by mechanism:** CLI differences are declarative configuration and skill documentation, not kind-specific core branches.
- **G5 — Skills/CLI over MCP:** portable skills and ordinary CLI/file mechanisms replace MCP servers.
- **G6 — Headless observability:** machine-readable disk state is the source for polling; no UI or screen scraping.
- **G7 — Session continuity:** live tmux state and durable native resume metadata support deterministic fleet relaunch.
- **G8 — Default roles and guided setup:** useful defaults and onboarding make source reading unnecessary.

## Functional requirements

| ID | Requirement | Acceptance criteria | Trace |
|---|---|---|---|
| CAP-001 | Foil SHALL expose management operations through a documented CLI and a portable manager skill usable by an outside controller agent. | In a clean test project, a controller can configure, launch, poll, message, and stop a fleet using only CLI help and the manager skill; no Foil UI is opened. | G3, G5 |
| CAP-002 | The outside controller SHALL remain an ordinary user-selected agent process rather than a privileged embedded model. | A fleet can be managed from at least two supported controller CLIs without changing Foil core code; the controller process is not present in the worker registry unless explicitly added as a seat. | G3, G4 |
| CAP-003 | Every seat definition SHALL declare exactly one primary specialization and its own context source. | Configuration validation rejects a missing or multi-valued primary specialization; two sample seats can be launched with different role/context documents. | G1 |
| CAP-004 | Fleet configuration SHALL support deliberate model and usage-pool diversity plus explicit challenge relationships between roles. | A sample fleet validates only when each challenge edge points to an existing, differently specialized seat; its generated plan shows at least two configured usage pools and two model families. | G1, G2 |
| CAP-005 | A usage pool SHALL be a first-class configuration object distinct from agent kind and model, with extensible identifiers and operator-supplied availability signals. | Two seats using the same CLI/model but different pool IDs are accounted separately; an unknown future pool metadata key survives a read/write round trip. | G2, G4 |
| CAP-006 | Dispatch SHALL rank eligible seats using declared capability, pool availability, active load, and operator policy, and SHALL record the factors behind each choice. | Deterministic fixtures demonstrate work moving away from a depleted or concurrency-limited pool; the decision record identifies all ranking inputs and selected pool without exposing credentials. | G2, G6 |
| CAP-007 | Pool availability SHALL support manual state, command-probe state, cooldown/rate-limit observations, and an `unknown` state without requiring provider billing APIs. | Contract tests cover all four signal types; `unknown` never becomes fabricated numeric quota and is handled by configured policy. | G2, G4 |
| CAP-008 | Per-CLI behavior SHALL be represented by versioned declarative adapter records and referenced skill documents. | Adding a fixture adapter with unique launch, resume, session-ID capture, status, and shutdown commands requires no source-code conditional keyed by its agent kind; schema validation catches missing required fields. | G4, G5 |
| CAP-009 | Core runtime code SHALL NOT branch on known CLI/provider/model names. | A repository test scans the core adapter-dispatch boundary and fails on prohibited kind-name comparisons; behavior tests run against generic adapter fixtures. | G4 |
| CAP-010 | Foil SHALL package two top-level portable skills—controller and manager—and tool skills for poll-status, shared notepads, memory update, and browser bridge. | Distribution tests find each skill, validate its metadata, and verify that each documents a plain CLI/file fallback; installation docs cover the supported skill locations. | G3, G5, G8 |
| CAP-011 | Foil SHALL contain no MCP server, MCP transport, or runtime MCP dependency. | Dependency and repository scans find no server implementation or runtime MCP package; an end-to-end fleet scenario succeeds with MCP unavailable. | G5 |
| CAP-012 | All fleet, seat, task, pool, message, and decision status SHALL be readable from versioned machine-readable files. | A poll-status contract test reconstructs current fleet state from files alone while no terminal capture API is available. | G6 |
| CAP-013 | State writers SHALL use atomic replacement and include schema version, stable ID, state, and update timestamp. | Fault-injection tests never expose partial JSON; unsupported schema versions fail with a diagnostic; repeated reads preserve stable IDs. | G6, G7 |
| CAP-014 | Status SHALL be derived from Foil lifecycle operations, process/tmux liveness, explicit adapter probes/hooks, and recorded events—not regex matching terminal buffers. | Tests detect launch, working, waiting, idle, exited, and failed states using structured signals; a test with arbitrary terminal text cannot alter status. | G4, G6 |
| CAP-015 | The durable seat registry SHALL record seat ID, fleet ID, working directory, agent kind, native session ID when known, tmux session/window IDs, git branch, worktree path, usage-pool ID, and an extensible metadata map. | Registry round-trip tests preserve every required field and unknown metadata keys; secret-pattern fixtures are rejected from native session ID and metadata values. | G2, G6, G7 |
| CAP-016 | Registry and status locations SHALL be deterministic, documented, overrideable, and safe for concurrent local processes. | Tests resolve default and override paths on macOS and Linux; two concurrent writers cannot corrupt a record; file permissions follow the security design. | G6, G7, G8 |
| CAP-017 | Fleet relaunch SHALL apply this precedence per seat: revive a matching live tmux target; otherwise use agent-native resume when a recorded session ID and adapter resume command are available; otherwise launch fresh and log the reason. | Table-driven tests cover every branch, stale/mismatched tmux targets, absent/invalid session IDs, unavailable resume commands, and fresh-launch reason events. | G7 |
| CAP-018 | An operator SHALL be able to deliberately request fresh context for one seat without deleting its history. | A respawn-fresh test bypasses live/native resume, creates a new session lineage, and retains an auditable link to the prior seat incarnation. | G1, G7 |
| CAP-019 | Tmux session names SHALL be deterministic, collision-resistant, length-bounded, shell-safe, and prefixed `foil-`; window names SHALL follow ADR-0001 as amended by ADR-0010 and remain nested under their session. Stable IDs, not display names, SHALL provide identity. | Property tests cover punctuation, Unicode, long names, duplicate display names, and concurrent fleets; session names have the required prefix, window names match the documented grammar and nesting, and generated names map back through the registry. | G6, G7 |
| CAP-020 | Shared messages and notepads SHALL use append-safe or atomic file protocols with stable addressing, acknowledgements, and pollable delivery state. | Two-seat integration tests exchange a message and update a notepad without MCP or terminal scraping; duplicate delivery is detectable and interrupted writes remain recoverable. | G1, G5, G6 |
| CAP-021 | Memory updates SHALL be explicit, scoped, reviewable records rather than raw transcript persistence. | The memory skill can propose, accept, supersede, and reject a concise lesson; tests ensure source task/event references exist and secret-pattern content is rejected. | G1, G5, G6 |
| CAP-022 | The browser bridge SHALL be a separately launched, least-privilege local tool with explicit operator-approved targets and actions. | With the bridge stopped, core orchestration still works; with it running, an allowlisted page action succeeds and a non-allowlisted origin/action is denied and audited. | G5, G6 |
| CAP-023 | Browser bridge output and logs SHALL never include cookie values, authorization headers, storage tokens, profile database contents, or unrestricted page dumps. | Security tests seed canary secrets in cookies, headers, storage, and page content and assert they do not appear in Foil files, stdout, stderr, or audit events. | G5, G6 |
| CAP-024 | Worktree and branch identity SHALL be explicit per seat, with validation that prevents accidental cross-seat mutation. | Launch rejects an unexpected worktree/branch mismatch; status reports the recorded and observed values; two sample builder seats operate in distinct worktrees. | G1, G6, G7 |
| CAP-025 | Foil SHALL ship default role profiles for manager, requirements owner, domain designer, implementer, test/verifier, reviewer/challenger, researcher, and memory curator. | A generated starter fleet contains these roles, one primary specialization each, challenge edges, and at least two configurable usage pools. | G1, G2, G8 |
| CAP-026 | Guided setup SHALL discover available CLIs without reading credentials, validate tmux and filesystem prerequisites, configure pools/roles, and produce a dry-run fleet plan. | A clean-machine acceptance script completes setup using prompts or flags, reports missing prerequisites precisely, and produces valid config without opening source files or reading credential stores. | G2, G4, G8 |
| CAP-027 | Controller and manager documentation SHALL cover normal operation, polling, intervention, relaunch, deliberate fresh-context respawn, and failure recovery. | A documentation test maps every management command and registry/status state to a help or skill section; onboarding includes one end-to-end example. | G3, G5, G7, G8 |
| CAP-028 | Foil SHALL allow remote-capable controller CLIs to operate it through the same local CLI/file contract without adding a Foil remote service. | A contract test drives management commands from a non-interactive shell boundary; architecture contains no required Foil network listener. | G3, G5 |
| CAP-029 | Every lifecycle and scheduling decision SHALL append a structured, redacted audit event with causal identifiers. | Tests correlate fleet, seat, task, pool decision, resume attempt, and result events; canary credentials never appear in events. | G2, G6, G7 |
| CAP-030 | Foil SHALL fail closed on malformed config/state and SHALL preserve evidence needed for recovery. | Corrupt registry/config fixtures produce non-zero diagnostics, preserve the corrupt input, and do not launch or kill any process. | G6, G7, G8 |

## Quality requirements

| ID | Requirement | Acceptance criteria | Trace |
|---|---|---|---|
| CAP-031 | The core SHALL run headlessly on supported macOS and Linux environments with a documented minimum tmux version and a boring, portable runtime stack chosen in design. | CI runs unit and contract suites on macOS and Linux; installation and `foil doctor` requirements match the design record. | G5, G6, G8 |
| CAP-032 | Core domain logic SHALL be deterministic and test-first. | Every core module change includes tests committed with or before implementation; scheduling, naming, registry, resume precedence, and status readers have unit/property tests. | G2, G4, G6, G7 |
| CAP-033 | Schemas and adapter contracts SHALL be versioned and migration-ready. | Golden fixtures for the current version validate; an older fixture either migrates losslessly or fails with an actionable version diagnostic. | G4, G6, G7 |
| CAP-034 | Security boundaries SHALL assume local agent processes and browser pages may be untrusted. | Threat-model tests cover path traversal, symlink replacement, command-template injection, hostile metadata, secret redaction, and unauthorized browser targets. | G4, G5, G6 |
| CAP-035 | Documentation SHALL trace design decisions and verification evidence to these frozen requirements and goals. | A traceability check reports no implemented capability, ADR, or acceptance test without at least one CAP ID and no CAP ID without a G1–G8 trace. | G1, G2, G3, G4, G5, G6, G7, G8 |
| CAP-036 | When a validated live tmux-backed seat has queued mailbox messages, the controller SHALL be able to issue a generic, bounded wake notification without inspecting terminal content or branching on agent/provider identity. | Unit and CLI contract tests prove wake attempts occur only for queued mail, use fixed bounded literal input and argv execution without a shell, and expose the outcome as structured delivery state; a real-tmux integration test proves an idle seat can receive the wake and may skip only when tmux is unavailable. | G3, G4, G5, G6, G7 |

## Constraints and non-goals

1. No graphical fleet UI is in scope.
2. No MCP server or MCP transport is permitted.
3. Foil does not broker provider authentication or store provider credentials.
4. Foil does not promise exact remaining quota when a provider exposes no reliable signal.
5. Foil does not implement its own remote-control service; remote operation belongs to the chosen controller CLI or shell environment.
6. Browser automation is optional and may be narrower than raw CDP when required to uphold CAP-023.
7. Terminal scrollback may be inspected by a human for diagnostics, but it is not a machine status source.

## Freeze evidence

This document is the design input. Design artifacts must cite CAP IDs and must not weaken an acceptance criterion without the change rule at the top of this document.
