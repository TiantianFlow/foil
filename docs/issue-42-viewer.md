# Issue #42: technical design for `foil view`

Issue #42 asks for a UI to view settings and fleets. The visual spec is
the Magic Patterns design "Foil fleet viewer" linked from the issue.
The behavior spec is the "Latest requirements" text agreed for the
issue. This document answers how the viewer is built and run. It adds no
requirement. Section 10 lists the requirement text the change would land
in [requirements.md](requirements.md).

Read against `main` at 0.5.0, where the layout of PR #45 has landed:
`harnesses.toml`, `roles.toml`, `mapping.toml`, and `fleets/<id>/` with
`overrides.toml`, `board/`, and `run/`.

## Decisions to make

| # | Question | Recommendation |
|---|---|---|
| 1 | Same repository or a parallel one | Same repository, same package, one module |
| 2 | Stack | Standard library `http.server`, HTML rendered on the server, inline CSS, a few lines of inline script |
| 3 | How it runs | `foil view [--port N]` in the foreground, one process per project. Default port 7460, then the next free one, as Jupyter does. An explicit `--port` binds that port or fails |
| 4 | Daemon | No daemon mode. launchd or `systemd --user` supervises the foreground process. The README and the operator skill give the steps. Foil writes no unit file |
| 5 | Loopback | The common pattern: bind `127.0.0.1` only, refuse a foreign `Host` or `Origin`, strict CSP, no auth on loopback |
| 6 | Other local users | Accept and document, as opencode, Syncthing, and Ollama do. Optional basic auth is the later step if it is needed |
| 7 | Large files | A 1 MiB preview plus a Raw link that streams the whole file. The pane gets a More link |

The rest of this document gives the reasons.

## 1. Same repository or a parallel one

The agreed text says that a change to the lists, the overrides, memory,
the board, the registry, `seat list`, `peek`, `post`, or the session name
updates the viewer **in the same change**. Only one repository can keep
that promise.

| | Same repository, a new module, `foil.view` | Parallel repository |
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
| `/fleets/<id>/raw?open=<path>` | One listed board file, whole, as plain text (section 6) |

## 3. How it runs

- `foil view [--port N]`. Without `--port` it tries 7460, then each
  next port up to 7509, and binds the first free one, the way Jupyter
  starts at 8888. A second project's viewer lands on the next port.
  With `--port N` it binds exactly that port or exits 1 with
  `foil: port N is in use` (one line, N8). It does not move: a
  supervisor (section 4) needs the address to stay put. Fifty busy
  ports is one line too:
  `foil: no free port in 7460-7509`.
- It resolves the project the way every command does: `FOIL_PROJECT`,
  otherwise the Git toplevel of the working directory. A supervised
  viewer gets that from its working directory.
- With `FOIL_SEAT_ID` set, it prints
  `foil: view is outside the fleet only`, exits 1, and binds nothing.
  That check comes first.
- It prints the address (the `http` scheme, then `127.0.0.1:<port>`) as
  its only stdout line, flushed, so a script that reads one line works,
  and so does a supervisor's log.
- The server handles one request at a time and has a 10 s socket
  timeout, so a stuck client cannot hold it. Each page costs one tmux
  probe per seat, plus one capture on a seat page.
- Ctrl-C and SIGTERM exit 0 with no output. A failure is one line on
  stderr and exit 1 (N8), which a supervisor restarts after its
  throttle interval.
- No PID file, no lock, no state, no access log. Two viewers on one
  project are two ports and do not interfere. Stopping either leaves
  seats, windows, the registry, and the board unchanged.
- The page does not refresh itself. A reload reads the files again.
  Nothing is cached between requests. A viewer started before tmux or
  before any fleet shows "no session yet" until a reload finds one.

## 4. Running it as a service

Your reading is right. launchd on macOS and `systemd --user` on Linux
run an ordinary foreground program, start it at login, and restart it
when it exits. The program needs no daemon mode. It needs four things,
and `foil view` has them once `--port` exists:

| The supervisor needs | `foil view` |
|---|---|
| A fixed address to bookmark | `--port N` |
| A foreground process that does not fork | It serves in the foreground |
| A clean stop on SIGTERM | Exits 0 |
| A non-zero exit on failure, so it is retried | One stderr line, exit 1 |

Foil does not write the unit file. Writing one is a file outside the
store and would need its own command. The README gives the steps, and
the operator skill gets an onboarding-style step: when the human asks
for the viewer to keep running, the agent fills in the repository path,
the port, and the absolute `foil` path, shows the file, and runs the
load command only after the human agrees. One service per project; give
each project its own port.

Three details decide whether a supervised viewer works:

- **`PATH`.** launchd starts agents with `/usr/bin:/bin:/usr/sbin:/sbin`.
  Homebrew's `tmux` (`/opt/homebrew/bin`) is not on it, so
  `foil view` would exit `foil: tmux is not installed`, and the
  Harnesses page would call every CLI uninstalled. The unit sets `PATH`
  to the human's shell `PATH`, read once with `echo $PATH`.
- **The store.** If the human sets `FOIL_HOME` or `XDG_DATA_HOME`, the
  unit sets the same value, or the viewer finds a different store.
- **tmux.** The service runs as the same user, so it reaches the same
  tmux server socket. It must not set a different `TMUX_TMPDIR`.

macOS, `~/Library/LaunchAgents/dev.foil.view.<project>.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>dev.foil.view.PROJECT</string>
  <key>ProgramArguments</key><array>
    <string>/ABSOLUTE/PATH/TO/foil</string><string>view</string>
    <string>--port</string><string>PORT</string>
  </array>
  <key>WorkingDirectory</key><string>/ABSOLUTE/PATH/TO/REPO</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>OUTPUT OF echo $PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/tmp/foil-view-PROJECT.log</string>
</dict></plist>
```

Load with `launchctl bootstrap gui/$(id -u) <plist>`. Stop with
`launchctl bootout gui/$(id -u)/dev.foil.view.PROJECT`.

Linux, `~/.config/systemd/user/foil-view-<project>.service`:

```ini
[Unit]
Description=Foil viewer for PROJECT

[Service]
ExecStart=/ABSOLUTE/PATH/TO/foil view --port PORT
WorkingDirectory=/ABSOLUTE/PATH/TO/REPO
Environment=PATH=OUTPUT_OF_echo_$PATH
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
```

Load with `systemctl --user enable --now foil-view-<project>`. Stop with
`systemctl --user disable --now foil-view-<project>`. The log is
`journalctl --user -u foil-view-<project>`. Without
`loginctl enable-linger`, it runs only while the user is logged in,
which is what a viewer wants.

The log holds the one stdout line and any one-line error. The viewer
writes no access log, so it records no paths and no pane text.

A machine-wide viewer for every project stays out: the agreed text
opens each repository by running the viewer from it.

## 5. Loopback, and who can reach it

How other local web UIs do it:

| App | Bind | Port | Host check | Auth by default |
|---|---|---|---|---|
| Jupyter Server | 127.0.0.1 | 8888, then the next free port | Yes. A non-local `Host` gets 403, against DNS rebinding | Token in the printed URL |
| opencode `serve` / `web` | 127.0.0.1 | `--port` | — | None. `OPENCODE_SERVER_PASSWORD` turns on basic auth, and binding `0.0.0.0` calls for it |
| Syncthing GUI | 127.0.0.1:8384 | config | Yes, since 0.14.6, against DNS rebinding | None. A GUI password is optional |
| Ollama API | 127.0.0.1:11434 | `OLLAMA_HOST` | Origin allowlist (`OLLAMA_ORIGINS`) | None |
| "deepseek-harness" `dsh web` | 127.0.0.1:3080 | — | — | — (one third-party guide; it refuses `0.0.0.0`; not confirmed from an official source) |

The common pattern is: bind loopback only, refuse a foreign `Host` or
`Origin`, and ship no auth on loopback, with auth optional. Jupyter is
the exception with a token by default, because a notebook runs code.
`foil view` runs nothing and types nothing, so it follows the common
pattern.

What the server does:

- Binds `127.0.0.1` only. Not `0.0.0.0`, not `::`, not `localhost`,
  which may resolve to `::1` or something else. There is no flag to
  bind anything else.
- **Host.** A request whose `Host` is not `127.0.0.1:<port>` gets 403
  and an empty body, the same rule as Jupyter and Syncthing. A page on
  `evil.example` that rebinds its name to `127.0.0.1` makes requests the
  browser counts as same-origin. A same-origin GET carries no `Origin`,
  but it does carry `Host: evil.example:<port>`, and a page cannot set
  `Host`.
- **Origin.** A request whose `Origin` is anything but the viewer's own,
  including `null`, gets the same empty 403. A navigation with no
  `Origin` loads the page.
- Every error response (bad method, bad request line) is an empty body.
  `send_error` is replaced so the standard error page is never sent.
- Headers: `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'; script-src 'nonce-<random>'; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'`, `Cache-Control:
  no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`. The page cannot load anything from another host, even by
  mistake. The favicon is `data:,`.
- Pane text, board files, and persona lines are written by agents, so
  the page treats them as hostile. Each value is escaped once, where it
  becomes HTML. There are no inline event handlers. `capture-pane` runs
  without `-e`, so the pane is plain text.

Who can still read it:

- **The same user, including seats.** They can already read the same
  files in the store. Refusing `foil view` inside the fleet keeps a seat
  from opening a page that lists every fleet. It does not stop a seat
  that wants to `curl` a running viewer (F5: cooperative, not hostile).
- **Other users on the same machine.** The store is mode 0700, and
  loopback TCP is open to every local user. This is the same exposure
  opencode, Syncthing, and Ollama accept by default. The README says it
  in one sentence, next to the service steps, because a service makes
  the viewer run all day. If shared machines matter later, the common
  next step is opencode's: an environment variable that turns on basic
  auth. That fits a unit file (`Environment=`) better than a token in a
  URL that changes each run.

## 6. Large files and long panes

The rule is that the full file can always be seen. The common approach
for a file viewer (GitHub and GitLab blobs, Jenkins console logs) is a
size-limited preview plus a link to the raw file, not infinite scroll:

- An opened board file renders its first 1 MiB as escaped HTML. When the
  file is larger, a line under it says `Showing the first 1 MiB of
  <size>.` beside a **Raw** link.
- Every opened file has the **Raw** link:
  `/fleets/<id>/raw?open=<path>`. It streams the whole file as
  `text/plain; charset=utf-8` with `nosniff` and
  `Content-Security-Policy: sandbox`, so the browser shows any size,
  searches it with Ctrl-F, and never runs it. The same listing rule
  applies: only a path from the board listing is served.
- The listing shows each file's size from `stat`, not a line count, so a
  page load does not read every mail file.
- The pane shows 40 lines, as `seat peek` does. A **More** link reloads
  the page with `?lines=` doubled, up to tmux's history limit. That is
  `peek --lines N`, not a new reading of the pane.

Infinite or lazy loading would need a byte-range endpoint and script on
the page. The raw link gives the full file with neither, so this
version does not do it.

## 7. Reading the store

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
  Section 6 covers size.
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

## 8. What the design does not cover

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

## 9. Tests

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
- Ports: with 7460 held, the default binds 7461 and prints it; an
  explicit busy `--port` is one stderr line and exit 1; SIGTERM exits 0.
- A file over 1 MiB: the preview is cut with the size line, and Raw
  returns every byte with `text/plain` and `sandbox`.
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
`--port N` and no positionals. The size report shows the new count.

## 10. Requirement text this change lands

- **Section 6.1:** a row `foil view` | `--port N` (default: the first free port from 7460)
  | serves the read-only page (F34). The count becomes nine top-level
  commands and twenty-three actions.
- **Section 6.2:** a `view` column: outside the fleet yes, lead no,
  worker no.
- **F34 (new, under Visibility):** the agreed text, condensed, with
  these changes. `--port N` binds that port or fails with one line; it
  is the only flag. The `Host` rule joins the `Origin` rule. An opened
  file shows a 1 MiB preview and a raw view of the whole file. "No
  flags" becomes "one flag".
- **"Keep the screen in step":** one sentence in F34 or in CONTRIBUTING
  "Rules every change keeps".
- **Section 11:** "A UI, a server, MCP, or a hosted service" becomes "A
  UI or server other than `foil view`, a viewer that writes or types into
  a pane, a daemon mode or a command that installs a service, MCP, or a
  hosted service."
- **N4:** no text change. The pull request states the reason.
- **Documentation:** a README section with the launchd and systemd
  steps and the shared-machine sentence (and D3, the Chinese README); an
  operator-skill step that fills in the unit file for the human and loads
  it only after they agree; a row in [architecture.md](architecture.md);
  and a CHANGELOG entry.

## 11. Settled by common practice

1. Default port: a fixed default with fallback to the next free port,
   as Jupyter does. An explicit `--port` does not fall back.
2. Shared machines: documented, with no auth on loopback, as opencode,
   Syncthing, and Ollama do. Optional basic auth through an environment
   variable is the later step if it is needed.
