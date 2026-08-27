# Foil · 运筹

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0-informational)](https://github.com/TiantianFlow/foil)
[![CI](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml/badge.svg)](https://github.com/TiantianFlow/foil/actions/workflows/ci.yml)

[中文](README.zh-CN.md)

**Your agents' loyal opposition.** Foil coordinates a complementary
fleet of local CLI-agent seats from your shell. One seat implements.
Another challenges the work from a context the first seat does not
share. A lead you control staffs and directs them. Mail and status live
in durable files on disk; seats live in tmux. No hosted control plane,
no chat UI, no MCP, and no Foil login.

The opposition is structural, not theatrical. Foil seats are independent CLI processes,
not subagents sharing one parent model's context. They can
use different CLIs, models, personas, contexts, and worktrees, so a reviewer
can expose correlated blind spots that one agent family might reproduce.
That diversity does not guarantee correctness; it makes disagreement and
independent verification real rather than another voice in the same chat.

If you already run more than one local CLI agent, you know the failure
modes: every voice blended into one chat, context that evaporates when a
tmux session dies, no durable handoff between agents, and steady
pressure to adopt a hosted UI. Foil is the answer that stays in the
shell. Bring the Markdown personas you already trust — Foil does not
rewrite them.

## Why Foil

- **Complementary seats, not one blended voice.** Each seat is a
  distinct agent CLI process with its own role, context, and working
  directory.
- **Your roster, used as written.** Off-the-shelf catalogs such as
  [Agency Agents](https://github.com/msitarzewski/agency-agents) and its
  localized copies staff a seat as-is. No wrapper, no translation layer,
  no Foil-shaped rewrite.
- **Durable coordination.** Mailbox messages, acknowledgements, and seat
  status are versioned files on disk. A message stays `queued` until the
  recipient acknowledges it — whether or not anyone was watching.
- **Resumable after interruption.** Kill tmux, reboot, come back:
  `foil resume` revives seats, preferring live tmux, then the CLI's
  native session, then a logged fresh start.
- **Local-first and credential-free.** Seats run on your machine as the
  agent CLIs you have already authenticated locally. Foil never
  requests, stores, or prints provider credentials, and there is no
  Foil account.

## How it works

```mermaid
flowchart LR
  operator[You or a controller CLI] --> foil[Foil]
  foil --> lead[Lead seat]
  lead --> workers[Worker seats in tmux]
  foil --> files[Durable mailbox and status files]
```

A typical session: you point Foil at your repository and spawn a lead.
The lead staffs complementary specialists — an implementer to write the
code, a reviewer-challenger to attack the plan from a separate context.
The lead sends tasks as durable mailbox messages, asks for revisions
directly when output falls short, and integrates the evidence that
survives challenge. You inspect everything with ordinary CLI commands
and tmux. Foil is flexible coordination, not a fixed
research→implement→review pipeline.

## Quick Start

You need Python 3.11+, uv, tmux 3.2+, and Git. The example below also
uses locally authenticated `grok` and `opencode` CLIs; Foil never handles
those logins. If you would rather have a local agent do the setup, skip
to [Ask an agent](#ask-an-agent).

Install the current public tip (package version 0.1.0):

```sh
uv tool install "git+https://github.com/TiantianFlow/foil.git"
```

To upgrade later, reinstall from the same URL:

```sh
uv tool install --reinstall "git+https://github.com/TiantianFlow/foil.git"
```

Then switch to the repository you want Foil to coordinate. `foil init`
accepts an existing Git repository and preserves every tracked and
untracked file and all Git state; it refuses a non-empty directory that
is not a Git repository. (An empty directory works too — run
`git init -b foil-demo` after init to give it a Git identity.)

```sh
# then in your existing Git repository
cd your-repo
foil init .

# copy state_root and fleet_id from the JSON foil init printed
export STATE_ROOT=...
export FLEET_ID=...

foil seats set --seat lead --lead --cli grok --role manager
foil seats set --seat implementer --cli grok --role implementer
foil seats set --seat reviewer-challenger --cli opencode --role reviewer-challenger --permission auto
foil seat spawn --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat lead
foil seat spawn --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat implementer
foil seat spawn --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat reviewer-challenger
foil send-message --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat reviewer-challenger --sender lead --body "Please challenge the current plan." --wake
foil status --state-dir "$STATE_ROOT" --fleet "$FLEET_ID"
```

Do not paste the words `STATE_ROOT` or `FLEET_ID`. Persist the mapping
with `foil seats set` first; spawn then fills CLI, model, role, and
permission from `.foil/seats.toml`. `--cli`, `--model`, and the other
launch flags remain ad hoc overrides. The first seat must be the lead;
workers come after. `foil send-message` persists mail only — pass
`--wake`, or run `foil seat wake` after inspecting tmux, to nudge a
seat. `foil status` is the first thing to run when you want to see
whether those seats are actually alive.

Shipped presets are `grok` and `opencode`. Another interactive CLI
joins through a [declarative profile](#personas-roles-and-other-clis),
not by hoping the name works.

## Ask an agent

If a local CLI agent is already helping in this repository, you do not
need to paste the commands above. Tell it:

> Install Foil in this project and follow the agent setup instructions
> in the README.

That is the whole prompt. The playbook it should follow is the next
section.

## If you are an agent setting up Foil

You are staffing a local fleet, not installing a hosted product. Stay
in the shell. Do not invent a Foil login, MCP server, or browser
control plane.

1. Confirm Python 3.11+, uv, tmux 3.2+, and Git. Install Foil with
   `uv tool install "git+https://github.com/TiantianFlow/foil.git"`.
2. Work in the user's existing Git repository. Run `foil init .` and
   keep the printed `state_root` and `fleet_id` for every later flag.
3. Discover which agent CLIs are on `PATH` (`grok`, `opencode`, and any
   profiled CLI the user already runs). Ask when discovery is
   incomplete. `foil doctor` reports tmux, Git, and known CLIs; it does
   not read credentials. Ask which models those CLIs can launch and
   how much usage remains on each.
4. Offer a local Markdown roster. A good default is
   [Agency Agents](https://github.com/msitarzewski/agency-agents);
   localized copies of that catalog work the same way. Clone or copy it
   next to the project — for example `./personas` — and leave every
   persona file untouched. Foil reads the file as written.
5. Ask what kind of work the user actually does. Pick a small
   complementary suite from that answer: one lead, one implementer,
   one challenger at minimum. Do not staff eight seats because eight
   role files exist.
6. Map seats to locally available CLIs and models. Put heavy
   implementation on the CLI and model with enough remaining usage
   for the job. Put the challenger on a *different* CLI or model
   family when possible, so review is not the same voice restated.
   Persist that mapping with `foil seats set` (`--cli`, `--model`,
   `--role` or `--role-file`, `--permission`). Tell the user the
   mapping and wait if the tradeoff is unclear.
7. Spawn from the seat file: `foil seat spawn --state-dir … --fleet …
   --seat lead` first, then the workers. Do not pass `--cli` or
   `--model` unless you are overriding the file. Then `foil status`.
8. Hand the user back ordinary commands: `foil status`, `tmux`,
   `foil send-message --wake`, `foil resume`. You are done when a lead
   exists, complementary seats are mapped, and the user can inspect
   the fleet without you.

## Operating a fleet

**One worktree per fleet.** Keep a canonical checkout of your project
clean and fast-forwarded to the remote `main`. Create one dedicated
feature worktree per fleet, and run the lead and workers from that
worktree. Never let a fleet mutate the canonical checkout; if it is
dirty, fail and notify rather than altering it. An explicitly selected
alternative base is fine — this is an operating rule for you, not
something Foil enforces.

**Lead-owned membership.** Every fleet starts with one lead. The lead
(or you) spawns workers from the role library when they are needed.
Workers are isolated by default: Foil clones `worktrees/<seat_id>` from
the committed HEAD, so uncommitted files are not copied and later
commits do not refresh clones; `foil doctor` reports the lag.
`--shared-cwd` is the advanced override.

**Honest status.** `working` means the tmux process is alive — not that
a model is thinking. `foil status` reconciles the registry against live
tmux; `foil poll-status` reads only the versioned status files.

**Interruption and resume.** `foil resume` prefers a matching live tmux
window, then the seat's recorded native session, then a logged fresh
start. `foil seat stop` keeps the seat record so resume can continue;
`foil seat remove` deletes it. Permission comes from `.foil/seats.toml`
when set; otherwise spawn is `supervised`, so the CLI asks for
approvals. `--permission auto` on spawn or in the seat file uses
adapter-declared approval flags.

## Personas, roles, and other CLIs

`foil init` scaffolds a role library — manager, implementer,
reviewer-challenger, and more — under `.foil/roles/`. You can also staff
seats directly from a Markdown persona catalog; the persona file
is used untouched, with no wrapper. That includes
[Agency Agents](https://github.com/msitarzewski/agency-agents) and
localized copies of the same catalog. Foil does not own the roster and
does not require a Foil-specific rewrite:

```sh
git clone https://github.com/msitarzewski/agency-agents.git ./personas
foil catalog-list --path ./personas
foil catalog-map --path ./personas --persona NAME --json
foil seats set --seat designer --cli grok --role-file ./personas/path/to/designer.md
foil seat spawn --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat designer
```

`catalog-map` returns the persona `path`. It does not assign a CLI:
`cli` and `preset` stay null. Persist staffing with `foil seats set`.

Grok and OpenCode are shipped presets. Other interactive CLIs join
through a declarative seat profile — one TOML owning the executable,
launch/resume/startup argv, session capture, permission flags, working
directory behavior, and environment forwarding by variable name (values
are never persisted):

Save or copy the shipped
[`profiles/pi-interactive.toml`](profiles/pi-interactive.toml) example into
the repository you are coordinating, then pass its path per seat:

```sh
foil seats set --seat researcher --profile ./profiles/pi-interactive.toml --role researcher
foil seat spawn --state-dir "$STATE_ROOT" --fleet "$FLEET_ID" --seat researcher
```

`--profile` on spawn remains an ad hoc override of the seat file.

Presets and profiles are the only supported
paths — if a CLI is not a preset and you have not run it through a
profile yourself, do not assume it works. When you pass `--model`, Foil
forwards your choice to the CLI; the CLI's own screen is the source of
truth for which model actually launched.

## Security and limitations

- Foil is cooperative same-user protection, not hostile isolation. Seats
  run as you, with your CLI credentials, on your machine.
- Foil never stores provider credentials and never prints environment
  variables or terminal buffers.
- There is no MCP server, no hosted service, and nothing to log in to.

## Learn more

- [docs/walking-skeleton.md](docs/walking-skeleton.md) — the executable end-to-end verification path
- [docs/design/onboarding.md](docs/design/onboarding.md) — initialization and state-root contract
- [docs/design/seats.md](docs/design/seats.md) — predefined seat recipes in `.foil/seats.toml`
- [docs/design/profiles.md](docs/design/profiles.md) — the declarative seat profile contract
- [skills/controller](skills/controller), [skills/manager](skills/manager), [skills/worker](skills/worker) — portable skills for the CLIs that drive or join a fleet
