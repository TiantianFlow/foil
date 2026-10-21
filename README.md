# Foil · 运筹

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.0-informational)](https://github.com/TiantianFlow/foil)
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

Foil makes the split a structure. The lead plans, spawns workers by role, and merges their branches. The task file `board/tasks/<id>.md` is the brief, and the mail is the pointer to it. `foil seat spawn --task` writes that mail and does not write the task file. You talk to one operator agent, and the lead does the managing.

### A clean-context check that doesn't take "done" for an answer

An agent that has worked for an hour grades its own work with everything it believed along the way. It skips the step it forgot, trusts the test it wrote, and says "I'm done" when it isn't. Long sessions make it worse: the context fills up, quality drops, and compaction loses details.

In Foil, every seat starts clean and sees only its role and its task. The reviewer never saw the implementer's reasoning, only the result, so it checks the work instead of the story, and the lead asks for that check before it reports the goal done. Small, clean contexts also let the work run longer and stay correct: each seat does one job, and the plan, mail, and status live on disk, not in anyone's memory. That is the loyal opposition, and where Foil gets its name: a foil is the character whose contrast shows what the other one missed.

### Use the best model for each job, from any provider

Models differ. Some reason better, some write code faster, some cost less, and some still have usage left this week. In Foil, each role is a template with an ordered list of harness entries. Each entry is an id and an optional model, and a spawn takes the entry at the count of seat records already stored for that role. The lead can plan on a strong reasoning model, implementers can run on a fast one, and the reviewer can come from a different vendor, which adds a second kind of independence: different training, different blind spots. Each seat uses its own CLI's login, so the work spreads across subscriptions you already pay for. Foil never stores your keys or logins.

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
    board[("<b>project folder/board</b><br/>mail · room · status.md")]
    tree[("<b>Git worktree</b><br/>branch foil/implementer-1")]
  end

  you <-->|chat| operator
  operator -->|"foil init · seat spawn lead<br/>send · seat peek"| foil
  lead -->|"seat spawn · seat kill · send"| foil
  foil -->|"launch seats · send mail to one seat<br/>or post a room file that nudges every other alive seat"| fleet
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
- **foil**: this command-line tool. It launches seats in tmux. `foil send` writes one mail file and nudges that recipient. `foil post` writes one room file and nudges every other alive seat. The body is not typed. Both wait 200ms, then send Enter once. It runs no daemon of its own and never interprets what is on a seat's screen. `foil view` is an optional read-only page in your browser.
- **Lead, implementer, reviewer**: agent CLIs running headless in tmux windows, each started with its role's instructions. By default only the implementer gets its own Git worktree and branch.
- **Board**: mail, room threads, notes, and `status.md`, as plain files in the project folder `foil init` prints. That folder is outside the repository. Seats read mail with `foil mail` and other board files, including room threads, with `foil board`.

## Built to stay out of the way

- **Your own agent runs the fleet.** You don't drive a dashboard or wire agents together by hand. You tell the agent you already use what you want, in whatever interface you like it in, as long as it can run shell commands in your repository. Following one skill file, it starts the lead, checks in, and relays questions.
- **Each CLI works as it is.** Foil starts every agent with that CLI's own documented flags and talks to it the way you would: one short line typed into its window, and the message itself in a file. It never reads the screen to guess what an agent is doing, so a CLI update rarely breaks it, and adding a CLI takes a small preset file, not code.
- **Runs wherever tmux runs.** A command-line tool with no daemon of its own, plus an optional read-only page, on Linux or macOS, on your laptop or over SSH on a remote machine. Close your terminal and the fleet keeps working; reattach with tmux.
- **Nothing hidden.** Mail, tasks, status, and lessons are plain files, and the work lands on ordinary Git branches. You can read, grep, or edit any of it. They survive a dead tmux session or a reboot, and `foil seat resume` brings the seats back.

## Demo

[docs/demo.md](docs/demo.md) walks through a real run: a failing test, a lead, an implementer, and a reviewer, from `foil init` to the merged fix.

If Foil saves you time, a [star on GitHub](https://github.com/TiantianFlow/foil) helps others find it. That is all it asks for. Your agent may mention it after a finished goal a few times, then rarely, and stops when you say no.

## Get started

You need Python 3.11+, Git, tmux 3.2+, and at least one supported agent CLI (Claude Code, Codex, Gemini, OpenCode, or Grok), already logged in on this machine. Foil never handles that login. Trust is per harness per path. A seat worktree lives in the project folder, outside the repository, so it does not inherit the repository's trust. Each seat label needs folder trust once per harness. A seat waiting on one of those prompts looks, from outside, like a seat that is working. The operator answers an approval dialog: a folder-trust prompt, a login prompt, or a prompt that blocks the seat until you or the operator approve it. It reports an update dialog, a first-run dialog, and an opt-in, and leaves those for you. A global preset counts before `init`. Another CLI joins with a small preset file at `<root>/harnesses.toml` or `<root>/harnesses/<id>.toml`.

The human's part is one sentence, typed into the coding agent they already use, in the repository:

```text
Install Foil from https://github.com/TiantianFlow/foil, onboard this repository with it, and start a fleet. My goal: <goal>.
```

To update between goals, kill first, then upgrade, then `foil init`. From 0.4 to 0.5.4, do not resume a seat started on 0.4. `foil init` moves a project-root `board/` and `run/` under `fleets/main/` when that fleet's copies are absent or empty, and it does not read `templates/<role>.toml`. `init` writes each missing default role in `<root>/roles.toml` and one mapping row per missing role in `<root>/mapping.toml`. It writes no project `roles.toml`, `mapping.toml`, or `harnesses.toml`, and it deletes those paths. A new list uses `permission = "auto"` and omits `model`. Lessons in `memory/` and seat worktrees stay. `FOIL_FLEET` defaults to `main`. Run `foil seat kill --all` once the goal is done, run the install line again, run `foil init` in each project, then spawn the lead. A project that still has an in-repo `.foil/` from 0.3.x is killed with that old `foil` before the install line runs. A stale seat whose tmux window is already gone also counts. If that old `foil` is no longer on `PATH`, install 0.3.4 beside the current one, which does not replace it: `UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then run `DIR/bin/foil seat kill --all`. With pipx: `PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then run `DIR/bin/foil seat kill --all`. Do not run `foil init` while those seats remain, and do not move that directory aside. The previous goal's `board/status.md`, in the project folder the report prints, already says `state: done`, so note its `updated` line before you spawn and wait for it to change. After `foil seat kill --all`, either case can occur, so check: run `tmux ls`. If it lists any session, a named preset `env` variable comes from that server and is set with `tmux set-environment -g NAME VALUE` before spawn. If it says no server is running, the next spawn starts a server from your shell.

- The agent installs Foil and runs `foil init`. If that command prints `foil: no harness installed`, it wrote nothing. Install one of the CLIs above and run `foil init` again.
- It reads the report, runs `foil roster list`, and asks you to run each harness id once in this repository and accept its folder-trust, update, first-run, and opt-in prompts, or to confirm that is done. It sets each template's harness list and `permission` with `foil roster`. The shared lists are `<root>/harnesses.toml`, `<root>/roles.toml`, and `<root>/mapping.toml`. `init` writes no project `roles.toml`, `mapping.toml`, or `harnesses.toml`, and it deletes those paths. `init` writes one mapping entry per role and `permission = "auto"`. Every fleet shares those lists. `FOIL_FLEET` selects a fleet and defaults to `main`. `roster update ROLE harness=ID/MODEL,ID/MODEL` sets a role's whole list, and `model` edits a one-entry list. From inside the fleet, both write that fleet's `overrides.toml` instead, and the next spawn applies it. A spawn takes the entry at the count of seats already stored for that role. `auto` on the lead alone does not let the workers run unattended. Limits says what `auto` still does: the seat can stop on an approval prompt.
- It runs the first-run check and sends the goal.
- It never does the project work itself.

An agent that was started without that sentence uses the pointer `init` prints. When `<root>/skills/operator.md` exists, that line is:

```text
Read <root>/skills/operator.md and follow it. My goal: <goal>.
```

Otherwise the pointer is the packaged `operator.md`.

A persistent install of the operator skill is optional. Copy it where that harness discovers skills, at user level, so it stays out of `git status`. No path below was checked against that harness's current docs.

| Harness | Install path | Verified |
|---|---|---|
| any | the pointer line above | required; not a per-harness install |
| Claude Code | `~/.claude/skills/foil-operator/SKILL.md` | no |
| grok, codex, opencode, gemini | none published | no |

### By hand

The same steps, typed yourself. Install Foil, then in the repository:

```sh
uv tool install --upgrade foil-ai
cd your-repo
foil init
```

If the install says `Executable already exists: foil`, an install from before the package was renamed to `foil-ai` is in the way. Run `uv tool uninstall foil-orchestrator`, then the install line again.

Read the report and set the three templates with `foil roster`. If `board/status.md` is already in the project folder, note its `updated` value. Then spawn the lead once:

```sh
foil seat spawn lead --task "Write board/status.md with state: done"
```

When `board/status.md` in the project folder shows `state: done`, send the goal to that lead. If that file was already there, this passes only after `updated` changes. Do not spawn the lead again. The lead's first prompt already carries its instructions, so a message holds only the goal or an answer.

```sh
foil send lead "the goal"
foil seat list
foil seat peek lead
```

`board/status.md` in the project folder is the lead's own report, including any questions for you; answer them with `foil send lead "..."`. It carries a checklist of what is done and what is left, and the operator reports it at each check-in and whenever the human asks. When you are done, `foil seat kill --all` stops every seat and leaves branches and worktrees in place. A killed seat's name stays reserved until `foil seat archive NAME`, which frees the name and does not delete the worktree, the branch, the mailbox, or uncommitted work. `foil seat list --archived` shows the stored worktree. A leftover seat worktree is `<project folder>/worktrees/<label>`. Remove one that is clean with `git worktree remove <path>`. A dirty tree needs `--force`, and that discards uncommitted work.

### If nothing happens

Run `foil seat peek lead`, and do the same for a seat that was just spawned or resumed. A seat that makes no progress is usually stopped on a dialog. The operator answers an approval dialog: a folder-trust prompt, a login prompt, or a prompt that blocks the seat until someone approves it. It reports an update dialog, a first-run dialog, and an opt-in, and leaves those for you. Answer those in the window (`tmux ls` lists Foil's session and `tmux attach` opens it). A login the CLI will not finish in the pane is done once by hand, then `foil seat kill lead` and spawn the lead again. If `foil seat list` shows that lead as `dead`, `foil seat kill lead` clears it. Spawn the check again only after it is gone.

## See it in a browser

`foil view`, run from any directory, serves one read-only page for every Foil project on this machine and prints its address. The page lists the projects. Each project lists its fleets and lessons, and its Settings shows the three shared lists. Each fleet shows its seats grouped by state (dead first, killed folded away), its `status.md`, its posts and board files with their modified times, its overrides, and its attach command. A board file opens on its own page, and each seat shows its raw pane. It types nothing into a pane and decides nothing about progress; that is still `board/status.md`. Stopping it leaves every seat as it was.

```sh
foil view
foil view --port 8790
```

Without `--port` it takes the first free port from 7460 to 7509. It listens on `127.0.0.1` only. To reach it from another machine, forward the port over SSH, for example `ssh -L 8790:127.0.0.1:8790 host`.

To open it from your other devices on Tailscale, put `tailscale serve` in front of it, for example `tailscale serve --bg 8790`, and open the `https://` address that command prints. Nothing listens on your LAN or tailnet address; Tailscale forwards each request to `127.0.0.1`. `foil view` checks Tailscale when it starts, so start or restart it after Tailscale is up, and after this machine signs in to another account or is tagged. Under the tailnet name, it serves only requests that Tailscale marks as coming from the login that owns this machine. Funnel traffic, tagged devices, and devices of other users get an empty 403. Use the HTTPS form above, or `--http`. `tailscale serve --tcp`, `--tls-terminated-tcp`, and `tailscale funnel --tcp` forward raw bytes without the headers this check reads, so anyone they let in can read the page. `tailscale serve reset` stops sharing it.

### Keep the viewer running

Foil has no daemon mode. launchd on macOS and `systemd --user` on Linux run `foil view` as it is, start it at login, and restart it when it fails. Your operator agent can fill these in for you. One service covers every project on the machine. Give it a port outside 7460-7509, so an ad hoc `foil view` never takes it. A service keeps the page readable by every account on the machine for as long as you are logged in (see Limits), so set one up only on a machine you do not share. Replace each capitalized placeholder:

- `FOIL`: the absolute path that `command -v foil` prints.
- `PORT`: the port.
- `YOUR_PATH`: what `echo $PATH` prints in your own terminal. A service starts with a minimal `PATH`, and without yours it cannot find tmux or your agent CLIs.
- `HOME_DIR`: your home directory's absolute path.

If you set `FOIL_HOME`, `XDG_DATA_HOME`, or `TMUX_TMPDIR`, add the same value next to `PATH`; without the same `TMUX_TMPDIR`, the viewer cannot find your seats' windows and shows every seat dead. Restart the service after you upgrade Foil.

macOS, `~/Library/LaunchAgents/dev.foil.view.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>dev.foil.view</string>
  <key>ProgramArguments</key><array>
    <string>FOIL</string><string>view</string><string>--port</string><string>PORT</string>
  </array>
  <key>WorkingDirectory</key><string>HOME_DIR</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>YOUR_PATH</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>HOME_DIR/Library/Logs/foil-view.log</string>
  <key>StandardErrorPath</key><string>HOME_DIR/Library/Logs/foil-view.log</string>
</dict></plist>
```

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.foil.view.plist
launchctl bootout gui/$(id -u)/dev.foil.view
```

The first line starts it and the second stops it. After you edit the file, stop it before you start it again. It restarts after a failure, at most once every 30 seconds, and not after a clean stop.

Linux, `~/.config/systemd/user/foil-view.service`. Write each `%` in `YOUR_PATH` as `%%`:

```ini
[Unit]
Description=Foil viewer

[Service]
ExecStart="FOIL" view --port PORT
WorkingDirectory=HOME_DIR
Environment="PATH=YOUR_PATH"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

```sh
systemctl --user enable --now foil-view
systemctl --user disable --now foil-view
journalctl --user -u foil-view
```

It runs while you are logged in.

## Limits

Foil is cooperative protection for seats that follow instructions. It is not isolation from a hostile process. A seat's identity is the `FOIL_SEAT_ID` environment variable Foil sets when it launches that seat. There is no flag a seat can pass to claim another seat. Anything you can do on this machine, a process running as you can do too.

Templates default to `permission = "auto"`. An omitted permission runs the seat unattended. Setting `permission = "ask"` is how a template opts back into prompts: that seat stops at its first approval prompt. Setting `permission = "auto"` inserts that preset's auto flags. For Claude, those flags are `--permission-mode` and `auto`. Claude Code's auto mode can still ask before some commands, so an `auto` seat may stop on an approval prompt. Peek new seats. That seat is still you.

Foil never interprets what is on a seat's screen. `foil seat peek` prints the raw tmux capture, and `foil seat list` reports `alive`, `dead`, or `killed` from whether the seat's window still exists, not from what is on its screen. Agents say whether they are blocked or done in board files. Foil does not scrape a pane, does not decide that a dialog is present, and does not send the keys.

`foil view` has no login. Your project folders are private to you, but a port on `127.0.0.1` is open to every account on the machine, so while the viewer runs anyone logged in can read it, including every project's pane text and board files. It hides launch-command tokens that look like credentials, as a best effort. On a machine other people log in to, stop it when you are done. The tailnet check guards only the tailnet name: an account on this machine can still read the page on `127.0.0.1`.

A nudge is typed even when the window is not at a prompt. `foil send` writes the mail file first, then types one line into the recipient's window: the sender, a space, and the mail file's absolute path. `foil post` writes one room file and types that same line into every other alive seat. Either command waits 200ms, then sends Enter once, so a harness that treats Enter inside a fast burst as an unsent paste submits the line instead of leaving it in the input. The message body is never typed. `foil post --re PATH --decline` writes a room file and types nothing. If the agent is not waiting for input, those keystrokes still go into the window. Foil does not look at the screen to decide, and it does not send Enter again.

## Learn more

- [docs/demo.md](docs/demo.md) — a real run, from `foil init` to the merged fix
- [docs/architecture.md](docs/architecture.md) — components, data flow, and module boundaries
- [skills/operator.md](skills/operator.md), [skills/lead.md](skills/lead.md), and [skills/worker.md](skills/worker.md) — what each role runs
- [CONTRIBUTING.md](CONTRIBUTING.md) — setup and checks
- [CHANGELOG.md](CHANGELOG.md) — release history
