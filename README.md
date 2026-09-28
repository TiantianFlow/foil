# Foil · 运筹

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.0-informational)](https://github.com/TiantianFlow/foil)
[![CI](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml/badge.svg)](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS-lightgrey)](pyproject.toml)
[![Runtime dependencies](https://img.shields.io/badge/runtime%20dependencies-none-brightgreen)](pyproject.toml)

[中文](README.zh-CN.md)

**Your agents' loyal opposition.**

Foil turns the coding agents you already use (Claude Code, Codex, Gemini, OpenCode, Grok) into a small team. You talk to one agent. It hands your goal to a lead. The lead plans the work, gives each task to a worker with its own Git branch, has another agent check the result, and merges it. Every agent runs in tmux, and every message is a file you can read.

## Why Foil

### Stop managing your agents by hand

The pattern that works is a split: one agent plans, another implements, a third reviews. Claude Code even ships an `opusplan` setting that plans with Opus and builds with Sonnet. Across tools, though, the split usually means you do the plumbing: copy the plan into another terminal, relay the result back, remind each agent what was decided.

Foil makes the split a structure. The lead plans, spawns workers by role, sends them tasks as mail, and merges their branches. You talk to one operator agent, and the lead does the managing.

### Use the best model for each job, from any provider

Models differ. Some reason better, some write code faster, some cost less, and some still have usage left this week. In Foil, each role is a template that names a harness and a model. The lead can plan on a strong reasoning model, implementers can run on a fast one, and the reviewer can come from a different vendor.

That last part is the loyal opposition. A model reviewing its own code tends to repeat its own assumptions. A reviewer from another vendor brings different blind spots, so it catches different mistakes. Each seat also uses its own CLI's login, so the work spreads across subscriptions you already pay for. Foil never stores your keys or logins.

### Keep each context small, and the state on disk

One long session collects everything: the plan, every file it read, every dead end. Quality drops as the context fills, and compaction loses details. Each Foil seat sees only its role and its task. Tasks, results, and status are files on disk, so they survive compaction, a dead tmux session, or a reboot. `foil seat resume` brings dead seats back.

| | One agent session | Foil |
|---|---|---|
| Who manages the work | You, between terminals | The lead |
| Models | One model, from one vendor | A harness and model per role |
| Review | The author checks itself | A separate seat, from another vendor if you like |
| Context | One growing conversation | One focused context per seat |
| After a crash | Whatever the CLI saved | Mail, status, and branches on disk; `foil seat resume` |
| What you see | One chat | Your operator agent, plus `foil seat peek` into any seat |

## How it works

```mermaid
flowchart TB
  you(["You"])
  operator["<b>Operator agent</b><br/>the harness you chat with<br/>e.g. Claude Code or Codex"]
  foil[["<b>foil</b><br/>command-line tool"]]

  subgraph fleet["tmux session · headless agents, each on the harness and model you pick"]
    lead["<b>Lead</b><br/>plans · delegates · merges"]
    impl["<b>Implementer</b><br/>writes the code"]
    rev["<b>Reviewer</b><br/>checks the work"]
  end

  subgraph disk["On disk"]
    board[("<b>.foil/board</b><br/>mail · status.md")]
    tree[("<b>Git worktree</b><br/>branch foil/implementer-1")]
  end

  you <-->|chat| operator
  operator -->|"foil init · seat spawn lead<br/>send · seat peek"| foil
  lead -->|"seat spawn · seat kill · send"| foil
  foil -->|"launch seats · write mail · type a nudge line"| fleet
  fleet <-->|read and write| disk
  impl -->|commit| tree
  lead -->|merge| tree
  operator -.->|reads status.md| board

  classDef human fill:#fde68a,stroke:#b45309,color:#1f2937
  classDef yours fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937
  classDef tool fill:#e5e7eb,stroke:#374151,color:#1f2937
  classDef seat fill:#bbf7d0,stroke:#15803d,color:#1f2937
  classDef data fill:#fbcfe8,stroke:#be185d,color:#1f2937
  class you human
  class operator yours
  class foil tool
  class lead,impl,rev seat
  class board,tree data
```

- **You** talk only to the operator agent.
- **Operator agent**: any harness you like, with its normal interface. It follows the operator skill and never does the project work itself.
- **foil**: this command-line tool. It launches seats in tmux, writes mail, and types a one-line nudge into the recipient's window. It runs no daemon and never interprets what is on a seat's screen.
- **Lead, implementer, reviewer**: agent CLIs running headless in tmux windows, each started with its role's instructions. By default only the implementer gets its own Git worktree and branch.
- **.foil/board**: mail, notes, and `status.md`, as plain files the seats read and write.

## Demo

[docs/demo.md](docs/demo.md) walks through a real run: a failing test, a lead, an implementer, and a reviewer, from `foil init` to the merged fix.

## Quick start

You need Python 3.11+, Git, tmux 3.2+, and the Claude CLI, already logged in on this machine. Foil does not handle that login.

Install Foil 0.2.0:

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
```

In a Git repository:

```sh
cd your-repo
foil init .
```

`foil init` writes `.foil`, including the templates and `.foil/skills/operator.md`. It sets `harness` to the first installed CLI among `grok`, `claude`, `codex`, `opencode`, and `gemini`. This walkthrough uses Claude. If init chose another harness, set `harness = "claude"` in `.foil/templates/lead.toml`, `.foil/templates/implementer.toml`, and `.foil/templates/reviewer.toml`. Leave `permission = "ask"`.

Load `.foil/skills/operator.md` into your harness and follow that skill. Tell the harness the goal. The skill starts the fleet, checks in, relays what you say, and tears the fleet down. It spawns the lead once. If that lead is already running, it sends the goal with `foil send` instead of spawning the lead again.

The same commands, if you run them yourself:

```sh
foil seat spawn lead --task "Summarize this repository in board/status.md"
foil seat list
foil seat peek lead
```

Read `.foil/board/status.md` for the lead's own report. To pass an answer to the lead:

```sh
foil send lead "the answer"
```

When you are done:

```sh
foil seat kill --all
```

That stops every seat. It does not delete branches or worktrees.

## Onboarding

This is the path from install to a lead that is working.

1. Install Foil. You need Python 3.11+, Git, tmux, and at least one harness CLI that is already logged in. Foil does not handle that login.

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
```

2. In the repository, run `foil init`. It writes `.foil/` and picks an installed harness. Its output ends with the pointer line and the lead template's permission.

```sh
cd your-repo
foil init
```

3. In the harness, in the repository, paste this line. Replace `<goal>` with the goal. This needs nothing installed in the harness.

```text
Read .foil/skills/operator.md and follow it. My goal: <goal>.
```

A persistent install is optional. Copy the skill where that harness discovers skills, at user level, so it stays out of `git status`. No path below was checked against that harness's current docs.

| Harness | Install path | Verified |
|---|---|---|
| any | the pointer line above | required; not a per-harness install |
| Claude Code | `~/.claude/skills/foil-operator/SKILL.md` | no |
| grok, codex, opencode, gemini | none published | no |

4. Before you spawn the lead, set `permission` on every template the fleet will use: `.foil/templates/lead.toml`, `.foil/templates/implementer.toml`, and `.foil/templates/reviewer.toml`. Each has its own permission. `permission = "ask"` is the default: that seat stops at its first approval prompt and waits in that pane. `auto` on the lead alone does not let the workers run unattended. Set `auto` on each template whose seat should run unattended. With `ask`, peek a new seat for an approval prompt.

5. The lead's first prompt carries the instructions: the role skill, persona, commands, and board conventions. It tells the lead to re-read its instruction file whenever it is woken. Put only the goal in `--task`.

6. Check that the lead is working. Spawn the lead once, with a task to write `board/status.md` containing `state: done`, then wait a few minutes. If the quick start already started the lead, skip this spawn.

```sh
foil seat spawn lead --task "Write board/status.md with state: done"
```

If `.foil/board/status.md` does not appear, `foil seat peek lead` shows a login prompt, an approval prompt, or an error. When the file shows `state: done`, send the human's goal to that lead. Do not spawn the lead again.

```sh
foil send lead "the goal"
```

## Limits

Foil is cooperative protection for seats that follow instructions. It is not isolation from a hostile process. A seat's identity is the `FOIL_SEAT_ID` environment variable Foil sets when it launches that seat. There is no flag a seat can pass to claim another seat. Anything you can do on this machine, a process running as you can do too.

Templates default to `permission = "ask"`, so the harness asks before it acts. Setting `permission = "auto"` inserts that preset's auto flags. For Claude, those flags are `--permission-mode` and `auto`, and the harness can edit files and run commands without asking. That seat is still you.

Foil never interprets a pane. `foil seat peek` prints the raw tmux capture. `foil seat list` reports `alive`, `dead`, or `killed` from whether the stored window still exists, not from what the text on the screen means. Agents say whether they are blocked or done in board files.

A nudge is typed even when the pane is not at a prompt. `foil send` writes the mail file first, then types one line into the recipient's window: the sender, a space, and the mail file's absolute path, then Enter. The message body is never typed. If the agent is not waiting for input, those keystrokes still go into the pane. Foil does not look at the pane to decide.

## Learn more

- [docs/demo.md](docs/demo.md) — a real run, from `foil init` to the merged fix
- [docs/architecture.md](docs/architecture.md) — components, data flow, and module boundaries
- [skills/operator.md](skills/operator.md), [skills/lead.md](skills/lead.md), and [skills/worker.md](skills/worker.md) — what each role runs
- [CONTRIBUTING.md](CONTRIBUTING.md) — setup and checks
- [CHANGELOG.md](CHANGELOG.md) — release history
