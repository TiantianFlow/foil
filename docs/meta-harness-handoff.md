# Meta-harness handoff (2026-11-27, America/Toronto)

Snapshot for the next agent group. Repo tip on `main` when written: **`af466aa`** ([#165](https://github.com/TiantianFlow/foil/pull/165)). No open PRs at handoff. Package version is still **0.7.0**; meta-harness work is under CHANGELOG `[Unreleased]` until [#142](https://github.com/TiantianFlow/foil/issues/142).

Parent roadmap: [#101](https://github.com/TiantianFlow/foil/issues/101).

## Where we left off

Implementation through **Phase 4** is on `main` (observe mode + full doctor in [#160](https://github.com/TiantianFlow/foil/pull/160)). After that, outside the Phase 4 wave:

| When (America/Toronto) | PR | What |
|---|---|---|
| Nov 23 ~10:03 AM | [#158](https://github.com/TiantianFlow/foil/pull/158) | Joined-lead scheduled-check rule (#157) |
| Nov 23 ~11:22 AM | [#160](https://github.com/TiantianFlow/foil/pull/160) → `1927406` | #130 observe + doctor (Phase 4 complete) |
| Nov 24 ~4:48 PM | [#164](https://github.com/TiantianFlow/foil/pull/164) | Seat instructions as system instructions (#161) |
| Nov 24 ~9:36 PM | [#165](https://github.com/TiantianFlow/foil/pull/165) → **`af466aa`** | Gemini preset replaced by **agy**; closes #162 |

## What shipped (ticket → PR)

**Phase 0/1:** #110 → [#132](https://github.com/TiantianFlow/foil/pull/132); #111–#115 → [#131](https://github.com/TiantianFlow/foil/pull/131)–[#136](https://github.com/TiantianFlow/foil/pull/136) (phase #103 closed).

**Phase 2:** #116 → [#138](https://github.com/TiantianFlow/foil/pull/138); #117 → [#137](https://github.com/TiantianFlow/foil/pull/137)+[#139](https://github.com/TiantianFlow/foil/pull/139); #118 → [#140](https://github.com/TiantianFlow/foil/pull/140); #119 evidence → [#145](https://github.com/TiantianFlow/foil/pull/145) (**issue still open**); #120 push → [#147](https://github.com/TiantianFlow/foil/pull/147) (**still open**); #121 fleet merge → [#146](https://github.com/TiantianFlow/foil/pull/146) (**still open**).

**Phase 3:** #122 → [#148](https://github.com/TiantianFlow/foil/pull/148); #123 → [#149](https://github.com/TiantianFlow/foil/pull/149); #124 → [#150](https://github.com/TiantianFlow/foil/pull/150); #125 → [#151](https://github.com/TiantianFlow/foil/pull/151); #126 → [#152](https://github.com/TiantianFlow/foil/pull/152) — **issues still open** (PRs lacked `Closes`).

**Phase 4:** #127 → [#153](https://github.com/TiantianFlow/foil/pull/153)+[#154](https://github.com/TiantianFlow/foil/pull/154); #128 → [#156](https://github.com/TiantianFlow/foil/pull/156); #129 → [#159](https://github.com/TiantianFlow/foil/pull/159) — **issues still open**; #130 → [#160](https://github.com/TiantianFlow/foil/pull/160) (closed).

Phase parents #102, #104, #105, #106 and #101 are still open. Treat **code as ahead of the issue board**.

## Cleanup for next owners

1. Close stale-open tickets **#119–#129** after a done-when check (or comment if anything is missing). Agent integrations lacked issue-write for close/comment.
2. Refresh [#101](https://github.com/TiantianFlow/foil/issues/101) **Status / Next** — the Nov 22 evening list still names #121/#119/#120 as upcoming; those PRs merged.
3. **#109** Gemini re-check: [#165](https://github.com/TiantianFlow/foil/pull/165) removed `gemini` and added `agy`. Close as superseded by #162/#165, or narrow to a live row for `agy` under #108.

## Remaining work (recommended order)

1. Housekeeping above.
2. **[#143](https://github.com/TiantianFlow/foil/issues/143)** realistic mock harnesses — next real build.
3. **[#144](https://github.com/TiantianFlow/foil/issues/144)** phone journey on mocks — extend for evidence/push/merge now on main.
4. Live / release last (need logged-in CLIs + phone): **#108**, **#141**, then **#142** (core 0.8.0 + foil-bridge 0.1.0).
5. **[#107](https://github.com/TiantianFlow/foil/issues/107)** Phase 5 — adapters only on evidence; no tickets; do not spawn.

## Operating prefs that worked

- Model: `grok-4.7-high`.
- After impl: independent architecture review, then privacy/publish review; merge when that gate + CI are green (no wait on human review for that gate).
- Public repo: extreme care on personal/sensitive info; original work only.
- Commit author: `177855728+TiantianFlow@users.noreply.github.com`.
- Behavior changes edit `docs/requirements.md` in the same PR. Read CONTRIBUTING / AGENTS first.
- Stacked PRs: sync merge sometimes refused; use GitHub merge-async when needed.

## Checklist

- [ ] Close or comment on #119–#129 (and #109).
- [ ] Update #101 Status / Next to match `main` at `af466aa`.
- [ ] Start #143, then #144.
- [ ] Do not start #108/#141/#142 without a logged-in harness machine / phone.
- [ ] Do not start Phase 5 without evidence.
- [ ] Fresh workers only for new work.
