# Architecture

Status: Proposed  
Requirements baseline: frozen v0.5
Domain model: `docs/design/domain-model.md`

## Architectural shape

Foil is a short-lived command-line application plus durable local files. The user's chosen controller agent invokes commands, reads structured results, and performs the manager role through portable skills. Foil does not embed a model, require a daemon, expose a fleet UI, or listen on a network port.

```text
outside controller agent
        |
        | foil CLI + portable skills
        v
application services
  |       |        |         |
fleet   runtime  polling  collaboration
plan    continuity status   files
  |       |
adapter catalog ---- tmux / agent CLI / git
        |
optional capability-bounded browser bridge
```

This shape keeps the controller replaceable (CAP-001–CAP-002, CAP-028), makes disk state authoritative (CAP-012–CAP-014), and keeps external CLI differences outside core control flow (CAP-008–CAP-009).

## Stack decision

### Runtime

- Python 3.11 or newer
- Standard library for domain models, JSON/TOML parsing, `argparse`, subprocesses, hashing, atomic filesystem operations, and advisory file locks
- Tmux as the live continuity layer
- Git CLI for worktree/branch observation

### Development

- `pytest` for readable table and integration tests
- `hypothesis` for naming, serialization, and hostile-input properties
- `ruff` for formatting and static lint rules
- JSON Schema documents as interchange specifications; core parsing still enforces invariants in domain constructors

### Rationale

Python and tmux are already common prerequisites for CLI-agent environments and work on the target macOS/Linux platforms. A standard-library core minimizes installation and supply-chain cost. Typed Python domain records and explicit validators are sufficient for the first vertical slice; an ORM, database, web framework, message broker, and daemon would add failure modes without satisfying a requirement. Pytest/Hypothesis make the test-first and property-test requirements direct. This decision traces to CAP-031–CAP-034.

## Source layout

```text
src/foil/                    CLI, runtime, registry, adapters, onboarding
src/foil/cli/                argument parsing and JSON/human output
src/foil/templates/roles/    default one-specialization role profiles
src/foil/resources/          packaged adapter records and schemas
schemas/                     versioned config and profile schemas
adapters/                    built-in declarative CLI adapter records
skills/
  controller/
  manager/
  worker/
  poll-status/
  shared-notepads/
  memory-update/
  adapters/
tests/
  unit/
  contract/
  integration/
  e2e/
docs/
```

Dependencies point inward: infrastructure and CLI depend on application/domain; domain imports neither external process code nor adapter IDs.

## Configuration and state

Desired project configuration is reviewable TOML under `<project>/.foil/`. Runtime state is not kept in a worktree:

1. `FOIL_STATE_DIR`, when explicitly set.
2. `<git-common-dir>/foil/` for a Git project, shared by its worktrees.
3. `$XDG_STATE_HOME/foil/projects/<project-id>/` or the documented platform fallback for a non-Git project.

The versioned runtime shape is:

```text
<state-root>/v1/
  fleets/<fleet-id>/
    fleet.json
    seats/<seat-id>.json
    status/fleet.json
    status/seats/<seat-id>.json
    events/events.jsonl
    mailboxes/<seat-id>/{inbox,ack}/
    notepads/<notepad-id>.json
    memory/<lesson-id>.json
    locks/
```

Per-seat records avoid a single registry write hotspot. Unknown keys are preserved under the explicit `extensions` map. Every state document has `schema_version`, stable IDs, and an RFC 3339 UTC update timestamp. State directories are mode `0700` and state files mode `0600` by default. Writers lock the narrow aggregate, write a same-directory temporary file, flush and fsync, atomically replace, and fsync the directory. Readers reject symlinks, non-regular files, oversized records, malformed JSON, unsupported schema versions, and invariant failures before performing side effects. This traces to CAP-013, CAP-015–CAP-016, CAP-020–CAP-021, CAP-030, and CAP-033–CAP-034.

Registry secret-pattern rejection is heuristic defense-in-depth that reduces
accidental persistence of common token shapes; it is not a credential boundary.
Foil neither accepts nor brokers provider credentials, and callers must not
place credentials in registry or status data.

## Process execution boundary

Adapter command templates are arrays of literal argv tokens and typed placeholders. Foil never assembles provider commands as shell strings. Placeholder expansion:

- substitutes one value into one argv element;
- rejects missing/unknown placeholders;
- applies field-specific length and character constraints;
- never expands environment variables or command substitutions;
- records redacted argv shape, not credential-bearing values.

External commands run with explicit working directory, bounded inherited environment, timeout, output-size limit, and separate stdout/stderr capture. Authentication remains native to each CLI and outside Foil storage. This traces to CAP-008, CAP-015, and CAP-034.

## Adapter contract

An adapter record describes capabilities rather than selecting a code path by name:

```toml
schema_version = 1
id = "example-cli"
skill = "skills/adapters/example-cli/SKILL.md"

[executable]
candidates = ["example"]
version_argv = ["example", "--version"]

[launch]
argv = ["example", "--model", "{model}"]

[resume]
supported = true
argv = ["example", "--resume", "{native_session_id}"]

[session_capture]
kind = "json_file"
path = "{adapter_state_dir}/sessions/{seat_id}.json"
json_pointer = "/session_id"

[status_probe]
kind = "command_json"
argv = ["example", "session", "status", "--json", "{native_session_id}"]
state_pointer = "/state"
```

Allowed capture/probe kinds are generic infrastructure capabilities. New kinds require an architecture change; new CLIs using existing kinds require only config and documentation. Adapter conformance tests execute fixture records through the same loader and ports. This is the mechanism behind CAP-008–CAP-009 and does not claim every external CLI exposes all capabilities.

## Runtime continuity flow

For each seat:

1. Load and validate the seat record and adapter.
2. If deliberate fresh context is requested, choose `start_fresh` with a new incarnation and retain lineage.
3. Probe the recorded tmux target by stable fleet/seat marker.
4. If the matching target is alive, choose `revive_tmux`.
5. If liveness is indeterminate, stop with `blocked`; do not guess.
6. If tmux is absent/stale and a validated native session ID plus resume capability exist, choose `resume_native`.
7. Otherwise choose `start_fresh` with an explicit reason.
8. Record attempt and result events. A failed native resume is evidence for a subsequent logged fresh-start decision; it is never silently retried.

The resolver is pure and table-tested. Side effects occur only after a validated decision. This traces to CAP-017–CAP-019, CAP-029–CAP-030.

## Status projection and polling

Seat states are `launching`, `working`, `waiting`, `idle`, `exited`, `failed`, `blocked`, or `unknown`. Evidence sources are:

1. Foil command lifecycle and result events;
2. verified process/tmux liveness;
3. explicit adapter hook, file, process, or structured command probes;
4. operator transitions recorded through the CLI.

Terminal content is never an evidence source. `poll-status` reads status documents, validates them, and can report age/staleness as reader metadata without rewriting source state. It returns JSON by default for agent callers and an optional concise table for humans. This traces to CAP-012–CAP-014 and CAP-027.

## Pool accounting and dispatch

Pool state is evidence, not billing truth. A pool projection contains:

- configured concurrency limit and active assignments;
- optional manual availability;
- last bounded command-probe observation;
- rate-limit/cooldown observations from structured adapter events;
- update time and confidence/source;
- extensible metadata.

The scheduler first filters by capability and safety constraints, then ranks by operator policy, availability class, active load ratio, cooldown, and stable tie-breaker. Unknown headroom follows explicit policy and never becomes a fabricated quota number. Every decision records candidates, redacted factors, rejections, selected seat/pool, and policy version. This traces to CAP-005–CAP-007 and CAP-029.

## Collaboration files

Messages are immutable envelopes written to a seat inbox with a unique message ID and causal IDs. Acknowledgements are separate immutable records, making duplicate delivery observable. Notepads use optimistic version checks and atomic replacement. Memory lessons are bounded conclusions with evidence references and a review state; they are not transcripts. File names derive from validated stable IDs, never message content. This traces to CAP-020–CAP-021 and CAP-034.

For a validated live tmux-backed seat with queued mail, the controller may issue
one bounded generic wake after the message commit. The wake uses native tmux IDs,
a fixed literal prompt to poll the mailbox, argv execution without a shell, and no
terminal reads or retries. Message bodies never enter tmux input. Wake success is
advisory and pollable separately from the immutable acknowledgement that completes
delivery. This traces to CAP-036 and ADR-0009.

## Browser boundary

Raw CDP or Playwright handles are never handed to agents. The optional bridge runs separately and exposes a narrow local CLI over a permissioned Unix-domain socket or one-shot subprocess protocol. The operator grant binds allowed origins, tab identity, action vocabulary, expiry, and output limits. High-level actions return minimal typed results; cookie APIs, authorization/network header access, storage export, arbitrary JavaScript, unrestricted DOM/page dumps, and profile filesystem access are absent.

The bridge process is trusted more highly than worker seats because browser automation protocols can access authenticated state. Initial support must use a dedicated browser profile or a browser-side component without cookie permission unless a security ADR demonstrates equivalent containment for a human profile. Compromise of a raw CDP client would violate CAP-023, so raw endpoint disclosure is not an acceptable design. Core operation remains independent of the bridge. This traces to CAP-022–CAP-023 and CAP-034.

## Skills and roles

The controller skill teaches the outside agent how to invoke and poll Foil. The manager skill defines decomposition, staffing across pools, challenge assignment, progress polling, intervention, and synthesis duties. Tool skills document narrow CLI/file contracts and safe fallbacks. Adapter-specific quirks belong in adapter skill documents.

Default roles each own one specialization: manager, requirements owner, domain designer, implementer, test/verifier, reviewer/challenger, researcher, and memory curator. Context references are intentionally role-scoped; the setup plan does not copy one shared transcript into every seat. This traces to CAP-003–CAP-004, CAP-010, and CAP-025–CAP-027.

## Verification strategy

1. Unit tests cover pure invariants, naming, dispatch, resume resolution, redaction, and serialization.
2. Contract tests run arbitrary fixture adapters through the generic adapter boundary.
3. Integration tests use temporary state roots and real tmux where available.
4. Security tests use canary secrets, malicious paths/metadata/templates, symlinks, oversized inputs, and interrupted writers.
5. Documentation tests verify CAP-ID traceability, skill presence, command coverage, and the no-MCP/no-kind-branch policies.
6. CI runs supported macOS and Linux matrices.

The first vertical slice is registry read/write, tmux naming, resume resolution, and poll-status reading. It has no live agent dependency, allowing deterministic red/green TDD before process adapters are introduced.
