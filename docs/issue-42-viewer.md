# Issue #42: technical design for `foil view`

Issue #42 asks for a UI to view settings and fleets. The visual spec is
the Magic Patterns design "Foil fleet viewer" linked from the issue.
The behavior spec is the "Latest requirements" text agreed for the
issue. This document answers how the viewer is built and run. It adds no
requirement. Section 9 lists the requirement text the change would land
in [requirements.md](requirements.md).

Read against `main` at 0.5.0, where the layout of PR #45 has landed:
`harnesses.toml`, `roles.toml`, `mapping.toml`, and `fleets/<id>/` with
`overrides.toml`, `board/`, and `run/`.

## Decisions to make

| # | Question | Recommendation |
|---|---|---|
| 1 | Same repository or a parallel one | Same repository, same package, one module |
| 2 | Stack | Standard library `http.server`, HTML rendered on the server, inline CSS, a few lines of inline script |
| 3 | How it runs | `foil view` in the foreground, one process per project, an ephemeral port, stopped with Ctrl-C |
| 4 | Daemon | Not in this version. No unit file, no `--port`, no machine-wide service |
| 5 | Loopback only | Bind `127.0.0.1` only. Check `Host` and `Origin`. Strict CSP |
| 6 | Other local users | Decide: accept and document it, or add a URL token (this changes the agreed text) |
| 7 | Large board files | Show the first 1 MiB and say when a file is cut |

The rest of this document gives the reasons.

## 1. Same repository or a parallel one

The agreed text says that a change to the lists, the overrides, memory,
the board, the registry, `seat list`, `peek`, `post`, or the session name
updates the viewer **in the same change**. Only one repository can keep
that promise.

| | Same repository, `src/foil/view.py` | Parallel repository |
|---|---|---|
| Reading the store | Calls the readers the commands already use: `seat_rows`, `peek_text`, `roster_rows`, `read_lessons`, `fleet_overrides`, `read_maps`, `load_preset` | Imports Foil internals, which are not a stable API, or parses the files again and drifts |
| Staying in step | A format change breaks the viewer's tests in the same pull request | A format change ships and the viewer finds out later |
| Versions | One version. `foil --version` covers it | Two versions and a compatibility matrix against the registry schema (N7) |
| Size (N4) | Adds about 550 lines to a package that is already 4,845 lines, against a 2,500 target | Core stays the same size |
| Scope (section 11) | Section 11 must change: it lists "a UI, a server" as out of scope | Section 11 can stay, but the viewer then needs its own requirements |

A third option, a second distribution built from the same repository
(`foil-view`, depending on `foil-orchestrator==X.Y.Z`), keeps the
readers shared and the core wheel smaller. Foil has no runtime
dependencies, so this saves almost nothing. It doubles the release steps.

**Recommendation:** same repository, one module. State the N4 reason in
the pull request: the viewer reuses the command readers so that it cannot
drift from them, and that is worth the lines.

## 2. Stack

| Option | For | Against |
|---|---|---|
| **A. `http.server` and HTML rendered on the server** | Standard library only (N1). No build step. One language. Every value goes through `html.escape` in one place. The page works with scripts off, except the copy button | The page is plain. The popovers are `<details>` elements, not floating panels |
| B. Build the Magic Patterns React and Tailwind app. Ship the bundle in the package and serve a JSON API | Matches the design pixel for pixel | Needs Node and a lockfile in the repository and in CI. A built bundle of several hundred KB in a Python wheel. Two codebases describe one screen. The design's Google Fonts import must go, because the page may request nothing from another host |
| C. A Python web framework (Flask, FastAPI) | Familiar | Breaks N1 |
| D. A terminal UI (curses) | No browser, no port | Not what the issue asks for. Pane text inside a TUI is awkward |

**Recommendation:** A. The design is the layout and the words, not the
React code. Its tokens (neutral greys, a 6 px radius, 13 px monospace,
green, grey, and red state dots) are about 70 lines of CSS. System fonts
replace Geist. Dark mode follows `prefers-color-scheme`.

The page has four routes:

| Route | Screen |
|---|---|
| `/` | Fleets list, the help line, Lessons |
| `/settings` | Roles, Harnesses, Default mapping |
| `/fleets/<id>` | Attach, Workspace, Overrides, Agents, Board, Posts. `?open=<path>` opens one file |
| `/fleets/<id>/seats/<name>` | Seat facts, Attach, Pane |

## 3. How it runs

- `foil view` takes no flags. It resolves the project the way every
  command does: `FOIL_PROJECT`, otherwise the Git toplevel of the
  working directory.
- With `FOIL_SEAT_ID` set, it prints
  `foil: view is outside the fleet only`, exits 1, and binds nothing.
  That check comes first.
- It binds `127.0.0.1:0` and prints the page's address, the `http`
  scheme then `127.0.0.1:<port>`, as its only stdout line, flushed, so `foil view | head -1` and a script that reads
  one line both work.
- The server handles one request at a time and has a 10 s socket
  timeout, so a stuck client cannot hold it. Each page costs one tmux
  probe per seat, plus one capture on a seat page.
- Ctrl-C exits 0 with no output. SIGTERM ends it with no output. A
  failure to bind is one line on stderr and exit 1 (N8).
- No PID file, no lock, no state. Two viewers on one project are two
  ports and do not interfere. Stopping either leaves seats, windows, the
  registry, and the board unchanged, because the viewer never wrote them.
- No access log. `log_message` is replaced with a no-op.
- The page does not refresh itself. A reload reads the files again.
  Nothing is cached between requests.

The port is new on each run, so a bookmark goes stale. That is the price
of having no flags, and section 4 accepts it.

## 4. Running it as a daemon

| Option | What it takes | Problems |
|---|---|---|
| **A. Foreground only** (recommended) | Nothing. Run it in a spare terminal or a tmux window of your own | You start it when you want it. The URL changes each run |
| B. A user service per project (`systemd --user`, a launchd agent) | A fixed port, so a flag (`--port`) or a config file, which section 6 does not allow today. Foil, or the user, writes a unit file. The unit needs `WorkingDirectory=<repo>` and the same `FOIL_HOME` or `XDG_DATA_HOME` | A service gets a different `PATH`, so the "Installed" column would describe the service, not your shell. Writing a unit file breaks "the viewer writes no file" unless it is a separate command. It stays running while nobody looks, which is more exposure (section 5) |
| C. One machine-wide daemon for every project | A project picker and a list of projects | Contradicts the agreed text: each repository is opened by running the viewer from it. Lists every project to anyone who can reach the port |

The tmux side works in every option: the viewer runs as the same user,
so it reaches the same tmux server socket.

**Recommendation:** A for this version. If a long-running viewer is
wanted later, write B up as its own requirement: a `--port` flag, a
documented unit example in the README rather than a command that writes
one, and an answer to question 6 first, because a viewer that is always
running makes the multi-user question matter more.

## 5. Loopback only, and who can reach it

What the server does:

- Binds `127.0.0.1` only. Not `0.0.0.0`, not `::`, not `localhost`, which
  may resolve to `::1` or something else.
- **Origin.** A request whose `Origin` is anything but
  the viewer's own (`http` scheme, `127.0.0.1:<port>`), including `null`, gets status 403 and an
  empty body. A navigation with no `Origin` loads the page.
- **Host.** A request whose `Host` is not `127.0.0.1:<port>` gets the
  same empty 403. This blocks DNS rebinding: a page on `evil.example`
  that rebinds its name to `127.0.0.1` makes requests the browser counts
  as same-origin. A same-origin GET carries no `Origin` header, so the
  `Origin` check alone would let it read pane text. Such a request still
  carries `Host: evil.example:<port>`.
- Every error response (bad method, bad request line) is an empty body.
  `send_error` is replaced so the standard error page is never sent.
- Headers: `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'; script-src 'nonce-<random>'; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'`, `Cache-Control:
  no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`. With `default-src 'none'` the page cannot load anything
  from another host, even by mistake. The favicon is `data:,`, so the
  browser does not ask for `/favicon.ico`.
- Pane text, board files, and persona lines are written by agents, so
  the page treats them as hostile. Each value is escaped once, at the
  point where it becomes HTML. There are no inline event handlers.
  `capture-pane` runs without `-e`, so the pane is plain text with no
  escape sequences.

Who this leaves able to read the page:

- **The same user, including seats.** A seat can `curl` a running
  viewer and read every fleet's board. It can already read the same
  files from the store, which it owns. Refusing `foil view` inside the
  fleet keeps a seat from opening a page that lists every fleet. It does
  not stop a seat that wants to look (F5: cooperative, not hostile).
- **Other local users.** This is new. The store is mode 0700 (section
  7.1), so other users cannot read it today. Loopback TCP is open to
  every user on the machine, and the agreed text says the URL carries no
  token. On a shared machine, anyone logged in can read pane text,
  checkout paths, and board bodies while the viewer runs.

Question 6 options:

| | Change | Cost |
|---|---|---|
| a. Accept and document it | README: "On a machine other people log in to, anyone there can read the viewer while it runs." | None. Matches the agreed text |
| b. Token in the URL | Print the address with a `/<token>/` path. A request without that prefix gets the empty 403. 128 random bits, new each run | About 10 lines. The URL is a secret in shell history and the browser history. Changes the agreed text ("carries no token") |
| c. Check the peer's user id | Not portable. TCP has no `SO_PEERCRED`. Reading `/proc/net/tcp` works only on Linux | Rejected |
| d. A Unix socket | Browsers cannot open one | Rejected |

**Recommendation:** b if Foil is used on shared machines; otherwise a.
This is a product decision, so it is listed as a question.

## 6. Reading the store

- **Reuse, not reimplement.** The viewer calls the functions the
  commands call. Five small refactors split the printing from the
  reading, with output unchanged:
  `seat_rows` from `list_seats`, `peek_text` from `peek_seat`,
  `roster_rows` from `list_roster`, `read_lessons` from `list_lessons`,
  and `preset_ids` from `installed_presets`.
- **Choosing a fleet.** `fleet_dir()` reads `FOIL_FLEET` from the
  environment. The viewer must read every fleet in one process. Pass the
  fleet id as an optional argument through `fleet_dir`, `registry_path`,
  `load_registry`, `fleet_overrides`, `seat_rows`, and `peek_text`. That
  is cleaner than setting `os.environ` around each read, which only works
  because the server is single-threaded.
- **Fleets** are the directories under `fleets/` whose names are safe
  ids and that are not symlinks. An unreadable registry or override file
  is one red line in that fleet's row or section. The other fleets, and
  the rest of that page, still render.
- **Settings.** Roles are `roster_rows`, so the description follows the
  same rule as `roster list`, and a persona with no role is listed as
  `+ <name>`. Harnesses are every eligible id from `preset_ids`: id,
  "installed" from `shutil.which(command[0])`, the model ids, `command`
  joined with spaces and placeholders as written, and the `env` names.
  Values are never read. `fake` is left out. A list file that does not
  load is named under the table. The default mapping is one table row per
  `[[map]]` row, in file order.
- **Overrides** show the last row for each role: role, next-spawn model,
  and the note column when any row has a note.
- **Next-spawn model.** For a seat's role, the line appears when the
  last override's model differs from the model of
  `entries[n % len(entries)]`, where `n` is that role's stored seat
  count. That is the model spawn would pick without the override (F3).
- **Attach.** `tmux attach -t <tmux_session>` from the registry, quoted
  with `shlex.quote`. An empty session says the fleet has not started.
  A seat with a window id adds `\; select-window -t <window id>`. Nothing
  is derived from the directory name.
- **Board and posts.** Walk `fleets/<id>/board/` without following
  symlinks. A symlinked file or directory is skipped. `room/` is Posts,
  everything else is Board, and `status.md` comes first. A file is
  opened only when `?open=` names a path **from that listing**, so a
  path from the URL never reaches the filesystem and `..` is impossible
  by construction. The bytes are decoded as UTF-8 with replacement.
- **Pane.** `peek_text(name, lines=40)`. A `FoilError` is shown as its
  one line, with no pane text. The capture is not stored, logged, or
  classified.
- **Writes nothing.** None of the readers above creates a file. The
  viewer never calls `ensure_board`, `ensure_registry`, or a function
  that takes the registry lock. A test enforces this (section 8).

Every value the page shows is looked up again on each request.
`foil_root()` runs `git rev-parse` on each call, so a request should
resolve it once and pass the folder down. That saves tens of git
processes per page.

## 7. What the design does not cover

The design is ready to build from. These states are missing from it,
and the agreed text decides each one:

| Gap in the design | Resolution from the agreed text |
|---|---|
| The seat page shows "not found" when there is no capture | Every seat opens. A killed seat, or a dead one whose window is gone, shows peek's one-line error |
| Attach always adds `select-window` | No window id: the fleet command alone. No session: "no session yet" |
| No next-spawn model on the seat page | Shown beside the recorded pair |
| The override type carries a harness | Not shown. Overrides replace the model only |
| No note column on overrides | Added when any row has a note |
| One model per role in the default mapping | One row per `[[map]]` row, each with its own model, in order |
| No persona-only roles | Listed as `+ <name>` |
| No unreadable-fleet state | One error line in that fleet's row |
| Google Fonts import | Removed. System fonts |

## 8. Tests

Unit tests use a hand-built project and a stand-in tmux, the same way
`tests/test_room_post.py` does:

- `FOIL_SEAT_ID` set: exit 1, the exact stderr line, and no socket
  opened. Patch `socketserver.TCPServer` to fail if it is constructed.
- Origin: none, own, other, and `null`. Host: own and other. Each
  rejection has an empty body.
- Every page is fetched over a real socket. The stdout line is checked
  first.
- Roles, the persona-only row, an invalid template, harness rows with a
  missing binary and an invalid `harnesses/<id>.toml`, and mapping rows
  in order with per-row models.
- Overrides: none, one, several for one role (the last wins), with a
  note, and an invalid file.
- A next-spawn line that appears and one that does not (an override
  equal to the mapping model).
- Attach with no session, with a session, and with and without a window
  id.
- Board: a symlinked file and a symlinked directory are skipped,
  `?open=../../project.json` and an unlisted path open nothing, and
  hostile text such as `<script>` in a board file or pane is escaped.
- A second fleet with a broken registry leaves the first on screen.
- **Writes nothing:** snapshot every path, mode, and byte under the
  store, crawl every page and every listed file, then compare.

One end-to-end test with real tmux and the `fake` harness: spawn a
lead, load its seat page, check that the pane text matches `seat peek`,
stop the viewer, and check that the window is still there.

Standing checks that change: the command-surface test gains `view`, with
no flags and no positionals. The size report shows the new count.

## 9. Requirement text this change lands

- **Section 6.1:** a row `foil view` | none | serves the read-only page
  (F34). The count becomes nine top-level commands and twenty-three
  actions.
- **Section 6.2:** a `view` column: outside the fleet yes, lead no,
  worker no.
- **F34 (new, under Visibility):** the agreed text, condensed:
  read-only; loopback; no flags; the stdout line; the Origin rule; the
  `Host` rule if accepted; no access log; outside the fleet only; the
  screens; the attach command; the pane or peek error.
- **"Keep the screen in step":** one sentence in F34 or in CONTRIBUTING
  "Rules every change keeps".
- **Section 11:** "A UI, a server, MCP, or a hosted service" becomes "A
  UI or server other than `foil view`, a viewer that writes or types into
  a pane, MCP, or a hosted service."
- **N4:** no text change. The pull request states the reason.
- **Documentation:** a README paragraph (and D3, the Chinese README), a
  row in [architecture.md](architecture.md), and a CHANGELOG entry.

## 10. Open questions

1. Question 6: accept multi-user exposure, or add a token?
2. Keep the `Host` check? It is not in the agreed text, but without it
   DNS rebinding defeats the `Origin` rule.
3. A 1 MiB cap on an opened file, or show it whole?
4. Daemon: confirm it is out of scope for this version.
