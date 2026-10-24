# Changelog

Notable changes to Foil, newest first.

## [Unreleased]

### Added

- The operator skill says when check-ins stop: the goal is done and reported, the fleet is stuck after one nudge, or a question waits on the human. The report that stops says so and names the condition, and a timer that drives the operator pauses on the same conditions. The interval is the operator's choice from the work, usually 5 to 20 minutes, a minute for a step expected to be that fast, and up to 30 minutes early in a long goal. (#83)
- The lead can answer the human with `foil send user`. That mail goes to `board/mail/user/` and nudges nothing; outside the fleet, `foil mail list` lists it. The operator skill reads it at each check-in. (#69)
- Each seat's git runs Foil's `pre-commit` and `pre-push` hooks, set through `GIT_CONFIG_*` in the seat's environment only, before the repository's own hooks. In the project's repository they refuse a commit outside the seat's worktree, and a commit or a push that adds the seat's home directory path, the hostname, a non-noreply git email outside a trailer, or a token. A repository's `reference-transaction` and `fsmonitor-watchman` hooks no longer run for seats. (#69)
- Spawn and `roster add`/`update` print a warning when a model is not in that harness's `models`, and still launch or write it. (#69)
- The skills describe a handoff for a seat near a full context: it writes `board/handoffs/<name>.md`, and the lead (or, for the lead, the operator) kills it and spawns a new seat from that file. (#69)

### Changed

- The lead treats a method, a cause, or a fix that arrives with the goal as a proposal a seat checks, and asks the human when the evidence contradicts it. It checks each result against its acceptance before accepting it, and a seat proposes the structure of the work when that is a choice. The operator relays the goal in the human's words and adds no method of its own.
- The operator clears a seat's folder-trust prompt itself, so the human no longer pre-trusts each path and a fleet can start unattended. It does so only when the seat is in `foil seat list`, the window and worktree come from the seat's registry record, the launch directory comes from its plan, the canonical path the prompt shows is that seat's own checkout, and the choice is unambiguous; then it peeks again. Every other dialog, now including a login prompt and a command or tool approval, is reported to the human and not answered. (#84)
- A `vX.Y.Z` tag publishes only after the CI workflow has succeeded for that commit. A run still going, a failed or cancelled run, and a commit with no CI run do not publish.
- The packaged personas are rewritten and much longer. Each now says who the seat is, what it owns and does not own, its rules, how it works, the shape of what it hands back, how it sounds, and its traps. The lead persona is now an overseer: it frames each decision, asks seats for independent proposals, weighs their evidence, settles conflicts with a task rather than a vote, and records what it chose and why. The persona descriptions `seat list` and `roster list` show are unchanged, except the lead's. The lead skill says the same: when seats' results disagree, the lead chooses by their evidence or writes the task that settles the point. The operator skill no longer names an outside persona catalog. (#80)
- The demos show the current `foil init` pointer and mail under `fleets/main/board/`. `docs/architecture.md` no longer names a version.
- The notes for issues #18, #42, and #66 are removed. The decisions are in `docs/requirements.md`.
- A room reply (`foil post --re`) wakes only the seats that wrote a waking post in that thread and the seats its text names as `@name`. A new post still wakes every other alive seat. (#69)
- Rotation over a role's pair list now counts that role's spawns in the fleet, stored in the registry's new `rotation` record, instead of its stored seat records. Every lead respawn takes the next entry, and an edited list starts again at its first entry. A fleet started before this change starts each role at its first entry. (#69)

## [0.6.1] - 2026-10-21

`foil --version` prints 0.6.1. This is the first release on PyPI: install with `uv tool install --upgrade foil-ai`.

### Added

- A release workflow publishes each `vX.Y.Z` tag to PyPI as `foil-ai`, using trusted publishing. CONTRIBUTING.md has the release steps. (#70)

## [0.6.0] - 2026-10-21

`foil --version` prints 0.6.0.

### Breaking

- **`foil init` deletes your old project settings files without reading them.** On every `foil init` that completes, including a same-checkout re-run, these project paths are deleted without being read and with no backup: `roles.toml`, `mapping.toml`, `harnesses.toml`, `harnesses/`, `templates/`, `skills/operator.md`, `skills/lead.md`, and `skills/worker.md`. A symlink is unlinked and not followed. An init that exits non-zero deletes nothing. Re-create a customized roster with an unflagged `foil roster` before `init`; that write is the one that survives. Re-apply any `--project` override after every `init`. Move an edited persona out of `templates/personas/` to `<root>/personas/<role>.md`, or to `<project folder>/personas/<role>.md` for one project, before `init`. `foil roster` does not re-create a persona.
- Inside a fleet, the lead can set `harness` and `model` only. Those write that fleet's `overrides.toml` and leave the global and project mapping in place. `roster add`, `add --from`, `remove`, `persona=`, and `worktree=` are refused with `foil: roles are outside the fleet only`. `permission=` is refused with `foil: permission is outside the fleet only`. `--project` is refused with `foil: --project is outside the fleet only`. A caller outside the fleet still runs them. (#66)
- The packaged harness model lists changed, and they are read live from the package. A project copy no longer freezes them, and `foil init` does not write one. Claude is now `sonnet`, `opus`, `haiku`, and `fable` (no longer `claude-sonnet-4-5` or `claude-opus-4-5`). Codex is now `gpt-6.1-sol`, `gpt-6-luna`, and `gpt-6-astra` (no longer `gpt-5.5`). Gemini adds `gemini-3-pro-preview` and `gemini-3-flash-preview` beside `gemini-2.5-pro` and `gemini-2.5-flash`. Grok is now `grok-4.7` (no longer `grok-4` or `grok-3`). OpenCode stays `anthropic/claude-sonnet-4-5` and `openai/gpt-5.5`: both are current `provider_id/model_id` entries on models.dev, which is where OpenCode's docs say its built-in names come from. Spawn still does not reject a model that is absent from a list.
- `foil init` no longer copies personas or the operator skill into the project folder. The report drops `Updated skills:` and `Skills: current`. It says `Wrote personas: none`. `Left personas:` listed only personas already in the project; it now lists every packaged persona. It prints a `Removed:` line for each project path it deleted, and says `Skill: <root>/skills/operator.md` when that file exists, otherwise `Skill: packaged <package path>`. The pointer line 0.5.4 told you to paste, `Read <project folder>/skills/operator.md and follow it. My goal: <goal>.`, names a file this `init` deletes. Paste `Read <root>/skills/operator.md and follow it. My goal: <goal>.` when that file exists, or the packaged path the report prints.
- Unflagged `foil roster add`, `update`, and `remove` now write the global layer instead of the project file, and they work before `init`. The project layer needs `--project`, which is new.

### Changed

- `foil view` titles the global page Settings, where the project page stays Project settings. A role's rotation is a row of unnumbered pair cards. A fleet override links each role to the settings page of the layer that role comes from, and the header Settings tab opens the project settings page inside a project and the global settings page outside one.
- Global settings are the settings. A project file is an optional override and a fleet file is an optional pair list. Lookup is packaged, then global, then project, then fleet. `foil init` writes missing global default roles. `foil view` serves `/settings` for that layer. `seat spawn` refuses a model-only override row when the pair list it replaces has more than one entry. (#66)

## [0.5.4] - 2026-10-21

`foil --version` prints 0.5.4.

### Changed

- `foil view` was reworked from a UX audit (#63). A board or post file opens on its own page, at the top and full width, with a link back and links to the files before and after it, instead of below the lists in half the width. A path not in the listing is now a 404. The fleet page lists seats first, grouped as Needs attention (dead), Running (alive), and Killed (folded), with links to filter by state, then `status.md` inline, then posts by thread and board files by folder, each with its modified time, then overrides and the attach command. The default mapping and a fleet's overrides show each role as one numbered list of harness and model pairs, with the entry the next spawn takes marked on the fleet page. Settings is titled Project settings. Column headers have info explanations, the header is one line of breadcrumbs, a seat page links to the seats before and after it, and the seat list fits a phone screen. Dead seats have their own colour.

## 0.5.3 - 2026-10-20

This release was withdrawn. Its changes ship in 0.5.4.

### Added

- `foil roster update ROLE harness=ID/MODEL,ID/MODEL` sets a role's whole harness list in one value, in the form `roster list` prints, so a role with more than one entry no longer needs `mapping.toml` edited by hand. A single id with no `/` on a one-entry role still keeps its model. `model` on a longer list is refused and names this form. (#55)
- A fleet's `overrides.toml` rows can name a `harness` as well as a `model`, and a role's rows rotate in spawn order like the mapping's. Inside the fleet, `roster update ROLE harness=...` writes them, so one fleet can move a role to another harness or to a list of pairs without changing the default mapping. (#55)
- `foil view` can be opened over Tailscale through `tailscale serve`. It still listens on `127.0.0.1` only. Under this machine's tailnet name it serves only requests Tailscale marks as from the login that owns the machine; Funnel traffic, tagged devices, and other users get an empty 403. (#48)

### Fixed

- `foil init` stores the origin remote without HTTP userinfo, and adopt matches that form. A token in the remote URL, or one git inserts through `insteadOf`, is not written into `project.json`.
- In `foil view`, an open info explanation closes on a click anywhere else or on Escape, not only on its own icon. (#54)
- The Installed column in `foil view` settings shows where it found each command, and its explanation says that it checks the viewer's own `PATH`, which a seat started from another shell may not share. (#54)

### Changed

- `foil view` serves one page for every project on the machine, so one viewer (and one launchd or systemd service) is enough for a host. It runs from any directory. The page opens on the project list; a project's pages moved under `/projects/<key>/`. A project whose recorded checkout is gone or moved is listed without a link. (#55)
- Inside the fleet, `roster update ROLE harness=...` now writes that fleet's `overrides.toml` instead of the shared `mapping.toml`, as `model` already did, and every row Foil writes there names the harness, so a later change to that role's default harness does not reach that fleet. `model` on a role with more than one entry is refused instead of putting every new seat of that role, on every harness in its list, on the same model. (#55)
- An `overrides.toml` with several model-only rows for one role now rotates through them instead of using only the last. Rows Foil writes are unaffected; this changes only a hand-edited file. (#55)
- `tests/test_view_browser.py` runs the `foil view` page in headless Chromium through Playwright, a new dev dependency, and checks the info pop-ups and the Copy button. CI installs Chromium for it. (#54)
- CI runs the unit and end-to-end tests as separate jobs at the same time instead of one after the other. Lint, build, and the wheel smoke test run in the shorter end-to-end job. The `Python 3.11` and `Python 3.12` checks now pass only when every job passed. (#54)
- `foil view` refuses a request with a loopback `Host` that carries `X-Forwarded-For`, `X-Forwarded-Host`, `Tailscale-User-Login`, or `Tailscale-Funnel-Request`, so a reverse proxy in front of it on this machine now gets an empty 403. (#48)
- `foil view` gives no raw view of a board file over 16 MiB. (#48)
- A `foil view` page build gives tmux at most 10 seconds in total, so a hung tmux call no longer holds one of the 8 build slots past the request. (#48)
- The Agents info text says that `foil seat archive` frees a killed seat's name and that an archived seat is not listed. docs/requirements.md now lists every info text, and a test checks that the page serves exactly those. (#48)
- The test suite runs in parallel by default with pytest-xdist, one worker per CPU. On a 4-CPU machine the full run drops from about 4.5 minutes to about 1.2 minutes. `-n 0` runs it in one process. (#52)

## 0.5.2 - 2026-10-19

This release was withdrawn. Its changes ship in 0.5.4.

### Added

- `foil view [--port N]` serves a read-only page for the project on `127.0.0.1`: the fleets and lessons, the three shared lists, and for each fleet its attach command, overrides, seats, board, and posts, with each seat's raw pane. It writes nothing and types nothing. Without `--port` it takes the first free port from 7460 to 7509. It refuses a non-loopback `Host`, a foreign `Origin`, and any method but GET and HEAD. A board file shows a 1 MiB preview and a raw view of the whole file, and no file is opened through a symlink. It has no login, so while it runs any local account can read it. The README shows how to keep it running under launchd or `systemd --user`. (#42)

### Fixed

- The first `foil seat spawn` in a new fleet (`FOIL_FLEET` set to a fleet with no registry yet) stored a different fleet id than the one it launched the window with, so that seat always read as `dead`. Spawn now creates the fleet's registry first.
- `foil seat peek` reports `foil: could not peek seat` when tmux fails during the window check, instead of `foil: unexpected error`.
- The operator skill tells the human `tmux attach -t '=<session>'`, so a gone `main` session does not attach to another fleet whose name begins with it.

## 0.5.1 - 2026-10-19

This release was withdrawn. Its changes ship in 0.5.4.

### Added

- The `foil init` report can end, just before the pointer line, with one line for the human that invites a GitHub star. It shows on the first `init` in a store, then at least 3 days later, then at least 14 days after that, and afterwards at most once per newer minor version, 30 days or more after the last one. `<root>/star.json` records those days per store, not per project, and the line does not name it; `"stop": true` silences it for good. The record is written after the rest of the report has been flushed, just before the line, and the line prints only when that write succeeded, so a store Foil cannot write never repeats it and an `init` that fails writes no record; a failed write does not fail `init`, and it leaves the store root's mode unchanged. Foil never stars, opens a browser, or reads a GitHub login. The package was already over the 2,500-line target; this adds about 70 lines to `init`, the one report every operator session reads. (#46)

### Changed

- The operator skill relays that line once, after the goal reaches `state: done`, and sets `"stop": true` when the human declines. The README invites a star in one line. Package metadata names the supported agent CLIs.

## 0.5.0 - 2026-10-18

This release was withdrawn. Its changes ship in 0.5.4.

### Breaking

- The roster is three shared lists in the project folder: `harnesses.toml`, `roles.toml`, and `mapping.toml`. Permission defaults to `auto`. `templates/<role>.toml` is not read. Several fleets can run on one repository and one checkout. `FOIL_FLEET` selects a fleet and defaults to `main`. Each fleet has its own board, posts, registry, and `overrides.toml` under `fleets/<id>/`. (#43)
- From inside the fleet, `foil roster update ROLE model=VALUE` writes that fleet's `overrides.toml` and leaves `mapping.toml` unchanged. The next spawn applies that model as the harness model flag. Resume still uses the model recorded on the seat. (#43)

### Upgrade from 0.4

Do this between goals. Do not resume a seat that was started on 0.4.

1. With 0.4 still on `PATH`, run `foil seat kill --all` in the project. A stale seat whose tmux window is already gone also counts.
2. Upgrade Foil. `foil --version` prints `foil 0.5.4`.
3. Run `foil init` in the project. It moves a project-root `board/` and `run/` under `fleets/main/` when that fleet's copies are absent or empty, and it rewrites those absolute prefixes inside that fleet's `run/`. It writes the three lists when they are missing. It does not read `templates/<role>.toml`.
4. Copy each role's harness, model, worktree, and permission from the old template file into `roles.toml` and `mapping.toml`. A list `init` just wrote uses `permission = "auto"` and omits `model`. Set `permission` from outside the fleet.
5. Lessons in `memory/` and seat worktrees stay. Spawn the lead again. `FOIL_FLEET` defaults to `main`, which is the lifted fleet.

### Fixed

- The init report lists a persona as available only when `roles.toml` has no row for it.
- `foil post` writes the current fleet's room. Another fleet's room stays unchanged.

## [0.4.5] - 2026-10-18

`foil --version` prints 0.4.5.

### Added

- `foil seat archive NAME` frees a killed worker's name so it can be spawned again. The old record is a prose file under `run/archive/`, and the registry schema stays 1. The worktree, branch, mailbox, plan, and instruction file stay. A dirty tree is left in place on purpose. `foil seat list` omits the archived seat. `foil seat list --archived` prints the stored worktree path and branch. `seat archive` holds the registry lock across its tmux probe. Spawn, kill, and resume re-read the registry under that lock before they write, so they do not put an archived seat back. A `display-message` failure proceeds rather than refuses. A first launch says older mail may be from an earlier goal when the seat is the lead or when that name already has an archive file. A normal worker's first launch does not, and a resume does not rewrite the sentence. (#32)

### Changed

- The command surface is eight top-level commands and twenty-two actions.

## 0.4.4 - 2026-10-18

This release was withdrawn. Its changes ship in 0.4.5.

### Added

- `foil post` writes one `room/v1` file under `board/room/<id>/` and types one nudge into every other alive seat. A seat whose stored state is empty still receives that nudge when the window check passes. An outside caller reaches every alive seat. `--re` writes a child in that thread and wakes the same set. `--decline` writes a child whose body is `decline`, stores an empty `nudged`, and types nothing. The package was already over the 2,500-line target; this is a thin wrapper beside mail. (#33)

### Changed

- The command surface is eight top-level commands and twenty-one actions.
- A nudge path under `board/room/` is read with `foil board read`. A launch whose prompt is the instruction file tells the seat to list `board/room` and read threads it has not answered. `foil post` types the same nudge as `foil send`, including the 200ms wait before Enter.
- The operator, lead, and worker skills say when to `foil send` and when to `foil post`. The task file is the brief. The result file is the report. A note wakes nobody. `foil post --re ""` is one stderr line, writes nothing, and types nothing.
- Onboarding names the dialog the operator may answer. An approval dialog is a folder-trust prompt, a login prompt, or a prompt that blocks the seat until someone approves it. An update dialog, a first-run dialog, and an opt-in are reported to the human and not answered. A previous `board/status.md` that already says `state: done` passes the first-run check only when `updated` changes. The first `foil init` with no eligible harness prints `foil: no harness installed` and writes nothing; a preset can be added only after the project folder exists. A dead first-run lead is cleared with `foil seat kill lead` before the check is spawned again. The sample `foil init` banner in the demos is labeled as an illustration from an older run. (#24)

## 0.4.3 - 2026-10-17

This release was withdrawn. Its changes ship in 0.4.5.

### Added

- A role template's harness is one ordered list of entries (`[[harness]]`, with `id` and an optional `model`). Spawn takes `entries[n % len(entries)]`, where `n` is the number of seat records already stored for that role, including killed seats, counted before the new seat is inserted. A failed launch writes no seat record, so a retry selects the same entry. One entry stays on that entry. The lead reuses one registry record named `lead`, so that count is 0 before the record exists and 1 before every later launch.

### Changed

- Resume and restart launch with the harness and model recorded on the seat. They do not reload the template list over that pair.
- `roster update` of `harness` or `model` edits a one-entry list and writes `[[harness]]`. On a longer list those two updates are refused and the file is left unchanged. Editing the list while a seat is alive is allowed. `roster remove` still fails while a seat of that role is not killed. Seat records stay, including killed ones.
- `roster list` and `roster show` report the harness list. JSON `harness` is the entries and JSON `preset` is one source per entry.

## 0.4.2 - 2026-10-16

This release was withdrawn. Its changes ship in 0.4.5.

### Fixed

- `foil send` waits 200ms after the nudge line before it sends Enter once. A harness that treats Enter inside a fast burst as an unsent paste was leaving that line in the input, both while the agent was working and while it was waiting. The message body is still never typed. Foil still does not read the pane or send Enter again. (#35)

## [0.4.1] - 2026-10-14

`foil --version` prints 0.4.1.

### Fixed

- Upgrading a 0.3.x project is kill, then upgrade, then `foil init`. Kill the fleet with the old `foil` (`foil seat kill --all`) before replacing the command on `PATH`; a stale seat whose tmux window is already gone also counts. A refused `foil init` creates nothing in the store. When seats remain, its message names them and that order, and says a stale seat also counts without claiming that one exists. When none remain, it says to run `foil init`. When the old `foil` is no longer on `PATH`, the message gives a version-pinned install into a separate tool directory (`UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`, or with pipx `PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`), which does not replace the `foil` on `PATH`. It never says to move the directory aside. A move into a store that holds only a fresh skeleton succeeds. A collision names the stored entry that is already taken. A directory that is not a simple tree says that `board`, `run`, and `templates` must be real directories. A repository with no origin is adopted when one store record has a recorded path that is gone and a matching repo identity, such as the root commit. Otherwise init warns, names the orphan's path, and does not silently create a second empty project. A record is considered only when it could be this repository, and the warning does not stop init. After adopt or a legacy move, and only after `git worktree repair` succeeds, absolute paths in `run/registry.json`, `run/plans/`, and `run/instructions/` are rewritten to the store paths no matter what the old absolute prefix was, including the old checkout and the old `.foil/` directory, and a stale `FOIL_PROJECT` value in `run/plans/*.json` is rewritten to the current project key. That write is atomic. A failed repair leaves the stored paths unchanged. A later init finishes the full rewrite: the old store root or the old `.foil/` is mapped whenever that directory itself no longer exists, and the checkout recorded before the write is what detects a checkout that is gone. When the gitdir no longer names the old store, that folder is the project key of the recorded checkout. A 0.4.x command that finds an in-repo legacy `.foil/` prints that upgrade order instead of `foil: not initialized`. (#29)

## [0.4.0] - 2026-10-10

`foil --version` prints 0.4.0.

### Breaking

- Project state no longer lives in the repository. `foil init` creates `<store>/projects/<key>/` and writes nothing into the working tree, including no `info/exclude` entry. The store root is `$FOIL_HOME` when that is set, otherwise `$XDG_DATA_HOME/foil`, otherwise `~/.local/share/foil`. `<key>` is a readable slug of the realpath of the Git common dir plus a short hash, so the main checkout, a linked worktree, and a seat worktree share one folder. `project.json` records the repository path, the origin remote when there is one, and the creation time. Seat worktrees are `<store>/projects/<key>/worktrees/<label>`, and a seat's environment includes `FOIL_PROJECT`. (#23)
- `foil init` adopts one stored project whose origin remote matches and whose recorded path is gone, then runs `git worktree repair`. An in-repo `.foil/` with no live seat is moved into the store when that move is simple, which also requires `board`, `run`, and `templates` to be real directories. Otherwise init prints one instruction line and leaves the old directory in place. Both layouts are not supported together. (#23)
- A seat worktree no longer inherits the repository's folder trust. Each seat label needs that trust once per harness. `foil seat kill` still does not delete a branch or uncommitted work. A clean leftover worktree is removed with `git worktree remove`. (#23)

## [0.3.4] - 2026-10-07

`foil --version` prints 0.3.4.

### Changed

- An omitted permission, and `foil roster add` without `--from`, now default to `permission = "auto"`. `ask` remains the explicit choice, set from outside the fleet. The operator may send keystrokes only for an approval dialog (folder trust, login, or a prompt that blocks the seat until approved). Foil never interprets pane contents; it does not scrape a pane, decide a dialog is present, or send keys. (#25)
- In-fleet `foil roster add --from` refuses a file that asks for `ask`. (#25)

## [0.3.3] - 2026-10-02

`foil --version` prints 0.3.3.

### Changed

- `foil init` writes only the operator skill. The lead and worker skills are read from the package and inlined into the instruction file. An older `.foil/skills/lead.md` or `.foil/skills/worker.md` is left in place. (#18)

### Fixed

- `foil seat kill` on a dead seat whose stored window id now names another window no longer raises, and no longer stops `foil seat kill --all`. The seat is marked killed and the other window is left alone.
- A harness that exits at launch leaves a peekable pane. The seat window stays after the process exits, `#{pane_dead}` makes that seat dead, and `foil seat peek` prints the pane when it still carries this seat's markers. `foil seat kill` and `foil seat resume` remove that leftover window first.

## 0.3.2 - 2026-10-02

This release was withdrawn. Its changes ship in 0.3.3.

This release is documentation only. It publishes the survey of which
project files could move global (issue #18) and the architecture note on
worktree layout and where project state lives
([docs/architecture.md](docs/architecture.md), issue #19).
The two were developed on separate lines.
No product-code behavior changes.

### Added

- Documents the worktree layout (flat, one level, under `.foil/worktrees`; the lead uses the project checkout), where Foil's project state and the board live, and how harness session storage is global and keyed by working directory. (#19)

## 0.3.1 - 2026-10-01

This release was withdrawn. Its changes ship in 0.3.3.

[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.3.1.md](docs/plan-v0.3.1.md) is the plan for this release.
This release shows the roster and the live fleet in the lists the operator
already runs and stops spawn typing into a new pane (issue #15), refreshes
the packaged skills on `foil init` and documents upgrading between goals
(issue #14), and gives each packaged persona its own judgment (issue #17).
The three were developed on separate lines.

### Added

- `foil roster list` and `foil roster show` print a role description and a preset source (`builtin`, `user`, or `invalid`). `foil seat list` appends the harness and model recorded at launch, and the template's current description. A registry written before `model` existed still loads. (#15)

### Changed

- `roster list` JSON `personas` is a list of `{name, description}` objects. The 0.3.0 shape was a list of strings. (#15)
- `foil seat spawn --task` writes the task as mail before launch and types nothing into the new pane. The first launch prompt names that mail file and tells the seat to read it first, so a lead spawned for a new goal does not take mail kept from an earlier goal as its task. Without `--task`, it tells the seat to run `foil mail list` and read its mail before it acts. (#15)
- Trust is once per harness per repository. A seat worktree inherits it. The operator asks once per harness id, including update dialogs, and again when a role moves to a new id. (#15)
- `foil init` prints its version first, replaces the three packaged skills when they differ from this version, and reports a built-in preset that a file in `.foil/harnesses` overrides. An instruction file names the Foil version that wrote it. Onboarding covers install-or-update and upgrading between goals. After `foil seat kill --all`, either case can occur, so check with `tmux ls`. If it lists any session, a named preset `env` variable comes from that server and is set with `tmux set-environment -g` before spawn. If it says no server is running, the next spawn starts a server from the caller shell. `PATH` comes from the spawning caller. (#14)
- The packaged personas gained specialization. A project that already ran `init` keeps its old persona files, because `init` never overwrites them. (#17)
- Package Python is 2,908 lines, up from 2,793 in 0.3.0. N4's 2,500-line target is not a hard limit. #15 adds 58 lines: shared helpers replaced the copied persona listing and the copied shown-text helpers, and what remains is the description and preset-source columns and writing the spawn mail before launch. #14 adds 39 lines for the skill refresh and the version and override lines in the init report. Naming the task mail in the first launch prompt adds 11, and the description scan and persona path check add 7. #17 changes no Python.

### Fixed

- A persona that is not valid UTF-8, or that cannot be opened, no longer makes `roster list`, `roster show`, or `seat list` fail. That role lists with an empty description. A preset file that cannot be read, including one that is not valid UTF-8, lists as `invalid`. A template that is not valid UTF-8, or that cannot be opened, no longer makes `seat list` fail. That seat lists with an empty description. (#15)
- A persona line that looks like a credential is never printed as a description by `roster list`, `roster show`, or `seat list`; the description is empty. A persona path that resolves outside `.foil/templates`, for example through a symlinked directory, is refused.

## [0.3.0] - 2026-09-30

`foil --version` prints 0.3.0.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.3.0.md](docs/plan-v0.3.0.md) is the plan for this release.
This release adds mail and board reads (issue #12) and roster commands
(issue #10). Both were developed on separate lines and ship together as
0.3.0.

### Added

- `foil mail read`, `foil mail list`, `foil board read`, and `foil board list`.
  A seat in a worktree can call `foil mail` and `foil board` as project
  tools; some harnesses still ask for some commands. `mail read` takes an
  absolute path. JSON mail includes the `mail/v1` contract. JSON board
  output keeps list front matter as arrays. (#12)
- `foil mail list` rejects an unsafe seat id. An outside-fleet caller
  gets `foil: not allowed`.
- `foil roster` commands for managing role templates: `list` shows all templates and available personas, `show ROLE` displays one template's configuration, `add ROLE` creates a template from a TOML file, or from the persona of the same name, `update ROLE FIELD=VALUE` modifies a template field, and `remove ROLE` deletes a template (fails for lead/implementer/reviewer). Authority: outside the fleet and lead only (F21). (#10)
- `foil roster list` and `foil roster show` accept `--json` for structured output.
- `foil init` report now includes available personas (those without templates) with a note to use `foil roster add`.
- The operator skill teaches the roster commands, first-run roster exploration, and adding a role from a Foil template file.
- The lead skill teaches roster commands and how to provision new roles.

### Fixed

- An invalid field name and a missing `--from` path stay on one error line when that text contains a newline.
- A roster `harness`, `model`, or `persona` that starts with `-` is refused, so `expand_argv` cannot place it as a harness flag. A symlinked `.foil/board/mail` directory lists nothing.

### Changed

- The command surface is seven top-level commands and twenty actions: four mail and board reads, and five roster actions.
- Requirements F21 documents the five roster subcommands. It no longer says the roster is managed only by editing template files.
- Skills and seat instructions use `foil mail read` and `foil mail list` instead of direct mail file reads.
- The lead's generated instructions list the `foil roster` commands. Roster errors are one line. Roster writes are atomic and do not follow a template symlink.
- Only a caller outside the fleet may set `permission`, including `add --from` when the file's permission is not `ask`. Remove and a harness change fail while a seat of that template has a stored state other than `killed`. A string value on `roster update` that contains a quote, a backslash, or a control character is refused, and an in-fleet update that changes `permission` is rolled back.
- Package Python is 2,767 lines. N4's 2,500-line target is not a hard limit. The lines over the target are the mail, board, and roster modules this release adds.

### Upgrading

`foil init` does not replace an existing project's `.foil/skills/`. A project upgraded from 0.2.2 keeps the old lead, operator, and worker skills, so it does not get the mail, board, or roster guidance. Move `.foil/skills/lead.md`, `.foil/skills/operator.md`, and `.foil/skills/worker.md` aside, or delete them if they were never edited. Run `foil init` again, and re-copy any user-level copy of the operator skill. New and resumed seats then get the new text.

## [0.2.2] - 2026-09-30

`foil --version` prints 0.2.2.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.2.md](docs/plan-v0.2.2.md) is the plan for this release.
The lead keeps a checklist in `status.md` of what is done and what is
left, and the operator reports that checklist. No command, flag, or
contract version was added.

### Changed

- Once the lead has a plan, the `status/v1` body holds a `## Checklist` of `- [ ]` and `- [x]` items. An item starts with its task id when it has one. `state: done` means every item is ticked. The front matter is unchanged, and Foil does not read the body.
- The lead skill says how to keep the checklist. The operator skill says to report it at each check-in and whenever the human asks, quoting the file rather than the pane.
- Both READMEs and both demos describe the checklist. The scenario 1 status sample includes a ticked checklist.

### Upgrading

`foil init` does not replace an existing project's `.foil/skills/`. A project upgraded from 0.2.1 to 0.2.2 keeps the old lead and operator skills, so it does not get the checklist. Move `.foil/skills/lead.md` and `.foil/skills/operator.md` aside, or delete them if they were never edited. Run `foil init` again, and re-copy any user-level copy of the operator skill. New and resumed seats then get the new text.

## 0.2.1 - 2026-09-29

This release was withdrawn. Its changes ship in 0.2.2.

[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.1.md](docs/plan-v0.2.1.md) is the plan for this release.
Onboarding is a sentence the human gives a coding agent. The agent
installs Foil, reads the `foil init` report, and starts the fleet.
No command or flag was added.

### Added

- `foil init` reports every installed eligible harness, in id order. The order is ascending Unicode code point of the id, case preserved: a tiebreak, not a ranking. `fake` is never eligible. A user preset that reuses a built-in id counts once. `lead` and `implementer` get the first installed id. `reviewer` gets the second when one exists, and the first otherwise. The report counts the distinct harness ids stored in the three templates. Two ids can still run the same program or model; set model on a template to choose one. It names what was written and what was left alone, and explains `ask` and `auto`. One invalid user preset is skipped and named; it does not abort `init`.
- Candidate personas: `documentation-writer`, `domain-designer`, `memory-curator`, `researcher`, and `verifier`. `init` copies every packaged persona and still writes only the three default templates. It overwrites nothing.
- Seats inherit `HOME` as well as `PATH`. A preset's `env` still names any further variables. The value is forwarded at launch and is not stored in the plan.
- A seat's instruction file includes its persona's text.
- The operator skill describes the template fields, the candidate catalog, and a few principles for picking a harness and a model. It tells the operator to peek after a resume, not only after a spawn.

### Changed

- A seat whose persona file is missing fails to spawn or resume with an error that names the file. `foil seat resume` with no name names each seat it could not restart, restarts the others, and exits 1.
- A fresh `init` used to pick `grok` first when several CLIs were installed. It now picks the first id in code-point order, so `claude` is first when it is installed. Re-creating the templates uses that new order.
- Both READMEs lead with the one sentence the human gives their coding agent. The by-hand commands stay as a second section. The agent asks the human to run each harness once in this repository and accept its folder-trust prompt as well as its first-run and opt-in dialogs, because trust is per folder, or to confirm that is already done, before the first spawn.
- [CONTRIBUTING.md](CONTRIBUTING.md) has a self-review to run before asking for review, and a new [AGENTS.md](AGENTS.md) points coding agents to it.
- Earlier documentation that had not been released: both READMEs carry the product's two names, the "loyal opposition" tagline, three problems Foil solves compared with a single agent session, a labeled diagram, how Foil is built, and badges for Python, platforms, and runtime dependencies. A demo per language ([docs/demo.md](docs/demo.md), [docs/demo.zh-CN.md](docs/demo.zh-CN.md)). [docs/architecture.md](docs/architecture.md) matches the scan, the persona copy, the inlined persona, and the launch environment.

### Known issues

- Claude Code, and any CLI that asks whether to trust a folder, waits on that prompt in a folder it has not trusted, and a waiting seat looks like a working one. Run the CLI once in the repository and accept the prompt before the first fleet.
- The live tier, pointed at Codex, passed two of six scenarios. The other four stopped on Codex's workspace-trust prompt in a fresh directory.
- The Claude Code operator-skill path (`~/.claude/skills/foil-operator/SKILL.md`) has not been checked by hand. The table still says no. The pointer line is the path that does not depend on that check.
- A harness's own first-run or opt-in dialog can still stop a seat, including after `foil seat resume`. The docs now say to dismiss those dialogs before the fleet starts, and to peek after a resume.
- With `permission = "auto"`, a Claude Code seat can still stop on Claude Code's own command-approval prompt. Peek new seats, and answer the prompt in that window.

## [0.2.0] - 2026-09-27

The command surface is `foil init`, `foil seat`, `foil send`, and
`foil memory`. `foil --version` prints 0.2.0.
[docs/requirements.md](docs/requirements.md) is the specification, and
[docs/plan-v0.2.0.md](docs/plan-v0.2.0.md) is the plan for this release.

### Added

- Templates for `lead`, `implementer`, and `reviewer`, and harness presets for `claude`, `codex`, `gemini`, `opencode`, `grok`, and `fake`.
- A Git worktree and `foil/<seat>` branch for seats whose template asks for one. Killing a seat leaves the branch and the worktree in place.
- Board mail. `foil send` writes the file, then types one nudge line: the sender and the mail path. The body is never typed.
- Generated instruction files, project memory, and the operator, lead, and worker skills. Each seat's first launch prompt carries its full instruction text, including its role skill.
- An onboarding path in both READMEs and the operator skill: install, `foil init` (which prints the operator's pointer line and the permission setting), a first-run check with the lead, then the goal. Persistent harness install paths are marked unverified.
- Operator scenarios that run against the fake harness, and the same scenarios against a real harness CLI when `FOIL_E2E_LIVE=1`. The default test run skips the live tier.
- [docs/architecture.md](docs/architecture.md) and a text walkthrough in [docs/demo.md](docs/demo.md). This changelog, the contributor guide, and the documentation index.

### Removed

- The 0.1 roster, catalog, status, doctor, dispatch, and mailbox commands, and the skills that described them.
- The 0.1 design documents. They remain at the v0.1.1 tag.

### Changed

- `foil seat list` reports `alive`, `dead`, or `killed`. A seat is alive only while a tmux window still carries its fleet and seat markers. Foil does not interpret pane text.
- `foil seat resume` restarts dead seats with the harness named by the template now.

### Live runs

Both runs used grok (`FOIL_E2E_LIVE=1 uv run --frozen --extra dev pytest tests/e2e -m e2e_live`).

- With the default `permission = "ask"`: 2 of 6 scenarios passed. Scenarios 1, 3, 4, and 5 timed out after 600 seconds. The run left no pane captures, but the same scenarios passed once seats ran with `auto`, so the seats were most likely waiting for approval.
- With `permission = "auto"`: 5 of 6 passed. Scenario 3 (resume after the tmux session dies) timed out: the resumed lead stopped on grok's own "Help improve Grok" opt-in dialog, not on a Foil or tool-approval prompt.

### Known issues

- A harness's own first-run or opt-in dialog can stop a seat, including after `foil seat resume`. Check new and resumed seats with `foil seat peek`.
- A seat's persona is referenced by path in its instructions rather than included in the text.
- Persistent install paths for the operator skill are unverified for every harness; the pointer line works everywhere.

## [0.1.1] - 2026-09-25

### Changed

- In an empty directory, run `git init` before `foil init`, so later
  commands find this project's only live fleet. `foil init --help`, the
  README, and the skills now say so. (#4)
- The skills no longer claim that `--seat` alone fails; lifecycle commands
  default to this project's Foil state and its only live fleet. (#4)
- A new end-to-end test follows the README Quick Start with fake agents on
  the real `foil` CLI.

### Known issues

- The package metadata and `foil --version` still report 0.1.0.

## [0.1.0] - 2026-08-27

First release. Foil runs a fleet of local CLI agents ("seats") in tmux,
with mail and status kept in files on disk.

- `foil init` sets up an existing Git repository or an empty directory:
  eight role templates, a starter seat roster in `.foil/seats.toml`, and
  an empty fleet.
- A lead-owned fleet: `foil seat spawn`, `list`, `inspect`, `stop`,
  `remove`, and `wake`. Only the lead, or a caller outside the fleet, can
  change membership.
- Seat recipes with `foil seats list`, `show`, and `set`. Built-in presets
  for `grok` and `opencode`; other CLIs join through a declarative profile.
- Worker seats get their own checkout by default: a local clone of the
  project, or a `git worktree` of another repository with `--from`.
- A durable file mailbox with an opt-in tmux wake: `foil send-message`,
  `ack-message`, and `message-status`.
- Shared notepads and reviewed memory lessons.
- `foil status`, `poll-status`, and `resume` (live tmux first, then the
  CLI's own session, then a fresh start), plus `doctor`, `dispatch`,
  `set-state`, and `catalog-list`/`catalog-map` for local persona files.

[0.6.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.6.0
[0.5.4]: https://github.com/TiantianFlow/foil/releases/tag/v0.5.4
[0.4.5]: https://github.com/TiantianFlow/foil/releases/tag/v0.4.5
[0.4.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.4.1
[0.4.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.4.0
[0.3.4]: https://github.com/TiantianFlow/foil/releases/tag/v0.3.4
[0.3.3]: https://github.com/TiantianFlow/foil/releases/tag/v0.3.3
[0.3.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.3.0
[0.2.2]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.2
[0.2.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.2.0
[0.1.1]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.1
[0.1.0]: https://github.com/TiantianFlow/foil/releases/tag/v0.1.0
