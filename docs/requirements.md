# Foil requirements

## 1. Summary

Foil is a lightweight command-line tool for running a fleet of CLI coding
agents in tmux. A human works through their own agent harness, the
*operator*, which uses Foil to start a *lead* agent and hand it a goal.
The lead spawns worker agents from role templates, directs them, and
integrates their work. Agents talk through short messages and shared
files. Anyone can peek at an agent's screen, but Foil never interprets
what it shows. All project work is done by the fleet, never by the
operator.

## 2. Glossary

| Term | Meaning |
|---|---|
| Operator | The human's own harness, outside the fleet. It is never a seat. Foil sees it as "a caller outside the fleet". |
| Fleet | All seats Foil runs for one project. One fleet per project. |
| Seat | One agent process in its own tmux window. |
| Lead | The one seat allowed to spawn and kill other seats. |
| Worker | Any seat that isn't the lead. |
| Template | A role definition: an ordered list of harness entries, a persona, a worktree flag, and a permission mode. Each entry is a preset id and an optional model. The project's templates form its roster. |
| Harness preset | How to launch one agent CLI (for example `claude` or `codex`). |
| Board | A shared folder all seats can read and write, holding mail, notes, and contract files. |
| Mail | A message to one seat, stored as one file on the board. |
| Nudge | The one line Foil types into a seat's pane to announce new mail. |
| Note | An unaddressed shared file on the board (the notepad). |
| Contract | A board file with an agreed, versioned format. |
| Memory | Reviewed lessons for the project. They outlive fleets and are given to every new seat. |
| Peek | Printing the raw last lines of a seat's pane. |

## 3. Workflow

```text
human ──▶ operator (outside the fleet)
             │  foil init · foil seat spawn lead · foil send lead
             │  foil seat list · foil seat peek lead
             ▼
        ┌──────────── tmux: one fleet ─────────────┐
        │  lead ──spawn/kill──▶ implementer-1       │
        │   │                   implementer-2       │
        │   └──send──▶          reviewer-1          │
        └───────────────────────────────────────────┘
             all seats read/write the board
```

1. The human asks the operator for something.
2. The operator runs `foil init` once per project, then
   `foil seat spawn lead --task "<goal>"`.
3. The lead receives the goal as mail and plans the work.
4. The lead spawns workers by role (`foil seat spawn implementer`) and
   kills them when they're done. Implementation seats get their own git
   worktree and branch.
5. Seats send short messages with `foil send`, and put durable work
   products (task briefs, results, reviews, status) on the board.
6. Every few minutes the operator checks in: it lists seats, peeks at the
   lead, and reads the lead's status file. It passes the lead's questions
   to the human, sends the human's answers to the lead, and nudges the
   lead if it looks stuck. It does no project work. The lead doesn't know
   an operator exists; it acts as if a human is talking to it.
7. The lead integrates the workers' branches, has the result reviewed,
   and reports completion in its status file.
8. After a crash or reboot, `foil seat resume` restarts dead seats.

### Onboarding

Onboarding adds no command or flag. A coding agent performs it. The
human's part is one sentence; everything after step 2 is the agent's work.

1. Prerequisites are Python, Git, tmux, and at least one harness CLI that is already logged in. Foil never handles that login. Before the first spawn, the agent runs `foil roster list`, names each distinct harness id the roster uses, and asks the human to run each of those once in the repository root and accept its folder-trust, update, first-run, and opt-in dialogs, or to confirm that is done. Trust is per harness per path. A seat worktree lives in the store, outside the repository, so it does not inherit the repository's trust. Each seat label needs folder trust once per harness. The agent asks again when a role moves to a harness id that was not on that list. A seat waiting at one of those looks from outside exactly like a seat that is working. A dialog seen in `foil seat peek` is answered by the agent only when it is an approval dialog, as F12 bounds it. Every other dialog is reported to the human and not answered. Seats run as the same user and inherit the login (F26).
2. In the repository, the human asks the coding agent they already use: `Install Foil from https://github.com/TiantianFlow/foil, onboard this repository with it, and start a fleet. My goal: <goal>.`
3. The agent installs or updates Foil with `uv tool install "git+https://github.com/TiantianFlow/foil.git"`. Running it again updates an existing install. It checks that `foil --version` prints the version it expects, then runs `foil init` in the repository. The first init with no eligible harness installed exits `foil: no harness installed` and writes no project folder. The agent stops and does not spawn. The human installs a harness CLI and the agent runs `foil init` again. A user preset can be added only after that folder exists. When a harness is installed, `init` writes the project folder in the store and reports what it found and what it wrote. The first line is the version. The report names every installed eligible harness (F27), the id each default template was given and why, the templates and personas it created and the ones it left alone, whether it replaced the operator skill, `Available personas (use 'foil roster add')` when a persona has no template, a built-in preset overridden by the project folder's `harnesses/`, the permission choice, the project folder path, and the pointer line. That report is the first thing the agent reads; the operator skill in step 4 is the second.
4. The agent reads the operator skill at the path the pointer line names and follows it. An agent that was started without the sentence in step 2 gets the same instruction from that line: `Read <project folder>/skills/operator.md and follow it. My goal: <goal>.` That needs nothing installed in the harness. A persistent install is optional: copy the skill where that harness discovers skills, at user level, so it stays out of `git status` (F7).

| Harness | Install path | Verified |
|---|---|---|
| any | the pointer line above | required; not a per-harness install |
| Claude Code | `~/.claude/skills/foil-operator/SKILL.md` | no |
| grok, codex, opencode, gemini | none published | no |

5. Before spawning any seat, the agent sets each template's harness list and permission, and adds any candidate role the goal needs with `foil roster add` beside a persona `init` already copied (F20). A list of one entry is what `init` writes. `roster update` of `harness` or `model` edits that entry. A longer list is edited in the template file (F21). A spawn takes the entry at the count of seats already stored for that role (F3). An omitted permission is `auto`: `permission = "auto"` is the default, and it asks the harness to skip approval prompts, though some harnesses still ask for some commands. `permission = "ask"` is the explicit choice, set by a caller outside the fleet: that seat stops at its first approval prompt and waits for the human in that pane. Each template carries its own permission. The agent pairs `auto` with a worktree when that seat can commit.
6. Foil launches each seat and delivers instructions with no harness-specific flags. The first launch prompt inlines the instruction file: the role skill, persona, commands, and board conventions. That text tells the seat to re-read the instruction file whenever it is woken. The operator and the lead put only the goal in `--task`. The instruction file stays the single source. Harness system-prompt flags are not used.
7. The agent's first task is the first-run check: spawn the lead with `Write board/status.md with state: done`, then wait a few minutes. `seat spawn --task` writes that task as mail before launch and types nothing into the new pane, so a seat at a startup dialog waits there. If `board/status.md` is already there from an earlier goal, the agent notes its `updated` value before the spawn, and the check passes only when `updated` changes and the file shows `state: done`. If the file does not appear or `updated` does not change, the agent runs `foil seat peek lead`. It may send keystrokes only for an approval dialog, as F12 bounds it: a folder-trust prompt, a login prompt, or a prompt that blocks the seat until a human or the agent approves it. An update dialog, a first-run dialog, an opt-in, and an error are reported to the human and not answered. If that lead is dead, the agent runs `foil seat kill lead` and spawns the check again only after that seat is gone. It does not spawn a second lead while one exists. A seat with its own worktree may stop on folder trust for that worktree's path in the store. The same label asks once per harness.
8. When the check passes, the agent sends the goal to that lead and does no project work itself.
9. Foil is one command for every project on the machine. Updating it changes the command under every live seat at once, but each seat keeps the instructions it was started with. A mid-goal upgrade works while the registry schema, the session-name derivation, and the `@foil-fleet-id` and `@foil-seat-id` markers stay compatible, and only when the project already lives in the store. An in-repo `.foil/` left by 0.3.x is not that case. The order is kill, then upgrade, then `foil init`: with the old `foil` still on `PATH`, `foil seat kill --all` in that project (a stale seat whose tmux window is already gone also counts), then upgrade, then `foil init`. A refused init creates nothing in the store. A 0.4.x command that still finds that directory does not say `foil: not initialized` (N8). When seats remain, it names them and this order. When none remain, it says to run `foil init`. The same order, with the installed `foil`, is how a later upgrade runs between goals: `foil seat kill --all`, update, `foil init`, then spawn the lead with the first-run check (step 7). The earlier goal's `status.md` already says `state: done`, so the check waits for its `updated` value to change. Re-copy any user-level copy of the operator skill.

## 4. Functional requirements

### Fleet and seats

| ID | Requirement |
|---|---|
| F1 | The command set is exactly the one in section 6. |
| F2 | The template named `lead` defines the lead, and its seat is always named `lead`. A fleet has at most one live lead, and the first seat spawned must be the lead. A new lead may be spawned only after the previous one was killed. |
| F3 | `seat spawn` takes a template name, not a harness command. The template holds one ordered list of harness entries (section 7.2). Spawn selects `entries[n % len(entries)]`, where `n` is the number of seat records already stored for that role, including killed seats, counted before the new seat is inserted. A list of one stays on that entry; the length is not a special case. The chosen id and model are written on the new seat. A failed launch writes no seat record, so a retry selects the same entry. The lead reuses the single registry record named `lead`, so the count is 0 before that record exists and 1 before every later launch, including one that replaces a killed lead. Worker names are auto-numbered from the template name (`implementer-1`, `implementer-2`) unless `--name` is given. A worker name is never reused within a fleet. |
| F4 | Only the lead or a caller outside the fleet may spawn, kill, or resume seats, or review memory (section 6.2). |
| F5 | A seat's identity comes from an environment variable Foil sets when launching it. A seat can't claim another identity through any flag. This protects against mistakes by cooperating agents, not against hostile processes, and the documentation says so. |
| F6 | When a template asks for a worktree, spawning creates a git worktree on a new branch whose name is unique in the repository. Killing a seat never deletes its branch and never silently destroys uncommitted work. |
| F7 | Foil writes nothing into the working tree. It creates no exclude entry. Files and worktrees it creates never appear as untracked or modified files in the user's repository. |
| F8 | Seat state is `alive` (its exact tmux window exists and its pane process is still running), `dead` (the window is gone, or the pane process has exited, and the seat wasn't killed), or `killed` (stopped with `foil seat kill`). Whether the pane process has exited is tmux's `#{pane_dead}` fact, not a reading of the pane. A seat window stays after its process exits, so the pane can still be read. |
| F9 | `seat resume` restarts `dead` seats, never `killed` ones. It launches with the harness and model recorded on the seat at its last launch, and with that harness's preset. It does not read the template's list to choose again, and it does not overwrite the seat's harness or model. A new session id is not minted only because the template's first entry differs from the recorded harness. It uses the harness's own session resume when the preset supports it; otherwise it starts the seat fresh and tells it that it was restarted and should re-read its mail. A seat with no worktree resumes in the project directory. A seat that cannot be restarted is named in the error and does not stop the others. |

### Visibility

| ID | Requirement |
|---|---|
| F10 | `seat list` reports only facts Foil owns: each seat's name, template, state, and worktree, plus the harness and model the registry recorded at that seat's last launch, and its template's current description. That recorded pair is what resume launches (F9). Editing the template's harness list while the seat is alive does not change the pair `seat list` shows. The description is empty when the template is gone, the template cannot be read, or the persona cannot be read. A seat record with no `model` field loads that field as empty. |
| F11 | `seat peek` prints the raw last lines of one seat's pane, exactly as tmux captures them. It does this for an alive seat and for a dead seat whose window still exists and still carries this fleet's and this seat's markers, so a process that exited at launch leaves an error that can be read. A killed seat, a dead seat whose window is gone, or a stored window id that now names another window is an error. |
| F12 | Foil never parses, classifies, or interprets pane contents. Whether an agent is busy, idle, blocked, or done is stated by the agents themselves in board files. The operator, who is outside the fleet, may read a pane and send keystrokes only for an approval dialog: a folder-trust prompt, a login prompt, or a prompt that blocks the seat until a human or the operator approves it. That reading is the operator's. Foil does not scrape a pane, does not decide that a dialog is present, and does not send the keys. The operator does not send keys for anything else, and does not decide from a pane who is working or what they are working on. |

### Communication

| ID | Requirement |
|---|---|
| F13 | `send` stores the message as a mail file on the board, then types one nudge line into the recipient's pane: the sender and the mail file's absolute path. After that line is delivered it waits 200ms, then sends Enter once, so a harness that treats Enter inside a fast burst as an unsent paste (Codex keeps that burst open for 120ms) submits the line instead of leaving it in the input. The wait does not depend on the pane. Foil does not read the pane and does not send Enter again. The message body is never typed into a pane. |
| F14 | Mail sent from outside the fleet shows the sender as `user`. There is no operator mailbox. |
| F15 | The board has a notes area for unaddressed shared files. Seats write notes with ordinary file tools and read them with `foil board read` and `foil board list`. Notes wake no one. |
| F16 | Board contracts (section 7.4) are Markdown files with versioned front matter. Foil defines them and tells seats about them. Foil does not act on contract fields (`state`, `outcome`, and the rest). `mail read`, `mail list`, and `board read` may parse front matter only to print the file. |

### Memory

| ID | Requirement |
|---|---|
| F17 | Memory lessons belong to the project and survive any fleet. |
| F18 | Any seat or the operator may propose a lesson and list lessons. Only the lead or the operator may accept or reject a proposal. A proposal may name a lesson it replaces; accepting it marks the replaced lesson superseded. |
| F19 | All accepted lessons are included in every seat's instruction file at launch. |

### Board reads

| ID | Requirement |
|---|---|
| F29 | `mail read` reads a mail file by absolute path. The path must be in the project folder's `board/mail/`. It outputs the body only in human mode, and the full contract with body in JSON mode (`--json`). |
| F30 | `mail list` lists mail files for the current seat, identified by `FOIL_SEAT_ID`. It fails when called outside a fleet. A symlinked `board/mail` directory lists nothing. Mail is sorted newest first. Human output: one line per mail with time, sender, and path. JSON output: array of mail metadata. |
| F31 | `board read` reads a board file by path (relative to board root or absolute). The path must be in the project folder's `board/`. Human output: full file contents. JSON output: parsed frontmatter plus body for contract files, or `{"body": "..."}` for non-contract files. |
| F32 | `board list` lists board files matching a glob pattern (relative to board root). Human output: one path per line, sorted alphabetically. JSON output: `{"files": [...]}`. |

### Templates, presets, and instructions

| ID | Requirement |
|---|---|
| F20 | Templates are per project. `init` creates default templates for `lead`, `implementer`, and `reviewer`, each with a real persona prompt and an installed harness chosen by F28. Each of those files is one `[[harness]]` entry for that id, and it omits `model`. `implementer` asks for a worktree. `init` also copies every packaged persona into the project, including candidate roles that have no default template, so adding a role is `foil roster add` beside a persona that is already on disk. `init` never overwrites an existing template or persona. Re-running it after the roster is edited leaves those files alone. |
| F21 | `foil roster` manages templates. A role name is checked against the safe-id rule before any template path is built. `list` shows every template and every persona that has no template. `show ROLE` shows one template. `add ROLE` creates one. Without `--from`, `personas/ROLE.md` must already exist; the new file is one `[[harness]]` entry for the first installed id, with no `model` line, `worktree = false`, and `permission = auto`. `--from FILE` copies that TOML. Add fails if ROLE exists, the name is not a safe id, or the file is not a valid template. Writes are atomic in the templates directory and refuse a symlink. An invalid result is removed, including an unknown harness or a persona path that leaves the project folder's `templates/`. `update ROLE FIELD=VALUE` changes one of `harness`, `model`, `persona`, `worktree`, and `permission`, splitting on the first `=`. Those names are how a caller edits a template. They are not a second schema beside the harness list. When the role has one entry, `harness` replaces that entry's id and `model` replaces that entry's model. When the role has more than one entry, `harness` and `model` are refused, the file is left unchanged, and the error names the template file, because one value cannot edit a list. A successful update writes the canonical `[[harness]]` list. A `harness`, `model`, or `persona` value that contains a quote, a backslash, or a control character is refused before the file is written. A `harness`, `model`, or `persona` value that starts with `-` is refused before the file is written. A template is invalid when an entry's `model` or the persona starts with `-`. A harness id is a safe id, so it cannot start with `-` either. A hand-edited file and `add --from` then fail closed, and that value is not placed in the harness argv. An invalid result is rolled back to the previous bytes. Only a caller outside the fleet may set `permission`. If an in-fleet update changes `permission`, the previous bytes are restored and the update fails with `foil: permission is outside the fleet only`. `add --from` is included: a caller inside the fleet is refused, before the template file is created, when the file's permission is not `auto`, with `foil: permission is outside the fleet only`. An omitted permission is `auto`. `remove ROLE` deletes a template and fails for `lead`, `implementer`, and `reviewer`, and while a seat of that template has a stored state other than `killed`. Seat records stay, including killed ones. Editing the harness list is allowed while such a seat is alive, including `harness` or `model` on a one-entry role. A harness change used to fail in that case because restart rebound the seat to the template. Resume keeps the recorded pair (F9), so that gate is gone. The running seat keeps the pair from its last launch (F10). Every error is one line. A role's description is the first line of its persona that is neither blank nor a Markdown heading. The description is empty when no such line exists, the line is credential-shaped (the same scan Foil applies to mail), the template is invalid, or the persona cannot be read, and that role still appears in `list`. `list` and `show` report every harness entry, in order, and the preset source of each id: `builtin`, `user` when the project folder's `harnesses/<id>.toml` loads, or `invalid` when the id is unknown or the preset does not load. Workers cannot run roster commands. The lead can, except setting `permission`. |
| F22 | A template's persona may be inline text or a path to a Markdown file, which is used untouched. A missing file inside the template directory raises `foil: persona file not found: <value>`. An absolute path or any `..` part raises `foil: persona path must stay inside the project templates: <value>` before filesystem access. So does a path that resolves outside the template directory, for example through a symlinked directory. A newline, a carriage return, or a single line that does not end in `.md` stays inline text. |
| F23 | Harness presets ship for `claude`, `codex`, `gemini`, `grok`, and `opencode`, plus `fake` (a test double, section 10, never eligible under F27). Users can add their own presets in the same format. The list is in F28's order, so the two never read as different orders. |
| F24 | Each seat gets a generated instruction file at launch. That file is the single source of the seat's instructions. The first launch prompt inlines the file's full text, including the role skill, persona, commands, and board conventions, and tells the seat to re-read the instruction file whenever it is woken. On a first launch with `--task`, it names that task's mail file, tells the seat to read it first, and says older mail may be from an earlier goal, because a reused seat name such as `lead` keeps its mailbox. On a first launch without `--task`, it tells the seat to run `foil mail list` and read its mail before it acts. The file contains the seat's name, the lead's name, the board path, the exact commands the seat may run, the board and contract conventions, all accepted memory lessons, the Foil version that wrote it, and, for the lead only, the available templates. |

### Skills

| ID | Requirement |
|---|---|
| F25 | Three skills ship in the package, as plain Markdown, matching the command reference exactly (section 8). Each skill file starts with YAML front matter that gives its `name` and `description`. `init` writes the operator skill into the project's Foil folder and replaces that file when its bytes differ from this version, atomically, refusing a symlink. The report says `Updated skills: …` or `Skills: current`. It does not write the lead or worker skill, and it does not delete an older copy of either. It does not overwrite a template, a persona, or a preset. A seat's instruction file inlines that seat's role skill from the package. The lead skill and the worker skill are that seat's role guidance. A default persona adds only specialization the skill does not already state. |

### Launch environment

| ID | Requirement |
|---|---|
| F26 | Seats run as the same user as the caller and inherit that user's harness login. Foil always forwards `PATH` and `HOME` to a seat; a preset's `env` names further variables. A variable a preset names takes its value from the tmux server's environment; `PATH` comes from the spawning caller. Forwarding is declared by name: Foil reads a named variable's value only to hand it to the child process at launch, and never stores, prints, or logs it (N5). A variable no one named is not forwarded. |

### Harness discovery

| ID | Requirement |
|---|---|
| F27 | An **eligible** preset is a built-in preset or a user preset in the Foil folder, apart from `fake`, which is a test double: it is never reported and never chosen. A user file that reuses a built-in id replaces that built-in, so it is one id and not two. A preset is **installed** when its own `command[0]` is on the path. `init` reports every installed eligible preset. When a file in the project folder's `harnesses/` reuses a built-in id, `init` reports `Built-in presets overridden by harnesses: …`. |
| F28 | F28 chooses the harness each default template gets. Eligible ids are sorted into one total order over built-in and user ids alike, by **ascending Unicode code point of the id string, with case preserved**. That order is not case-folded and not locale-dependent. A legal id matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, so `Alpha` sorts before `alpha`, and `a-b` before `a.b` before `a_b`. Over the built-in ids alone the order reads `claude`, `codex`, `gemini`, `grok`, `opencode`, and any user id takes its own place among them. `lead` and `implementer` are given the first installed id; `reviewer` is given the second installed id when one exists, and the first otherwise. The order and the assignment are a function of the eligible ids and of which of them are installed, so the same Foil folder and the same path give the same three ids. The order is a tiebreak, not a ranking: Foil states no preference among vendors and ships no vendor or model blocklist. What this gives the default roster is **two different harness ids**. Two ids can still run the same program or model; set `model` on that entry to choose one. Two presets may wrap the same program or reach the same model; that is allowed. `init` reports which id each default template was given and why. Each default template stores that id as one harness entry and omits `model`. |

## 5. Non-functional requirements

| ID | Requirement |
|---|---|
| N1 | Python 3.11+ with the standard library only; no runtime dependencies. |
| N2 | External programs are limited to tmux 3.2+, git, and the harness CLIs. |
| N3 | Supported platforms: Linux and macOS. |
| N4 | **Lightweight:** the package source targets at most 2,500 lines of Python. The target is not a hard limit: an automated check reports the count, and going over it needs a stated reason. The exact command surface is enforced by an automated test. |
| N5 | Foil does not store or log credentials. Environment variables are forwarded to seats by name only and are never printed. Memory lessons and mail written by `send` are refused if they contain credential-shaped text. `foil board read` does not apply that refusal: it prints the file as stored, whoever wrote it. |
| N6 | Tmux windows are always targeted by exact window ID, never by name. |
| N7 | Foil's own state is written atomically and carries a schema version. A release reads the registry and memory the previous release wrote. New fields are optional with a default. A schema bump either reads the previous schema or the changelog says to drain the fleet with `foil seat kill --all` using the old binary first. |
| N8 | Every error is a one-line message on stderr with a non-zero exit code. No Python tracebacks. When a 0.4.x command finds an in-repo legacy `.foil/`, that line is a legacy-layout message, not `foil: not initialized`. It names each seat the old registry still has in a state other than `killed`. A stale seat whose tmux window is already gone also counts, and the message says so without claiming that one exists. It names the order: kill them with the old `foil` (`foil seat kill --all`), then upgrade, then `foil init`. When that old `foil` is no longer on `PATH`, it gives a version-pinned install into a separate tool directory: `UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`. With pipx: `PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`. Either install does not replace the `foil` on `PATH`. When no such seat remains, the message says to run `foil init` and does not name the kill order. An unreadable registry says the registry is unreadable, not that a seat named `unknown` exists. It never says to move the directory aside. |
| N9 | Every tracked file and every commit is safe to publish: no secrets, personal paths, private hosts, or personal emails. Automated tests check the tracked tree. |

## 6. Commands

### 6.1 Command reference

These are the only commands, subcommands, and flags. Every command also
accepts `--help`, and `foil --version` prints the version.

| Command | Flags | Behavior |
|---|---|---|
| `foil init [DIR]` | none | Checks that tmux and git exist and that DIR (default: current directory) is a git repository. Creates the project folder in the per-user store, the default templates, every packaged persona, and the operator skill. It prints that folder's path. It writes nothing into the working tree and no exclude entry. When one stored project has the same origin remote and its recorded path is gone, it adopts that record. A repository with no origin is matched the same way: exactly one store record whose recorded path is gone and whose repo identity matches this repository. The root commit is one such identity. Init adopts that record and prints which one. It adds no flag and no prompt. When several records match, it says so and adopts none. When no match is possible, it warns that an orphaned store record exists, names that record's path, and does not silently create a second empty project. A record is considered only when it could be this repository: the same root commit, or, for a repository with no origin, no root field and no origin of its own. A record for some other repository is ignored, including when this repository has no commits. The warning does not stop init. Adopt runs `git worktree repair` and, only after that repair succeeds, rewrites every absolute path in `run/registry.json`, `run/plans/`, and `run/instructions/` to the store paths, whatever the old absolute prefix was, including a repository directory that was renamed before init, and whether or not any seat has a worktree. The old prefix covers the old checkout, the old `.foil/` directory, and each seat worktree. The old `.foil/` prefix is read from a worktree's gitdir text before repair, by stripping `/worktrees/<label>`. The rewrite also matches a path that JSON stored with escapes. The same rewrite replaces a stale `FOIL_PROJECT` value stored in `run/plans/*.json` with the current project key, and that write is atomic (N7). A recorded checkout that no longer exists is rewritten to the main checkout. A simple legacy move does the same rewrite. If `git worktree repair` fails, init reports `foil: could not repair worktrees` and exits non-zero, and the stored paths are left as they were. An in-repo `.foil/` whose registry still has a seat in a state other than `killed` blocks init, and a stale seat whose tmux window is already gone also counts. That refusal creates nothing in the store: no project folder and no skeleton a later move could collide with. It names each blocking seat and says a stale seat also counts, without claiming that one exists. The order it names is: kill them with the old `foil` (`foil seat kill --all`), then upgrade, then `foil init`. When that old `foil` is no longer on `PATH`, it gives the version-pinned install from N8. It never says to move the directory aside. An in-repo `.foil/` with no such seat is moved into the store when the move is simple. A store that holds only a fresh skeleton, such as empty directories and `project.json` left by a refused init, is not a collision: those empty directories are replaced. A real collision names the stored entry that is already taken and says to remove it, then run `foil init` again. A directory that is not a simple tree says that `board`, `run`, and `templates` must be real directories, then says to run `foil init` again. Neither message names the kill order. Both leave the old directory in place. Safe to re-run. A re-run finishes a repair and a path rewrite that failed earlier, for an adopted folder and for a legacy move. The recorded checkout is the one saved before this run writes the current checkout into `project.json`. When that path differs from the current checkout and no longer exists, or a seat gitdir still names a missing path, init runs repair and the rewrite again. The rewrite maps the old store root or the old `.foil/` whenever that directory itself no longer exists, not only the seat worktree paths. The check that the parent directory is gone applies only to rewriting the old checkout to the current checkout. It overwrites no template, persona, or preset. It replaces the operator skill, atomically and refusing a symlink, when the file's bytes differ from this version, and reports `Updated skills: …` or `Skills: current`. It does not write the lead or worker skill, and it does not delete an older copy of either. The first line of its output is the version. The rest is the onboarding report an agent works from: every installed eligible harness, the id each default template was given and why (F28), what it wrote and what it left alone, `Available personas (use 'foil roster add')` when a persona has no template, `Built-in presets overridden by harnesses: …` when a project file reuses a built-in id, the permission choice, and the operator pointer line (Onboarding). |
| `foil seat spawn TEMPLATE` | `--name NAME`, `--task TEXT` | Creates a seat from a template (F2, F3, F6, F24). `--task` is written as mail before launch, the instruction file names that mail file, and nothing is typed into the new pane. If launch fails, that mail file is removed. |
| `foil seat kill NAME` | `--all` (no NAME) | Stops the seat's window and marks it `killed` (F6). `--all` stops every seat. |
| `foil seat resume [NAME]` | none | Restarts `dead` seats (F9). |
| `foil seat list` | `--json` | Lists seats (F10). Human: `<name>\t<template>\t<state>\t<worktree>\t<harness>[/<model>]\t<description>`. JSON adds `harness`, `model`, and `description`. |
| `foil seat peek NAME` | `--lines N` (default 40) | Prints the pane tail (F11). A dead seat whose window still exists and still carries this seat's markers is printed the same way. A killed seat, a dead seat whose window is gone, or a stored window id that now names another window is an error. |
| `foil send TO TEXT` | none; `TEXT` = `-` reads stdin | Writes mail and nudges the recipient. Enter follows the nudge line after 200ms (F13, F14). |
| `foil mail read PATH` | `--json` | Reads mail file at PATH. PATH must be absolute and in the project folder's `board/mail/`. Human output: body only. JSON: full contract + body. |
| `foil mail list` | `--json` | Lists mail for current seat (FOIL_SEAT_ID required). A symlinked `board/mail` lists nothing. Sorted newest first. Human: `<time> <from> <path>` per line. JSON: array of mail metadata. |
| `foil board read PATH` | `--json` | Reads board file at PATH (relative to board root or absolute). PATH must be in the project folder's `board/`. Human output: full file. JSON: parsed frontmatter + body. |
| `foil board list PATTERN` | `--json` | Lists board files matching glob PATTERN (relative to board root). Sorted alphabetically. Human: one path per line. JSON: `{"files": [...]}`. |
| `foil memory add TEXT` | `--replaces ID`; `TEXT` = `-` reads stdin | Proposes a lesson and prints its ID (F18). |
| `foil memory accept ID` | none | Accepts a proposal. |
| `foil memory reject ID` | none | Rejects a proposal. |
| `foil memory list` | `--all`, `--json` | Lists accepted lessons; `--all` lists every lesson with its state. |
| `foil roster list` | `--json` | Lists templates and personas with no template (F21). Human: `<role>\t<id>[/<model>][,<id>[/<model>]...]\t<preset>[,<preset>...]\t<description>`, then `+ <persona>\t<description>`. One entry with no model is `<role>\t<id>\t<preset>\t<description>`. JSON `harness` is `[{"id","model"}, ...]` in list order, with `model` `""` when the entry omits it. JSON `preset` is the source of each entry, in the same order. JSON has no separate `model` field. `personas` is `[{"name","description"}]`. |
| `foil roster show ROLE` | `--json` | Shows one template (F21). Human: `harness:` is the same entry list as `roster list`, then `persona:`, `worktree:`, `permission:`, `preset:`, and `description:`. JSON is that role's list fields plus `persona`. |
| `foil roster add ROLE` | `--from FILE` | Creates a template (F21). Without `--from`: first installed harness, `worktree = false`, `permission = auto`. Inside the fleet, `--from` is allowed only when the file's permission is `auto`; an omitted permission counts as `auto`. |
| `foil roster update ROLE FIELD=VALUE` | none | Changes one field (F21). `permission` is outside the fleet only. An invalid result is rolled back, including an in-fleet update that changes `permission`. A string value with a quote, a backslash, or a control character is refused. A `harness`, `model`, or `persona` value that starts with `-` is refused. `harness` and `model` edit the single entry when the role has one. When it has more than one, those two are refused and the file is left unchanged. Editing the list while a seat is alive is allowed. |
| `foil roster remove ROLE` | none | Deletes ROLE (F21). Fails for lead, implementer, and reviewer, and while that template is in use. |

That's seven top-level commands (`init`, `seat`, `send`, `memory`, `mail`, `board`, `roster`) and
twenty actions.

### 6.2 Authority

| Caller | `init` | `seat spawn` | `seat kill` | `seat kill --all` | `seat resume` | `seat list` / `peek` | `send` | `mail read` | `mail list` | `board read` | `board list` | `memory add` / `list` | `memory accept` / `reject` | `roster list` / `show` | `roster add` / `update` / `remove` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Outside the fleet | yes | yes | yes | yes | yes | yes | yes | yes | no | yes | yes | yes | yes | yes | yes |
| Lead | no | yes | yes, not itself | no | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes, except `permission` |
| Worker | no | no | no | no | no | yes | yes | yes | yes | yes | yes | yes | no | no | no |

A caller inside the fleet cannot set `permission` on update, and cannot `roster add --from` a file whose permission is not `auto`. An omitted permission is `auto`. The lead still cannot set `permission`.

## 7. Data design

### 7.1 Layout

Everything for one project lives in one folder in a per-user store (F7).
The store root is `$FOIL_HOME` when that is set, made absolute so a
relative value does not follow a later working directory, otherwise
`$XDG_DATA_HOME/foil`, otherwise `~/.local/share/foil`. The project
folder is `<root>/projects/<key>/`. `<key>` is a readable slug of the
realpath of the Git common dir plus a short hash. The main checkout, a
linked worktree, and a seat worktree share that common dir, so they
share one folder. `project.json` records the main checkout, the origin
remote when there is one, the root commit when there is exactly one, and
the creation time. Init keeps a recorded
path that still exists. Otherwise it records the parent of the Git
common dir, including when init runs from a linked or seat worktree. After adopt or a
legacy move, and only after `git worktree repair` succeeds, absolute paths in `run/registry.json`, `run/plans/`, and
`run/instructions/` are rewritten to the store paths no matter
what the old absolute prefix was, including a repository directory that
was renamed, and whether or not any seat has a worktree. The old prefix covers the old checkout, the old `.foil/` directory, and each seat worktree. The old `.foil/` prefix is read from a worktree's gitdir text before repair. The rewrite also matches a path that JSON stored with escapes. A stale
`FOIL_PROJECT` value stored in `run/plans/*.json` is rewritten to the
current project key, and that write is atomic (N7). A failed repair leaves the stored paths unchanged. A later init maps the old store root or the old `.foil/` whenever that directory itself no longer exists. When the gitdir no longer names that store, the old folder is `<projects>/` plus the project key of the checkout recorded before the write. The check that its parent is gone applies only when rewriting the old checkout to the current one. The recorded checkout used for that retry is the path saved before this run writes the current checkout. A recorded checkout that no longer
exists is rewritten to the main checkout. A seat's environment
includes `FOIL_PROJECT` set to that key. It also includes `FOIL_HOME`
or `XDG_DATA_HOME` when the spawning process had that variable set, so
the seat resolves the same store. Every command resolves the project
the same way. `FOIL_PROJECT`, when it names a folder that has
`project.json`, selects that folder's recorded repository path.
Otherwise the command uses the Git toplevel of the working directory,
then the same key. The main checkout, a linked worktree, and a seat
worktree share the Git common dir, so they share the folder.
Templates, skills, and harnesses stay inside that project folder. This
release has no user-level layer above the project. The concepts below
are fixed; exact names may vary.

```text
<root>/projects/<key>/
  project.json                 path, origin, root, created
  templates/<role>.toml        role templates (the roster)
  templates/personas/<role>.md every packaged persona, including
                               candidate roles with no template yet
  harnesses/<id>.toml          optional project harness presets
  memory/                      lessons; survive fleets
  board/
    mail/<seat>/<time>-<from>-<rand>.md
    notes/                     unaddressed shared files
    status.md                  lead's status (status/v1)
    tasks/<id>.md              task/v1
    results/<id>.md            result/v1
  run/                         registry and generated instruction files
  skills/operator.md           the operator skill. The lead and worker
                               skills are read from the package.
  worktrees/<label>            one directory per seat worktree
```

### 7.2 Template

The file name is the role name. A role has one ordered list of harness entries. Spawn reads that list (F3). There is no second harness string or model field beside it.

```toml
persona = "personas/implementer.md"
worktree = true
permission = "auto"

[[harness]]
id = "claude"
model = "opus"

[[harness]]
id = "codex"
model = "gpt-5.5"
```

`persona`, `worktree`, and `permission` are written before the entries. A key after an entry is part of that entry.

| Field | Required | Meaning |
|---|---|---|
| `harness` | yes | Ordered list of entries. Each entry is a table with `id` (a preset id, and a safe id) and optional `model` (passed to that preset's model option). An empty list, an entry that is not a table, a missing or empty `id`, or a key other than `id` and `model` is invalid. A top-level `model` key beside this list is invalid. |
| `persona` | no | Inline text, or a path to a Markdown file. |
| `worktree` | no, default `false` | Give each seat its own worktree and branch. |
| `permission` | no, default `auto` | `ask` or `auto`, mapped to the preset's flags. |

A role with one CLI is a list of one. `init` and `roster add` without `--from` write one entry and omit `model` when there is none. `model` omitted on an entry is empty.

### 7.3 Harness preset

| Field | Meaning |
|---|---|
| `id` | Preset name. |
| `command` | Launch argv. Placeholders: `{model}`, `{prompt}`, `{session_id}`. |
| `permission.ask`, `permission.auto` | Extra argv for each mode. |
| `resume` | Optional resume argv, using `{session_id}`. |
| `session_id` | `generated` (Foil creates a UUID and passes it at launch) or `none`. |
| `env` | Names of further environment variables to forward. A named variable takes its value from the tmux server's environment. `PATH` comes from the spawning caller. `PATH` and `HOME` are always forwarded (F26). |

Built-in presets use each CLI's documented flags. A flag that could not
be verified is marked in the preset file.

### 7.4 Board files

**Mail:** Markdown with front matter `contract: mail/v1`, `from`, `to`,
`time` (UTC, ISO 8601), and an optional `re` (the mail file it answers),
followed by the body. Written atomically; file names never collide.

**Contracts:**

| Contract | Front matter |
|---|---|
| `status/v1` | `state: working \| blocked \| done`, `updated`, `questions` (list, may be empty) |
| `task/v1` | `id`, `owner`, `state: open \| doing \| done`, `acceptance` |
| `result/v1` | `task`, `author`, `branch` (if any), `outcome: pass \| fail` |

**Checklist.** Once the lead has a plan, the `status/v1` body holds a
`## Checklist` of Markdown task items, one per line: `- [ ]` open,
`- [x]` done. An item starts with its task id when it has one.
`state: done` means every item is ticked. The front matter is unchanged.
Foil parses front matter only to print a file and does not act on contract fields (F16).

### 7.5 Foil-owned state

**Memory lesson** (schema-versioned): id, text, proposer, time, state
(`proposed`, `accepted`, `rejected`, `superseded`), reviewer, and an
optional `replaces`.

**Registry** (schema-versioned JSON): fleet id, tmux session, lead name,
and per seat: name, template, harness, model, tmux window id, state, worktree
path, branch, and session id. `harness` and `model` on a seat are the pair recorded at that seat's last launch. Resume keeps them (F9). A record written without `model` loads that field as empty. A new seat's entry is chosen from the seat records already stored for that role (F3). The schema version does not change.

## 8. Skills

| Skill | Reader | Must cover |
|---|---|---|
| `operator` | The human's harness, which is a coding agent | The workflow: install, `init`, read the report `init` prints, set up the roster, spawn the lead with the human's goal, then let the fleet work. The pointer line and the first-run check (Onboarding). Managing role templates: the harness list (one ordered list of entries, each an id and an optional model), where personas live, adding a candidate role, and changing the one entry with `roster update` when the list has one entry. A longer list is edited in the template file. A spawn takes the entry at the count of seats already stored for that role (F3). Removing a role fails while a seat of it is not killed. Changing the list while a seat is alive is allowed; that seat keeps its launch pair, and resume uses the pair (F9, F10). A few principles for picking a harness and a model: match the model to the work, keep the reviewer off the implementer's harness whenever at least two eligible harnesses are installed, give the roles that read the most the largest context, and pair `auto` with a worktree. Trust is once per harness per repository: the operator lists the roster's harness ids and asks the human once per id, again when a role moves to a new id, and includes update dialogs. Checking in every few minutes with `seat list` (name, template, state, worktree, the harness and model recorded at launch, and the template's description), `seat peek lead`, and the lead's `status.md`. A blocked seat is a question in that file. The operator does not peek workers to find one. Relaying between the human and the lead with `send`, and nudging a lead that looks stuck. Reviewing memory proposals when asked. Teardown with `seat kill --all`. Upgrading between goals is kill, then upgrade, then `foil init`, then re-copy a user-level operator skill, then spawn the lead. A project that still has an in-repo `.foil/` is killed with the old `foil` first (`foil seat kill --all`). A stale seat whose tmux window is gone also counts. When that old `foil` is no longer on `PATH`, the operator installs 0.3.4 into a separate tool directory, which does not replace the `foil` on `PATH`: `UV_TOOL_DIR=DIR UV_TOOL_BIN_DIR=DIR/bin uv tool install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`. With pipx: `PIPX_HOME=DIR PIPX_BIN_DIR=DIR/bin pipx install "git+https://github.com/TiantianFlow/foil.git@v0.3.4"`, then `DIR/bin/foil seat kill --all`. The refusal names that order when seats remain, and says to run `foil init` when none do. It never says to move the directory aside. A preset `env` value comes from the tmux server. The rule that the operator never does project work. `--task` carries only the goal. Reporting progress to the human from the `status.md` checklist, at each check-in and whenever the human asks, quoting the file rather than the pane. Light familiarity with the goal is untrusted and is not research. The fleet does not follow it. The check-in interval is the operator's own scheduling and is at most 10 minutes, shorter until the first-run check has passed or a dialog has been reported. Each check-in tells the human the file's `state` and `updated`, the checklist as written, the questions, which seats `seat list` shows alive, dead, or killed, and when the next check-in is expected. The operator quotes the file and does not decide from a pane who is working. When a check-in reports a dialog or a seat that is not making progress, the operator runs `tmux ls` and may tell the human `tmux attach -t <session>` for a session that command printed. It does not invent a session name, and it does not add a Foil command or flag. The operator reads the init report before staffing. The first `foil init` with no eligible harness installed exits `foil: no harness installed` and writes no project folder. The operator stops and does not spawn. The human installs a harness CLI and the operator runs `foil init` again. A user preset can be added only after that folder exists. If a later report lists no installed eligible harness, the operator stops and does not spawn. The human adds a user preset at `<project folder>/harnesses/<id>.toml` in the format of section 7.3 and the operator re-runs `init` (F23, F27). A dead first-run lead is cleared with `foil seat kill lead` before the check is spawned again. A user preset that reuses a built-in id replaces that built-in. The operator does not invent preset fields. The operator does not reorder `init`'s harness assignment. It changes the one harness entry afterwards with `roster update` (F28). A longer list is edited in the template file. A spawn takes the entry at the count of seats already stored for that role (F3). That choice is not usage balancing (section 11). If a persona is missing or is the wrong one for the goal, the operator writes or adapts `<project folder>/templates/personas/<role>.md`, then `foil roster add ROLE` once the file exists, or `foil roster update ROLE persona=VALUE` when the template already exists (F20, F21, F22). Adapting is a new file or a file edited by hand, not a second `init`. `init` never overwrites an existing template or persona. `roster add` without `--from` uses the first installed harness, `worktree = false`, and `permission = auto`. The operator may adapt text from an outside catalog, including a localized pack. The catalog is a pointer. The fleet does not clone it and does not vendor it. An omitted permission is `auto`, and `roster add` without `--from` writes `auto`. `ask` is the explicit choice. The operator, outside the fleet, may set `permission` before the first spawn and again later. The next spawn picks it up. A live seat does not. Pair `auto` with a worktree stays. The operator may send keystrokes only for an approval dialog: a folder-trust prompt, a login prompt, or a prompt that blocks the seat until a human or the operator approves it. The operator does not send keys for anything else. An update dialog, a first-run dialog, and an opt-in are reported to the human and not answered. Foil does not scrape or interpret the pane to decide that the dialog is there. A prompt the operator will not approve is reported to the human with the seat name and what the pane shows, and is not answered. The operator refuses a suspicious prompt by not sending the keys and by telling the human. It does not send a denial sequence unless that sequence is the approval dialog's own reject key, and it says so to the human first. The command is `foil roster update ROLE permission=VALUE`. |
| `lead` | The lead seat | Its responsibilities: plan the goal, staff the fleet, delegate, integrate workers' branches, get the result reviewed, keep `status.md` current, and review memory proposals. How to spawn, kill, and resume seats. After each spawn or resume, it waits for that seat's first mail or board file. If none comes, it peeks, answers no dialog, and sets `state: blocked` with a question that names the seat, its harness, and what the pane shows. It sends that seat nothing until the human answers, then kills the stuck seat and spawns a new one. Its read commands: `mail read`, `mail list`, `board read`, `board list`. That implementation work belongs in seats with worktrees. How to manage the roster with `foil roster list`, `foil roster show`, `foil roster add`, `foil roster update`, and `foil roster remove`. The packaged personas are in the project folder's `templates/personas/`. To add a role whose persona file exists, use `foil roster add`. A role that commits needs `worktree = true`. Board and contract conventions. `--task` carries only the goal. Keeping the `status.md` checklist: one item per task and per lead step, ticked when done, with `state: done` only when every item is ticked. A complex decision is one the lead does not make alone: what is true, what the requirements must say, how the work is structured, whether the result meets that structure, and whether the result matches the requirements. The lead writes each one as a seat's task, waits for that seat's board result, and follows it. The seat is whichever template on the roster fits the ask. Research goes to a researcher. A check against the requirements goes to a verifier when the roster has one, and to a reviewer when it does not. Whether the result meets the design is a reviewer's ask. That review is adversarial. The reviewer tries to break the result, looking for where it contradicts the requirements or the design, and does not stop at the happy path. The review is of the whole result, before the design is accepted and again before the goal is reported done. If the operator has already added another persona for the same job, the lead may give the ask to that seat. The reviewer is the seat when no such persona exists. No role is added. The lead does not grade its own coordination. The lead skill says "a caller outside the fleet" where this row says operator. If a later finding shows an earlier stage was wrong, the next task goes back to that stage. The lead does not open a task whose only job is to patch the previous result. Seats run side by side only on work the board has already split into independent tasks, each with acceptance. A researcher and an implementer may run together when the item being implemented is already written down. Several implementers may run when the tasks do not share a file. When the ask needs a role the roster does not have, the lead uses the commands it already has. `foil roster add ROLE` creates a template when `personas/ROLE.md` is already on disk. `foil roster update ROLE model=VALUE` changes the model on a role whose harness list has one entry, so a researcher or reviewer can be given a stronger model for that ask. `harness` and `model` are refused when the list has more than one entry; that list is edited in the template file. Neither command sets `permission`. A role the lead adds arrives as `auto`. The lead cannot turn it back to `ask`. The lead and every worker still do not answer a dialog and do not type into a pane. A new seat takes the entry at the count of seats already stored for that role, including killed seats. A seat already alive keeps the harness and model from its last launch, and resume uses that pair. Removing a role fails while a seat of it is not killed. Changing the list while a seat is alive is allowed. If no persona file exists for the role, the lead asks the human, through `status.md` `questions`, to add the persona or to answer the decision. The lead does not write the persona, does not set `permission`, and does not invent a template name that has no file. The operator, who is outside the fleet, may write `<project folder>/templates/personas/<role>.md` and run `foil roster add`. The lead waits. The lead skill says "a caller outside the fleet" for that same sentence, because the lead skill must not contain the word operator. |
| `worker` | Every non-lead seat | Its responsibilities: do the assigned task, stay in its own worktree, report results to the lead. Its commands: `send`, `mail read`, `mail list`, `board read`, `board list`, `seat list`, `seat peek`, `memory add`, `memory list`. `seat list` shows the harness and model recorded at launch and the template's description. Board and contract conventions. That it may not spawn or kill. The worker does the task it was given. It reports `outcome: fail` and stops, without editing, only when the task itself says the job is a local patch on an earlier result and names no new acceptance. The worker does not decide that an earlier stage was wrong. |

The operator skill is the file `init` writes at `skills/operator.md`. The lead and worker skills are not written there. A seat's instruction file inlines the packaged copy. An older `skills/lead.md` or `skills/worker.md` left from an earlier version is not deleted.

## 9. Documentation

| ID | Requirement |
|---|---|
| D1 | The README sells the product: a one-line value proposition, the workflow in a diagram, a quick start, and a short comparison with doing the same work in one agent session. The quick start is the sentence the human gives their coding agent (Onboarding); the by-hand commands stay as a second path. A demo recording is optional. |
| D2 | The README states the limits plainly: cooperative-only authority (F5), the risk of `auto` permission, and that Foil never interprets agent screens. An omitted permission runs the seat unattended. The screen sentence follows the F12 replacement: Foil does not scrape a pane, does not decide that a dialog is present, and does not send the keys. |
| D3 | A Chinese README carries the same content as the English one. |
| D4 | This is the only requirements document. Other documents describe design, plans, or how to contribute, and never add requirements. |
| D5 | An architecture document describes Foil's components, data flow, and module boundaries as implemented. |
| D6 | Each release has a plan named `plan-vX.Y.Z.md` and an entry in the changelog. |
| D7 | A contributor guide explains setup, the checks CI runs, and the rules every change keeps: the command surface (section 6), the size target (N4), and publishing safety (N9). |
| D8 | A documentation index lists every document in the docs folder, and an automated test checks that none is missing. Documents describe the current state; earlier versions live in git history and at release tags. |

## 10. Verification

Principle: **real Foil, real tmux, real git, a small real task; only the
agent is replaced.** Tests check real outcomes (tests pass, a branch
exists, a window is gone), not Foil's reports about itself.

**Fake harness** (the `fake` preset) is a test double for an agent CLI:

- It runs interactively in its pane and reads typed lines, like a real TUI.
- When a nudge arrives, it reads the mail file it names.
- It follows a per-seat script, in a format the standard library can
  parse. Reactions include running a shell command (for example
  `foil seat spawn`, `git commit`, the fixture's test runner), writing a
  board file, and `foil send`.
- On start it handles any mail it hasn't handled yet, so restarts work.
- It keeps a debug log; tests never assert on it.
- It launches through the normal preset path, so templates, launch,
  environment, instruction files, worktrees, and nudges are exercised
  exactly as with a real CLI.

**Required scenarios** (each is a fixture repository plus seat scripts;
the test plays the operator):

| # | Scenario | Assert |
|---|---|---|
| 1 | Happy path: the fixture has one failing test. The test runs `init` and `seat spawn lead --task "make the tests pass"`. The lead spawns an implementer and a reviewer; the implementer commits a fix on its branch; the reviewer runs the tests there and approves; the lead merges and sets `status.md` to done. | Tests pass on the merged result; the branch is merged; `status.md` says done; `seat kill --all` leaves no Foil windows. |
| 2 | Authority: a worker tries `seat spawn` and `seat kill lead`. | Both fail; the fleet is unchanged. |
| 3 | Crash: kill the tmux server mid-scenario, then `seat resume`. | Scenario 1's outcome is still reached. |
| 4 | Operator loop: the lead writes a question to `status.md`; the test answers with `foil send lead`. | The lead's final status reflects the answer; `seat peek lead` shows raw pane text. |
| 5 | Memory: a worker proposes a lesson and fails to accept it; the lead accepts it. | A seat spawned afterwards has the lesson in its instruction file; `memory list` shows it accepted. |
| 6 | Errors: send to an unknown seat, spawn a missing template, run outside a git repository. | Each gives a one-line error and a non-zero exit, with no traceback. |

**Standing checks:** the command surface matches section 6 exactly; the
package source size is reported against the N4 target; tmux window identity, atomic
writes, public-safety checks (N9), and the documentation index check (D8)
pass.

**Live tier (optional):** the same scenarios with real harness CLIs in
place of `fake`, judged by the same assertions. It needs authenticated
CLIs, so it's opt-in and excluded from the default test run.

## 11. Out of scope

- Any command, subcommand, or flag not in section 6. Changing the command
  surface requires changing this document first.
- Parsing or interpreting pane contents. The only exception is the operator, bounded by the F12 replacement. Foil still does not do it.
- More than one fleet per project, remote machines, and Windows. The store has one folder per project, which could hold another fleet later. This release still runs one fleet per project.
- A UI, a server, MCP, or a hosted service.
- Message read receipts, usage-based dispatch, and persona catalog tools beyond `roster list` of the personas `init` copied. Usage-based dispatch includes quota detection, probes, pacing, credential rotation, and switching a running seat because it looks exhausted. Spawn-time rotation across the ordered harness list on a role is in scope. A spawn selects `entries[n % len(entries)]`, where `n` is the number of seat records already stored for that role (F3). That spreads later worker spawns across the entries the role was given. It does not balance usage and it does not move a seat that is already running.
