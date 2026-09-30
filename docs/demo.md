# Demo: fix a failing test with a three-agent team

This is one real run, from `foil init` to a merged fix. It is the run the
end-to-end suite checks as scenario 1, replayed by hand with the real
`foil` command, real tmux, and real Git. Only the agents were scripted.

Everything shown under a command below is Foil's own output from that run.
It looks the same whatever harness you use. What each agent shows in its
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
Read .foil/skills/operator.md and follow it. My goal: <goal>.
permission = "ask"
```

`foil init` writes `.foil/` and keeps it out of `git status`. The first
line of its output is what you paste into your own agent later. The second
is the lead template's permission.

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
`permission = "ask"` makes a seat stop at its first approval prompt and
wait for you in its window. For an unattended run, set
`permission = "auto"` on every template whose seat should work alone.
A harness may still ask before some commands, so peek new seats.

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
full instructions as the first prompt. The task becomes a mail file, and
Foil types one nudge line into the lead's window: the sender and the path
of that file.

## 4. Watch the team form

```sh
foil seat list
```

```text
implementer-1	implementer	alive	/path/to/your-repo/.foil/worktrees/implementer-1
lead	lead	alive
reviewer-1	reviewer	alive
```

The lead spawned an implementer and a reviewer. The implementer works in
its own worktree on branch `foil/implementer-1`. `alive` means the seat's
tmux window still exists and carries that seat's markers. It does not say
whether the agent is busy.

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
ls .foil/board/mail/lead
cat .foil/board/status.md
```

```text
20260928T005855Z-user-40eda840.md
20260928T005856Z-implementer-1-df1d08d0.md
20260928T005857Z-reviewer-1-d5045b2b.md
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

Every message is a file: your task, the implementer's report, and the
reviewer's approval. `status.md` is the lead's own report. When the lead
needs you, it lists questions there, and you answer with
`foil send lead "..."`.

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
implementer-1	implementer	killed	/path/to/your-repo/.foil/worktrees/implementer-1
lead	lead	killed
reviewer-1	reviewer	killed
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
