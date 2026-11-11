# Demo: fix a failing test with a three-agent team

This is one real run, from `foil init` to a merged fix. It is the run the
end-to-end suite checks as scenario 1, replayed by hand with the real
`foil` command, real tmux, and real Git. Only the agents were scripted.

Everything shown under a command below is Foil's own output from that run,
with the harnesses from step 2 in place of the test harness. What each agent shows in its
own window will differ, and with real agents the run takes minutes rather
than seconds. To replay the scripted run from a Foil checkout:

```sh
uv run --frozen --extra dev pytest tests/e2e/test_scenarios.py -k scenario_1
```

## The starting point

A Git repository where `check_calc.py` fails because `calc.py` subtracts
instead of adding. You need Python 3.11+, Git, tmux 3.2+, and at least one
agent CLI that is already logged in on this machine. Foil never handles
that login.

## 1. Install and initialize

```sh
uv tool install --upgrade foil-ai
cd your-repo
foil init
```

```text
foil 0.6.3
...
Project folder: /var/foil/projects/your-repo-1a2b3c
Run foil seat join lead, then read the file it names. My goal: <goal>.
```

`foil init` writes that project folder outside the repository and writes
nothing into the working tree, so `git status` stays clean. The first
line is the version `foil --version` prints. The report in between
(trimmed here) names the installed harnesses and what init wrote. Paste
the last line into your own agent, with your goal in place of `<goal>`.

## 2. Choose a harness and a permission for each role

Each role is a row in the global lists `<root>/roles.toml` and
`<root>/mapping.toml`. Init picks one installed CLI for all three. You can
give each role a different one, for example a strong planner for the lead
and a model from another vendor for the reviewer. `foil roster update`
writes the same rows. A `--project` override is those same files under the
project folder, written after `foil init`, because init deletes a project
copy it finds.

```toml
# <root>/roles.toml
[[role]]
id = "implementer"
persona = "personas/implementer.md"
worktree = true
permission = "auto"

# <root>/mapping.toml
[[map]]
role = "implementer"
harness = "codex"
```

Add another `[[map]]` row, with an optional `model`, when a later seat of that role should take the next entry. One entry stays on that entry.

`worktree = true` gives each implementer its own Git worktree and branch.
Omitting `permission` means `auto`, and the seat runs unattended.
`permission = "ask"` is the explicit choice: the seat's headless turns
refuse any action that needs approval, and the seat reports what it
needed. A harness may still refuse some commands under `auto`.

## 3. Hand over the goal

In your own agent (Claude Code, Codex, or any other), in the repository,
paste the last line from `foil init`, with your goal filled in:

```text
Run foil seat join lead, then read the file it names. My goal: make the tests pass.
```

That agent joins as the lead, a seat with no window, and plans the goal in
your conversation. It hears from the fleet with `foil mail wait`. This
demo replays the other way in, a lead Foil launches in tmux, which is
what you type by hand:

```sh
foil seat spawn lead --task "Make the tests pass."
```

Foil opens a tmux window named `lead` and starts the lead's CLI with its
full instructions as the first prompt. The task becomes a mail file before
the CLI starts, and the instructions name that file and tell the lead to
read it first. The lead's CLI runs headless, one process per turn: when a
turn ends, Foil's runner waits, and it starts the next turn on the same
session when mail or a room post for the lead arrives. Foil types nothing
into the window.

## 4. Watch the team form

```sh
foil seat list
```

```text
implementer-1	implementer	working	/var/foil/projects/your-repo-1a2b3c/worktrees/implementer-1	codex	019a4c2e-7d3b-7f10-a2c4-5e8f9b1d0c63	You make the change the task asks for, and you do not review your own work.
lead	lead	idle		claude	3f1c2a9e-5b7d-4e21-9a0c-6d8b2f4e1a77	You oversee the fleet: you ask other seats for proposals, weigh them against the goal, and choose, and you do not do the work yourself.
reviewer-1	reviewer	working		claude	b84e0d52-1c9a-4f36-8e7b-2a5d6c3f9e10	You check the change the lead names and report findings, and you do not fix them.
```

The lead spawned an implementer and a reviewer. The implementer works in
its own worktree on branch `foil/implementer-1`. `working` means a turn is
running in the seat's window, and `idle` that its runner waits for mail; a
seat whose turns keep failing is `paused`. Those come from the runner's own
record, not from the screen. The last three columns are the harness the
seat was launched with, written `harness/model` when its template sets a
model, its session id, and the first line of its role's persona.

## 5. Look inside a seat

```sh
foil seat peek lead --lines 8
```

```text
...
foil: turn ended (exit 0)
foil: turn started (turn)
...
foil: turn ended (exit 0)
foil: turn started (turn)
...
foil: turn ended (exit 0)
```

Peek prints the raw bottom of that window (trimmed here). Between the
agent's own output are the runner's lines around each turn: the lead's
turns that read the implementer's fix and the reviewer's approval. With a
real harness you see each turn's full output. Foil never interprets what is
there.

Each of those turns starts from a prompt the runner writes. It lists the
new files and inlines a short mail's body, for example:

```text
New for lead (1):
2026-09-28T00:58:56Z implementer-1 /var/foil/projects/your-repo-1a2b3c/fleets/main/board/mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
```

## 6. Read the mail and the lead's report

```sh
foil board list 'mail/lead/*.md'
foil board read status.md
```

```text
mail/lead/20260928T005855Z-user-40eda840.md
mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
mail/lead/20260928T005857Z-reviewer-1-d5045b2b.md
```

```text
---
contract: status/v1
state: done
updated: 2026-09-26T00:00:00Z
questions: []
---

## Checklist

- [x] Fix the failing test (implementer-1)
- [x] Review the fix (reviewer-1)
- [x] Merge foil/implementer-1

The tests pass.
```

Use `foil board list` and `foil board read` from outside the fleet; seats
prefer `foil mail list` and `foil mail read` for their mailbox. Every
message is a file under that fleet's `board/`, in the project folder. `status.md` is the lead's
own report. When the lead needs you, it lists questions there, and you
answer with `foil send lead "..."`.

## 7. Check the result

```sh
git log --oneline --graph
python3 -m pytest -q check_calc.py
```

```text
* 1afdc55 fix add
* fc11425 base: failing test
```

```text
1 passed
```

The lead merged `foil/implementer-1`. The test now passes.

## 8. Tear down

```sh
foil seat kill --all
foil seat list
```

```text
implementer-1	implementer	killed	/var/foil/projects/your-repo-1a2b3c/worktrees/implementer-1	codex	019a4c2e-7d3b-7f10-a2c4-5e8f9b1d0c63	You make the change the task asks for, and you do not review your own work.
lead	lead	killed		claude	3f1c2a9e-5b7d-4e21-9a0c-6d8b2f4e1a77	You oversee the fleet: you ask other seats for proposals, weigh them against the goal, and choose, and you do not do the work yourself.
reviewer-1	reviewer	killed		claude	b84e0d52-1c9a-4f36-8e7b-2a5d6c3f9e10	You check the change the lead names and report findings, and you do not fix them.
```

Every window is closed. Branches and worktrees stay, and nothing from
Foil shows up in `git status`. A leftover seat worktree is
`<project folder>/worktrees/<label>`. Remove one that is clean with
`git worktree remove <path>`. A dirty tree needs `--force`, and that
discards uncommitted work.

## If nothing happens

Run `foil seat list`, then `foil seat peek lead`. No seat stops on a
dialog: each turn runs headless, and a turn that needs a person ends.

- `paused`: three retries of a failed turn failed too. Foil mailed the
  reason to the lead, or to you when the lead is the seat. Fix the cause,
  such as a login that ran out (run that CLI once by hand and accept its
  update, first-run, and opt-in dialogs), then `foil seat resume lead`.
- `dead`: the window or its runner is gone. Foil mailed that too.
  `foil seat resume lead` starts it again on the same session.

If tmux died or the machine rebooted, `foil seat resume` restarts every
seat that was not killed.
