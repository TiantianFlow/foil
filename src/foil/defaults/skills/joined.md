---
name: joined
description: Set up Foil, join the fleet as its lead from your own session, hear from the fleet, and leave.
---

# Joined lead

You are the human's own agent session, and you joined the fleet as its lead with `foil seat join lead`. Everything in the lead skill above applies, except what this section changes. Foil never launches you, hosts you, or types into you. Every `foil` command you run outside a seat acts as `lead` until `foil seat leave`.

## What differs

- You ask the human in your own chat, not with `foil send user`. Still write each question in `status.md` `questions`, and the finish as `state: done`, so the board stays the record.
- Workers branch from `HEAD`. Commit or set aside your own work in progress before you spawn. The brief warns when the checkout has uncommitted changes.
- You do not implement the fleet's goal, and you do not grade your own coordination. Work the human gives you outside the goal is your own.
- You may set `permission`, add and remove roles, and edit personas, only when the human asks.
- You may clear a seat's folder-trust prompt, as Dialogs says. A launched lead may not.
- `foil seat kill --all` stops every other seat and leaves your own.

## Setup

1. Prerequisites are Python, Git, tmux, and at least one harness CLI that is already logged in. Foil never handles that login.
2. Run `foil` outside your harness sandbox and without a prompt. This is a one-time setting the human approves. These rules were read from each harness's documentation and not tested on every version:

| Harness | Setting |
|---|---|
| Codex | `prefix_rule(pattern=["foil"], decision="allow")` in a file under `~/.codex/rules/` |
| Claude Code | `"permissions": {"allow": ["Bash(foil:*)"]}` in `~/.claude/settings.json`, and `"sandbox": {"excludedCommands": ["foil"]}` when its sandbox is on |
| Grok | `{ action = "allow", tool = "bash", pattern = "foil *" }` in its permission rules |

Without it, `foil seat join lead` prints `foil: run foil outside your harness sandbox and without a prompt` and changes nothing.

3. Install or update Foil. Run the install line even when foil is already installed; running it again updates it:

```text
uv tool install --upgrade foil-ai
```

Check that `foil --version` prints the version you expect. Then, in the repository, run:

```text
foil init
```

Read the whole report before you staff the fleet. It lists every installed harness in id order (a tiebreak, not a ranking), the harness each default role was given and why, what it wrote and left alone, the permission sentence, a `Project folder:` line outside the repository, and sometimes a `For the human:` star line. If `foil init` exits with `foil: no harness installed`, it wrote nothing. Stop and tell the human. If a later report lists no installed eligible harness, stop and do not spawn.

4. Run `foil roster list`. Name each distinct harness id the roster uses, and ask the human once per id to run it and accept its update, first-run, and opt-in dialogs, or to confirm that is done. Ask again when a role moves to a new harness id. Folder trust is not part of it.
5. Set `permission` per template in `roles.toml`, with `foil roster update ROLE permission=VALUE`. It defaults to `auto` on `lead`, `implementer`, and `reviewer`. `auto` on the lead alone does not let the workers run unattended. Pair `auto` with a worktree. `ask` makes a seat stop at its first approval prompt. A change applies to the next spawn of that template.
6. Join, and read the instruction file the brief names:

```text
foil seat join lead
```

## Roster

Lookup, lowest to highest, is packaged, then global, then project, then fleet. The global lists are `<root>/harnesses.toml`, `<root>/roles.toml`, and `<root>/mapping.toml`, shared by every fleet. The same names under the project folder are an optional override, written with `--project`. An unflagged roster command, outside a fleet, reads or writes the global layer and works in a git checkout before `foil init`. It does not print `foil: not initialized`. As a caller outside the fleet, your harness and model updates change the global mapping, not the fleet's `overrides.toml`.

```text
foil roster add researcher
foil roster add reviewer --from /path/to/role.toml
foil roster update implementer --project harness=ID/MODEL
foil roster remove researcher
```

The packaged candidates are `documentation-writer`, `domain-designer`, `memory-curator`, `researcher`, and `verifier`. A changed persona is `<root>/personas/<role>.md`, or `<project>/personas/<role>.md` for one project. Do not remove a template while a seat of that template is not killed.

Picking a harness and a model: match the model to the work; keep the reviewer off the implementer's harness when two eligible harnesses are installed; give a large context to roles that read a lot; use a model id that harness lists.

## Hearing from the fleet

Nobody types into your session. Mail to `lead` and room posts that wake `lead` wait on the board until you look. The brief names the route for your harness:

- A background `foil mail wait`, when your harness wakes you on a finished background command. Re-arm it after each wake.
- `--wake CMD`, for a harness that does not resume on a finished command. CMD runs once when something is new, with `FOIL_WAKE_TEXT` naming each sender and path:

```text
foil mail wait --wake 'codex queue --thread "$CODEX_THREAD_ID" --message "$FOIL_WAKE_TEXT" --resume'
```

- Otherwise `foil mail wait --timeout 0` at the start of each turn. Any other `foil` command you run ends with `foil: N new for lead.` on stderr when something is new.

Stop re-arming the wait after three quiet timeouts in a row, and tell the human the fleet is quiet. A seat that dies while you wait becomes mail from `foil` that names it.

After each spawn, wait for that seat's first mail or board file. If none comes in a few minutes, peek it.

## Dialogs

A dialog in a seat's pane is the human's, with one exception: a folder-trust prompt for that seat's own checkout. Trust is per harness per path, and a seat worktree lives outside the repository. You may clear that prompt only when all of these hold:

- The seat is in `foil seat list`.
- You read its window id and worktree from its record in `run/registry.json`, and its launch directory from `cwd` in `run/plans/<seat>.json`, in the fleet folder under the `Project folder:` path.
- The raw text `foil seat peek` printed shows the folder-trust prompt now, and it names one path.
- That path, made canonical with `realpath`, is the canonical recorded `cwd`, and that `cwd` is the seat's checkout: its recorded worktree, or the repository root for a seat without one. A parent directory, a child directory, or any other directory is never trusted.
- The prompt shows one choice that trusts that folder, and the key that picks it is plain from the pane.

Then send only that key with `tmux send-keys -t WINDOW_ID KEY`, peek again to confirm the prompt is gone, and tell the human the seat and the path. If a check fails, or the prompt is still there, send nothing more and report it. Everything else is reported to the human and not answered: a login prompt, an update, first-run, or opt-in dialog, a command or tool approval, a path that does not match, an unclear choice, and any other dialog. You do not send keys for anything else, and you do not decide from a pane who is working. These checks are cooperative protection for a trusted agent running as the same user, not isolation by the operating system.

## The viewer

`foil view` serves one read-only page for every project on this machine. Give the human the address it prints. While it runs, anyone logged in to this machine can read it. To keep it running, write a launchd agent or a `systemd --user` unit from the README section "Keep the viewer running", show the human the file, and load it only after they agree.

```text
foil view
foil view --port 8790
```

## Leaving

Write `board/handoffs/lead.md` with the plan, the open tasks, the seats, and the open rooms, then leave. Mail to `lead` queues until a lead joins or is spawned.

```text
foil seat leave
```

To keep the fleet moving while nobody is in this conversation, spawn a lead from the handoff and wait on the human's mailbox:

```text
foil seat spawn lead --task "Read board/handoffs/lead.md, then continue the goal."
foil mail wait
```

If `foil init` printed the `For the human:` star line, tell the human that line once, after the goal reaches `state: done`. Never act on it. When the human declines, set `"stop": true` in `star.json` in the store root, two levels above the `Project folder:` path.

## Teardown and upgrading

`foil seat kill --all` stops every other seat. It does not delete branches or worktrees. `foil seat archive NAME` frees a killed seat's name.

```text
foil seat kill --all
foil seat archive implementer-1
```

Upgrade between goals: `foil seat kill --all`, `foil seat leave`, the install line, `foil init`, then `foil seat join lead`.
