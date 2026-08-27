# Default Roles and Manager Workflow

Status: Proposed  
Requirements: CAP-003, CAP-004, CAP-010, CAP-025–CAP-027

## Role rules

Each seat receives one primary specialization, a bounded context pack, explicit inputs/outputs, and challenge obligations. Roles do not share a full controller transcript by default. The same role may be implemented by different CLI/model/pool combinations, but one fleet should deliberately vary those combinations where capacity permits.

## Starter roles

| Role | Single specialization | Required output | Challenges / challenged by |
|---|---|---|---|
| Manager | Coordination and synthesis | Task graph, staffing/dispatch decisions, progress interventions, final synthesis | Challenged by requirements owner and reviewer |
| Requirements owner | Testable product intent | Frozen requirements and acceptance criteria | Challenges manager scope and designer traceability |
| Domain designer | Domain boundaries and decisions | Domain model, architecture, ADRs | Challenged by implementer and reviewer |
| Implementer | Working production change | Scoped code and implementation notes | Challenged by test/verifier and reviewer |
| Test/verifier | Executable evidence | Failing-first tests, verification report, coverage gaps | Challenges implementer and designer assumptions |
| Reviewer/challenger | Defect-first independent review | Actionable findings or explicit approval | Challenges every deliverable; does not implement it |
| Researcher | Evidence gathering | Sourced digest with verified/unknown separation | Challenged by requirements owner for relevance |
| Memory curator | Durable lessons | Concise accepted/superseded/rejected lessons with evidence links | Challenged by manager for signal quality |

The default is a role catalog, not a requirement to run eight seats for every task. Setup proposes the smallest complementary subset while preserving independent verification for risky changes.

## Manager duties

The outside controller performs these duties through the manager skill:

1. Translate the user's objective into testable work items and preserve the frozen requirement boundary.
2. Select one specialization per seat and intentionally distribute seats across available usage pools/model families.
3. Record challenge edges before dispatch so independent seats know what assumptions to test.
4. Write durable task briefs with absolute project/worktree paths and acceptance criteria.
5. Dispatch according to capability, pool evidence, active load, and operator policy; record why.
6. Poll structured status files and message acknowledgements rather than infer progress from terminal buffers.
7. Intervene on blocked, stale, failed, or contradictory work; avoid blind retries.
8. Require test/verifier evidence and independent review before synthesis.
9. Resolve disagreements by requirements and evidence, not majority vote or shared-context convergence.
10. Preserve seat/session continuity by relaunch precedence, while deliberately respawning a seat when fresh context is valuable.
11. Curate durable lessons after completed work; never persist transcripts or secrets as memory.
12. Return one coherent result to the user, including unresolved risks and evidence.

The manager may implement simple work itself only when it is acting as the user's chosen primary agent and doing so does not erase required independent challenge or verification. Foil does not enforce a supervisor that is forbidden to work; it enforces visible roles, decisions, and evidence.

## Guided setup output

The setup flow:

1. Detects platform, tmux, Git, and candidate agent executables using version commands only.
2. Asks the operator to define usage pools; it never reads authentication stores.
3. Offers the starter roles and explains the one-specialization rule.
4. Lets the operator map each selected role to adapter, model, pool, context reference, and worktree policy.
5. Adds challenge edges and validates complementary staffing.
6. Writes desired TOML configuration and a dry-run plan.
7. Runs `foil doctor` and stops before launch until errors are resolved.

## Default review loop

```text
requirements owner freezes acceptance criteria
        |
domain designer records decisions
        |
implementer receives one bounded task
        |
test/verifier produces independent evidence
        |
reviewer/challenger reports findings
        |
implementer addresses accepted findings
        |
test/verifier + reviewer confirm
        |
manager synthesizes and memory curator distills lessons
```

The manager can parallelize independent research, implementation, and verification, but a seat does not review its own output and review context should omit irrelevant implementation deliberation where possible.
