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
uv tool install "git+https://github.com/TiantianFlow/foil.git"
cd your-repo
foil init
```

```text
foil 0.3.1
...
Read .foil/skills/operator.md and follow it. My goal: <goal>.
```

`foil init` writes `.foil/` and keeps it out of `git status`. The first
line of its output is the version. The report in between (trimmed here)
names the installed harnesses and what init wrote. The last line is what
you paste into your own agent later.

## 2. Choose a harness and a permission for each role

Each role is a template in `.foil/templates/`. Init picks one installed
CLI for all three. You can give each role a different one, for example a
strong planner for the lead and a model from another vendor for the
reviewer:

```toml
# .foil/templates/implementer.toml
harness = "codex"
persona = "personas/implementer.md"
worktree = true
permission = "auto"
```

`worktree = true` gives each implementer its own Git worktree and branch.
Omitting `permission` means `auto`, and the seat runs unattended.
`permission = "ask"` is the explicit choice: the seat stops at its first
approval prompt and waits for you in its window. A harness may still ask
before some commands, so peek new seats.

## 3. Hand over the goal

In your own agent (Claude Code, Codex, or any other), in the repository,
paste the line from `foil init` with your goal:

```text
Read .foil/skills/operator.md and follow it. My goal: make the tests pass.
```

That agent becomes the operator. It checks that a lead can start, then
sends it the goal. To do the same by hand:

```sh
foil seat spawn lead --task "Make the tests pass."
```

Foil opens a tmux window named `lead` and starts the lead's CLI with its
full instructions as the first prompt. The task becomes a mail file before
the CLI starts, and the instructions name that file and tell the lead to
read it first. Foil types nothing into the new window, so a CLI that opens
on a trust or update dialog waits there.

## 4. Watch the team form

```sh
foil seat list
```

```text
implementer-1	implementer	alive	/path/to/your-repo/.foil/worktrees/implementer-1	codex	You make the change the task asks for, and you do not review your own work.
lead	lead	alive		claude	You are the lead seat of this fleet. Your role guidance is the lead skill.
reviewer-1	reviewer	alive		claude	You check the change the lead names and report findings, and you do not fix them.
```

The lead spawned an implementer and a reviewer. The implementer works in
its own worktree on branch `foil/implementer-1`. `alive` means the seat's
tmux window still exists and carries that seat's markers. It does not say
whether the agent is busy. The last two columns are the harness the seat
was launched with, written `harness/model` when its template sets a model,
and the first line of its role's persona.

## 5. Look inside a seat

```sh
foil seat peek lead --lines 8
```

```text
...
implementer-1 /path/to/your-repo/.foil/board/mail/lead/20260928T005856Z-implementer-1-df1d08d0.md
...
reviewer-1 /path/to/your-repo/.foil/board/mail/lead/20260928T005857Z-reviewer-1-d5045b2b.md
```

Peek prints the raw bottom of that window (trimmed here). Among the
agent's own output are two nudge lines: the implementer reported its fix,
and the reviewer approved it. With a real
harness you see its full screen. Foil never interprets what is there.

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
message is still a file under `.foil/board/`. `status.md` is the lead's
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
implementer-1	implementer	killed	/path/to/your-repo/.foil/worktrees/implementer-1	codex	You make the change the task asks for, and you do not review your own work.
lead	lead	killed		claude	You are the lead seat of this fleet. Your role guidance is the lead skill.
reviewer-1	reviewer	killed		claude	You check the change the lead names and report findings, and you do not fix them.
```

Every window is closed. Branches and worktrees stay, and nothing from
Foil shows up in `git status`.

## If nothing happens

Run `foil seat peek lead`. A seat that makes no progress is usually
showing one of these:

- a login prompt: log in to that CLI once by hand, then
  `foil seat kill lead` and spawn it again;
- a folder-trust prompt: accept it in that window, or run the CLI once
  in the repository before the first fleet;
- an approval prompt: approve it in that window (`tmux ls` lists Foil's
  session, and `tmux attach` opens it), or set `permission = "auto"`;
- the harness's own first-run or opt-in dialog: answer it the same way.

If tmux died or the machine rebooted, `foil seat resume` restarts every
seat that was not killed.
