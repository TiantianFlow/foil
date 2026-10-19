# Issue #42: technical design for `foil view`

Issue #42 asks for a UI to view settings and fleets. The visual spec is
the Magic Patterns design "Foil fleet viewer" linked from the issue.
The behavior spec is the text agreed on the issue, as amended there:
`--port` and service steps are in scope, the full file must be
viewable, and minor questions follow common practice. This document
answers how the viewer is built and run. It adds no requirement.
Section 10 lists the requirement text the change lands in
[requirements.md](requirements.md).

Read against `main` at 0.5.0, where the layout of PR #45 has landed:
`harnesses.toml`, `roles.toml`, `mapping.toml`, and `fleets/<id>/` with
`overrides.toml`, `board/`, and `run/`.

## Summary

| # | Question | Answer |
|---|---|---|
| 1 | Same repository or a parallel one | Same repository, same package, a new module `foil.view` |
| 2 | Stack | Standard library `socketserver` and `http.server`, threaded. HTML rendered on the server, inline CSS, a few lines of inline script |
| 3 | How it runs | `foil view [--port N]` in the foreground, one process per project. Default port 7460, then the next free one up to 7509. An explicit `--port` binds that port or fails |
| 4 | Service | No daemon mode. launchd or `systemd --user` supervises the foreground process. The README and the operator skill give the steps. Foil writes no unit file |
| 5 | Loopback | Bind `127.0.0.1`. Accept a loopback `Host` only, and an `Origin` only when it matches that `Host`. GET and HEAD only. Strict headers. No auth on loopback, and the exposure to other local users is stated |
| 6 | Large files | A 1 MiB preview plus a Raw view of the whole file. Files are opened without following any symlink |
| 7 | Size | About 800 lines of package Python, CSS included. The N4 reason is stated in the pull request |

## 1. Same repository or a parallel one

The agreed text says that a change to the lists, the overrides, memory,
the board, the registry, `seat list`, `peek`, `post`, or the session name
updates the viewer **in the same change**. Only one repository can keep
that promise.

| | Same repository, a new module `foil.view` | Parallel repository |
|---|---|---|
| Reading the store | Calls the same code the commands call, once five small refactors split reading from printing (section 7) | Imports Foil internals, which are not a stable API, or parses the files again and drifts |
| Staying in step | A format change breaks the viewer's tests in the same pull request | A format change ships and the viewer finds out later |
| Versions | One version. `foil --version` covers it | Two versions and a compatibility matrix against the registry schema (N7) |
| Size (N4) | Adds about 800 lines to a package that is already 4,845 lines, against a 2,500 target | Core stays the same size |
| Scope (section 11) | Section 11 must change: it lists "a UI, a server" as out of scope | Section 11 can stay, but the viewer then needs its own requirements |

A second distribution built from this repository (`foil-view`, pinned
to `foil-orchestrator==X.Y.Z`) shares the code but doubles the release
steps, and Foil has no runtime dependencies to save. It is not worth it.

**Answer:** same repository, one module. The pull request states the N4
reason: the viewer reuses the command readers so that it cannot drift
from them. The CSS stays in the module, so it is counted.

## 2. Stack

| Option | For | Against |
|---|---|---|
| **A. Standard library server, HTML rendered on the server** | Standard library only (N1). No build step. One language. The page works with scripts off, except the copy button | The page is plain. The popovers are `<details>` elements, not floating panels |
| B. Build the Magic Patterns React and Tailwind app, ship the bundle, serve JSON | Matches the design pixel for pixel | Needs Node and a lockfile in the repository and in CI. A built bundle of several hundred KB in a Python wheel. Two codebases describe one screen. The design's Google Fonts import must go, because the page may request nothing from another host |
| C. A Python web framework | Familiar | Breaks N1 |
| D. A terminal UI | No browser, no port | Not what the issue asks for |

**Answer:** A. The design is the layout and the words, not the React
code. Its look (neutral greys, a 6 px radius, 13 px monospace, green,
grey, and red state dots) is about 70 lines of CSS. System fonts
replace Geist. Dark mode follows `prefers-color-scheme`.

The server is `socketserver.ThreadingTCPServer` with
`BaseHTTPRequestHandler` and `daemon_threads = True`. It does not use
`http.server.HTTPServer`, whose `server_bind` runs a reverse DNS lookup
through `socket.getfqdn`. `SO_REUSEADDR` stays on, as `socketserver`
and most servers set it: the server closes each connection first, so
its port sits in TIME_WAIT after a stop, and without the option a
restarted viewer could not rebind its own port for up to a minute. On
Linux the option still refuses a port another socket listens on. On
macOS it may not refuse a port another program holds on all addresses,
so before binding a port the viewer tries a 0.2 s connect to
`127.0.0.1:<port>` and treats an answer as "in use".
A single-threaded server is not enough: a browser keeps idle and
speculative connections open, and one idle connection holds a
single-threaded server for its whole socket timeout, so every real
request waits. Threads remove that. A semaphore caps page work at 8
requests at once. A request takes a slot only after its request line and
headers are parsed, so idle and speculative connections hold none, and
gives it back before a Raw body streams, so a slow Raw reader holds none.
A request over the cap gets an empty 503.

Routes:

| Route | Screen |
|---|---|
| `/` | Fleets list, the help line, Lessons |
| `/settings` | Roles, Harnesses, Default mapping |
| `/fleets/<id>` | Attach, Workspace, Overrides, Agents, Board, Posts. `?open=<path>` opens one file |
| `/fleets/<id>/raw?open=<path>` | One listed board file, whole, as plain text (section 6) |
| `/fleets/<id>/seats/<name>` | Seat facts, Attach, Pane. `?lines=N` sets the pane length |

## 3. How it runs

- **Port.** `foil view [--port N]`. Without `--port` it tries 7460, then
  each next port up to 7509, and binds the first free one, the way
  Jupyter starts at 8888 and moves up. Only `EADDRINUSE` moves to the
  next port; any other bind error is one line and exit 1. With
  `--port N` it binds exactly that port or exits 1 with
  `foil: port N is in use`. It does not move, because a supervisor
  (section 4) needs the address to stay put. `N` must be an integer from
  1024 to 65535. `--port` is parsed as a string and checked by
  `foil view`, so anything else, including `abc`, is
  `foil: invalid port`. Fifty busy ports
  is `foil: no free port in 7460-7509`.
- **Project.** It resolves the project the way every command does:
  `FOIL_PROJECT`, otherwise the Git toplevel of the working directory. A
  supervised viewer gets that from its working directory. The header
  name and the Workspace line come from `project.json`'s recorded
  checkout, not from the toplevel, so a viewer started in a linked
  worktree still shows the main checkout's name.
- **Inside the fleet.** With a non-empty `FOIL_SEAT_ID` (the same test
  as every authority check), it prints
  `foil: view is outside the fleet only`, exits 1, and binds nothing.
  That check comes first.
- **Output.** Its only stdout line is the address, `http`, then
  `127.0.0.1:<port>`, with a trailing slash, flushed, so a script that
  reads one line works and so does a supervisor's log.
- **Stopping.** Ctrl-C and SIGTERM exit 0 with no output. `foil view`
  catches `KeyboardInterrupt` itself, because `cli.main` does not, and
  installs a SIGTERM handler that raises it. The serve loop runs in the
  main thread, so nothing calls `shutdown()` from inside it.
- **Errors.** A failure of the command is one line on stderr and exit 1
  (N8). A failure inside a request never prints: `handle_error`,
  `log_message`, and `log_error` are overridden, and an unexpected
  exception in a handler is an empty 500. So the supervisor's log holds
  the address line and at most one error line per start.
- **Clients.** Each connection has a 10 s socket timeout. The request
  line and headers must also arrive within 10 s in total: a
  `threading.Timer` started with the connection calls
  `shutdown(SHUT_RDWR)` on it at the deadline and is cancelled once the
  headers are parsed. That stops a client that drips one byte at a time,
  which the per-read timeout alone does not. A Raw
  response to a slow reader holds only its own thread.
- **State.** No PID file, no lock, no state, no access log. Two viewers
  on one project are two ports and do not interfere. Stopping either
  leaves seats, windows, the registry, and the board unchanged.
- **Freshness.** The page does not refresh itself. A reload reads the
  files again. A viewer started before tmux or before any fleet shows
  "no session yet" until a reload finds one.
- **Cost.** A fleet page probes tmux once per seat. A seat page probes
  only that seat: three probes for an alive seat, four for a dead one
  (state and window ownership, then peek's own state and probe), and
  one capture. Reusing peek's probe would save one or two calls, and
  costs a change to `peek_text` that is not worth it at this size.
- **Connections.** At most 64 at once. Past that a new connection is
  closed, so idle sockets cannot pile up threads.

## 4. Running it as a service

launchd on macOS and `systemd --user` on Linux run an ordinary
foreground program, start it at login, and restart it when it fails.
The program needs no daemon mode. It needs four things, and
`foil view` has them once `--port` exists:

| The supervisor needs | `foil view` |
|---|---|
| A fixed address to bookmark | `--port N` |
| A foreground process that does not fork | It serves in the foreground |
| A clean stop on SIGTERM | Exits 0 |
| A non-zero exit on failure, so it is retried | One stderr line, exit 1 |

Foil does not write the unit file. Writing one is a file outside the
store and would need its own command. The README gives the steps, and
the operator skill gets an onboarding-style step: when the human asks
for the viewer to keep running, the agent fills in the file, shows it,
and loads it only after the human agrees.

Rules for a service:

- **One service per project, each on its own port, outside 7460-7509.**
  A port inside that range can be taken by an ad hoc `foil view` while
  the service is down (at login, or between restarts), and then the
  bookmark shows another project. The page title carries the project
  name, so a wrong page is visible.
- **`PATH`.** launchd starts agents with a minimal `PATH`, so Homebrew's
  `tmux` is not on it and `foil view` would exit
  `foil: tmux is not installed`. The Harnesses page would also call
  every CLI uninstalled. The unit sets `PATH` to the human's login-shell
  `PATH`. The agent asks the human to run `echo $PATH` in their own
  terminal, because the agent's own `PATH` may be a harness sandbox's.
- **The store.** If the human sets `FOIL_HOME` or `XDG_DATA_HOME`, the
  unit sets the same value, or the viewer finds a different store.
- **tmux.** The service runs as the same user, so it reaches the same
  tmux server socket. It must not set a different `TMUX_TMPDIR`.
- **Upgrades.** A running service keeps the old code after Foil is
  updated. Restart it after `uv tool install` or `foil init`.

macOS, `~/Library/LaunchAgents/dev.foil.view.PROJECT.plist`:

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
    <key>PATH</key><string>THE HUMAN'S PATH</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>/ABSOLUTE/HOME/Library/Logs/foil-view-PROJECT.log</string>
  <key>StandardErrorPath</key><string>/ABSOLUTE/HOME/Library/Logs/foil-view-PROJECT.log</string>
</dict></plist>
```

`SuccessfulExit` false restarts after a failure and not after a clean
stop, matching systemd's `Restart=on-failure`. The log lives under the
user's own `~/Library/Logs`, not in the shared `/tmp`. Load with
`launchctl bootstrap gui/$(id -u) <plist>`. After editing the file, run
`launchctl bootout gui/$(id -u)/dev.foil.view.PROJECT` first, then
bootstrap again. A busy port fails once every 30 s and adds one line to
the log each time.

Linux, `~/.config/systemd/user/foil-view-PROJECT.service`:

```ini
[Unit]
Description=Foil viewer for PROJECT

[Service]
ExecStart="/ABSOLUTE/PATH/TO/foil" view --port PORT
WorkingDirectory=/ABSOLUTE/PATH/TO/REPO
Environment="PATH=THE HUMAN'S PATH, WITH EACH % WRITTEN AS %%"
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

Load with `systemctl --user enable --now foil-view-PROJECT`. Stop with
`systemctl --user disable --now foil-view-PROJECT`. Read the log with
`journalctl --user -u foil-view-PROJECT`. Without
`loginctl enable-linger`, it runs only while the user is logged in,
which suits a viewer.

The README examples use these placeholders, not real home paths, and
no `http` URL, so the docs checks pass.

A machine-wide viewer for every project stays out: the agreed text
opens each repository by running the viewer from it.

## 5. Loopback, and who can reach it

How other local web UIs handle this:

| App | Bind | Host check | Auth by default |
|---|---|---|---|
| Jupyter Server | 127.0.0.1, port 8888 then the next free one | A non-local `Host` gets 403, against DNS rebinding. `localhost` and loopback addresses count as local | Token in the printed URL |
| opencode `serve` / `web` | 127.0.0.1, `--port` | — | None. `OPENCODE_SERVER_PASSWORD` turns on basic auth; binding `0.0.0.0` calls for it |
| Syncthing GUI | 127.0.0.1:8384 | Since 0.14.6 the GUI answers only to a `localhost`, `127.0.0.1`, or `[::1]` host, against DNS rebinding | None. A GUI password is optional |

The common pattern is: bind loopback, refuse a non-loopback `Host`, and
ship no auth on loopback, with auth optional. Jupyter adds a token
because a notebook runs code. `foil view` runs nothing and types nothing,
so it follows the common pattern.

What the server does:

- **Bind.** `127.0.0.1` only. No flag binds anything else.
- **Host.** The `Host` hostname must be `127.0.0.1`, `localhost`, or
  `[::1]`, with any port. A missing `Host`, or any other name, gets 403
  and an empty body. A page on `evil.example` that rebinds its name to
  `127.0.0.1` still sends `Host: evil.example:<port>`, and a page cannot
  set `Host`. Any port is allowed so `ssh -L 8000:127.0.0.1:7460`
  works; the README already sells use over SSH.
- **Origin.** When `Origin` is present it must equal `http://` plus the
  request's own `Host`. Anything else, including `null`, gets the empty
  403. A navigation with no `Origin` loads the page.
- **Methods.** GET and HEAD. Every other method gets an empty 405.
  `send_error` is replaced, so no standard error page is ever sent.
- **Headers.** `Content-Security-Policy: default-src 'none'; style-src
  'unsafe-inline'; script-src 'nonce-<random>'; img-src data:; base-uri 'none';
  form-action 'none'; frame-ancestors 'none'`,
  `Cross-Origin-Resource-Policy: same-origin`, `Cache-Control:
  no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`. CORP stops another site from loading a viewer response
  as an image or a script, which carries a valid `Host` and no
  `Origin`. The page cannot load anything from another host. The
  favicon is `data:,`, which `img-src data:` allows, so the browser
  neither asks for `/favicon.ico` nor logs a policy error.
- **Hostile text.** Pane text, board files, file names, and persona
  lines are written by agents. Every value is escaped where it becomes
  HTML. A value inside a link (`?open=`) is percent-encoded with
  `urllib.parse.quote`, then escaped; the server decodes it and compares
  it with the listing. There are no inline event handlers.
  `capture-pane` runs without `-e`, so the pane is plain text.
- **Launch argv.** Preset `command` tokens are not scanned when a preset
  loads. Before the Harnesses table shows a token, the credential scan
  that mail uses runs on it, and a matching token is shown as
  `[hidden]`. So is the token after any flag whose name, with `-` read
  as `_`, contains one of the scan's secret key parts (`api_key`,
  `access_token`, `secret`, `password`, and the rest in `errors.py`), so `--api-key AKIA...` is hidden
  too, as are the value after `--token` or `--key`, a `*_TOKEN=` or
  `*_KEY=` assignment, a header value after a secret name, and URL
  user info. This is best effort, and F38 and D2 say so.

Who can still read the page:

- **The same user, including seats.** They can already read the store
  directly. Refusing `foil view` inside the fleet keeps a seat from
  opening a page that lists every fleet. It does not stop a seat that
  `curl`s a running viewer (F5: cooperative, not hostile).
- **Other users on the same machine.** The project folder is mode 0700
  and its files are 0600, and the tmux socket is private, so today
  other users cannot read pane text or the board. Loopback TCP is open
  to every local user, so while the viewer runs, they can. This is the
  same trade opencode and Syncthing make by default. It goes into F37
  and D2, not only the README, so it is a stated limit. The common next
  step, if it is ever needed, is opencode's: an environment variable
  that turns on basic auth, which fits a unit file.

## 6. Large files and long panes

The full file can always be seen. The common approach for a file viewer
(GitHub and GitLab blobs, Jenkins console logs) is a size-limited
preview plus a raw view, not infinite scroll:

- An opened board file shows its first 1 MiB, escaped. When the file is
  larger, a line says `Showing the first 1 MiB of <size>.`
- Every opened file has a **Raw** link to
  `/fleets/<id>/raw?open=<path>`. It streams the whole file in 64 KiB
  chunks as `text/plain; charset=utf-8` with `nosniff` and
  `Content-Security-Policy: sandbox`. The browser shows any size,
  Ctrl-F searches it, and nothing in it runs.
- **Opening safely.** A listed path is not enough. Between the listing
  and the open, a seat can swap a file or a directory for a symlink or a
  FIFO, and a seat can swap `fleets/<id>` itself. So the viewer opens
  the project folder, then `fleets`, `<id>`, `board`, and each path
  component below it with `os.open(name, O_RDONLY | O_NOFOLLOW | O_DIRECTORY,
  dir_fd=parent)`, then the file with `O_RDONLY | O_NOFOLLOW |
  O_NONBLOCK`. It requires `S_ISREG` from `fstat` and serves the preview
  and Raw from that one descriptor. A symlink anywhere on the path, or a
  file that is not regular, opens nothing and shows
  `foil: cannot read: <path>`.
- **Listing.** Walk the board without following symlinks. Skip
  symlinks, and skip names that start with `.`, which hides the
  `.<name>.*.tmp` files an atomic write leaves for a moment. Each row
  shows the size from `lstat`, not a line count, so a page load does not
  read every mail file.
- **Pane.** 40 lines by default, as `seat peek`. A **More** link doubles
  `?lines=`: 80, 160, and so on up to 1280. An integer from 1 to 1280 is
  used as given; anything else is 40. Foil's tmux wrapper refuses
  output over 64 Ki characters (`tmux.py`), so a wide pane can fail
  before 1280; the page then shows that one-line peek error and a link
  back to the last length that worked. This is `peek --lines N`, not a new reading of
  the pane.

## 7. Reading the store

- **Refactors.** Five splits of reading from printing, with command
  output unchanged: `seat_rows` from `list_seats` (also returning
  `branch`, `window_id`, and `owned`, the probe's identity result, which
  `seat list` keeps out of its output),
  `peek_text` from `peek_seat`, `roster_rows` from `list_roster`,
  `read_lessons` from `list_lessons`, and `preset_ids` from
  `installed_presets`.
- **Choosing a fleet.** `fleet_dir()` reads `FOIL_FLEET` from the
  environment, and the threaded server cannot set it per request. An
  optional `fleet` argument goes through `fleet_dir`, `registry_path`,
  `load_registry`, `fleet_overrides`, `_target`, `_session_name`,
  `seat_rows`, and `peek_text`. Commands pass nothing and behave as
  today.
- **One `git rev-parse` per request.** `foil_root()` runs git on every
  call, from about 25 call sites. A per-request cache of
  `_common_dir`, held in a `contextvars.ContextVar` that the handler sets
  and clears, removes the repeats without changing those signatures.
- **Fleets** are the directories under `fleets/` whose names are safe
  ids and that are not symlinks.
- **Errors stay in their section.** Every section is built inside its
  own `try`. Any exception there (`FoilError`, `TmuxError`, `OSError`,
  `UnicodeDecodeError`, a TOML error) becomes one red line in that
  section, using the `FoilError` text when there is one and
  `foil: unreadable` otherwise. One bad fleet leaves the others; a bad
  `roles.toml` leaves Harnesses; one bad lesson file empties Lessons
  into one line and leaves the fleet list.
- **Settings.** Roles are `roster_rows`, so the description follows
  `roster list`; a persona with no role is `+ <name>`. Harnesses are
  every eligible id from `preset_ids`: id, installed from
  `shutil.which(command[0])`, the model ids, the launch argv, and the
  `env` names. Values are never read. The argv keeps placeholders as
  written and quotes only a token that contains whitespace, so token
  boundaries survive. `fake` is left out. A list file that does not load
  is named under the table. The default mapping is one row per `[[map]]`
  row, in file order; an omitted model shows `no model`.
- **Overrides** show the last row for each role: role, next-spawn model,
  and the note column when any row has a note. An empty file shows "No
  overrides. The next spawn uses the default mapping."
- **Next-spawn model.** For a seat's role, the second line appears when
  the last override's model differs from the model of
  `entries[n % len(entries)]`, where `n` is that role's stored seat
  count. That is what spawn picks without the override (F3). When the
  mapping or the overrides fail to load, or the role has no mapping
  rows, there is no second line, and the section that failed shows its
  error.
- **Attach.** From the registry's `tmux_session`:
  `tmux attach -t '=<session>'`. The `=` makes tmux match the exact
  name. Without it tmux matches a prefix, and the `main` fleet's session
  name is a prefix of every other fleet's (`_session_name`), so a gone
  `main` session would attach to another fleet. An empty session shows
  "No session yet. This fleet has not started." A seat adds
  `\; select-window -t <window id>` only when `owned` is true: the
  probe found that window still carrying this fleet's and this seat's
  markers. The state string cannot decide this, because `dead` covers
  both a dead pane that is still there and a window that is gone. A
  killed seat keeps its old window id, and ids are reused after a tmux
  restart, so a stored id alone is not trusted. Nothing is derived from
  the directory name. The operator skill's `tmux attach -t <session>`
  gains the same `=`.
- **Posts** are grouped by thread: one heading per `room/<id>/`, its
  files under it in name order.
- **Writes nothing.** None of the readers creates a file. The viewer
  never calls `ensure_board`, `ensure_registry`, `private_dir`, or a
  function that takes a lock. A test enforces this (section 9).

## 8. What the visual design does not cover

These states are missing from the Magic Patterns design. The agreed
text decides each one:

| Gap in the design | Resolution |
|---|---|
| The seat page shows "not found" when there is no capture | Every seat opens. A killed seat, or a dead one whose window is gone, shows peek's one-line error |
| Attach always adds `select-window` | Only when the window is verified. No session: "no session yet" |
| No next-spawn model on the seat page | Shown beside the recorded pair |
| The override type carries a harness | Not shown. Overrides replace the model only |
| No note column on overrides | Added when any row has a note |
| One model per role in the default mapping | One row per `[[map]]` row, each with its own model, in order |
| No persona-only roles | Listed as `+ <name>` |
| No unreadable-fleet state | One error line in that fleet's row |
| Posts are a flat list | Grouped by thread |
| Google Fonts import | Removed. System fonts |

## 9. Tests

Unit tests use a hand-built project and a stand-in tmux, the way
`tests/test_room_post.py` does. The base port is injectable, so tests
never hold 7460.

- `FOIL_SEAT_ID` set: exit 1, the exact stderr line, and no socket
  bound (patch `socket.socket.bind` to fail the test). An empty
  `FOIL_SEAT_ID` serves.
- Ports: a stop and an immediate restart on the same `--port` binds
  again; with the base held, the default binds the next port and prints
  it; with every port in the range held, the range error; an explicit
  busy `--port`, `foil: port N is in use`; `--port 80` and
  `--port 70000`, `foil: invalid port`; SIGTERM exits 0.
- Access: `Host` of `127.0.0.1`, `localhost`, and a forwarded port load;
  `evil.example` and a missing `Host` get an empty 403; `Origin` equal
  to the `Host` loads, any other and `null` get an empty 403; POST gets
  an empty 405; every response carries the headers in section 5.
- Clients: an idle connection does not delay a second request; a client
  that sends a header byte every 5 s is closed after 10 s.
- Every page is fetched over a real socket.
- Settings: roles, a persona-only row, an invalid `roles.toml` (Roles
  shows one line, Harnesses still renders), a missing binary, an invalid
  `harnesses/<id>.toml`, a credential-shaped argv token shown as
  `[hidden]`, mapping rows in order with per-row models.
- Overrides: none, one, several for one role (the last wins), a note,
  an invalid file.
- Next spawn: a line that appears, and none when the override equals the
  mapping model.
- Attach: no session; a session; a verified window; a killed seat with
  a stale id (no `select-window`); the `=` prefix present.
- Board: a symlinked file and directory are skipped; a dotfile is
  skipped; an unlisted `?open=` and `../../project.json` open nothing; a
  file swapped for a symlink after listing, a parent directory swapped
  for a symlink, and a FIFO all show `foil: cannot read`, and the FIFO
  does not hang; a file over 1 MiB shows the cut line, and Raw returns
  every byte with `text/plain` and `sandbox`; `<script>` in a file name,
  a file body, and a pane is escaped.
- Errors: a `TmuxError` during peek is one line in the Pane section; an
  exception in a handler is an empty 500 and nothing on stderr.
- A second fleet with a broken registry leaves the first on screen.
- **Writes nothing:** snapshot every path, mode, and byte under the
  store, crawl every page and every listed file, then compare.

One end-to-end test with real tmux and the `fake` harness: spawn a
lead, load its seat page, check that the pane text matches `seat peek`,
stop the viewer, and check that the window is still there.

Standing checks that change:

- `tests/test_command_surface.py`: `view` with `--port N` and no
  positionals in `SECTION_6`; `foil view` in `SECTION_6_SYNOPSES`; the
  asserted counts become "nine top-level commands" and "twenty-three
  actions".
- `tests/test_skills.py`: `("view",): {"--port"}` in `FLAGS`, and
  `("view",)` in the operator's `ROLE_COMMANDS`.
- The size report shows the new count.

## 10. Requirement text this change lands

- **Section 6.1:** a row `foil view` | `--port N` | serves the read-only
  page (F37, F38, F39). The count becomes nine top-level commands and twenty-three
  actions.
- **Section 6.2:** a `view` column: outside the fleet yes, lead no,
  worker no.
- **F37, F38, F39 (new, under Visibility):** running and access, the
  pages, and board files. The agreed text, condensed, plus
  every user-visible rule this design adds: the port range 7460-7509
  and `--port` validation; the messages `foil: port N is in use`,
  `foil: no free port in 7460-7509`, `foil: invalid port`, and
  `foil: view is outside the fleet only`; the stdout line; exit 0 on
  Ctrl-C and SIGTERM; the `Host`, `Origin`, and method rules; the
  1 MiB preview, the Raw view and its headers; the best-effort argv
  hiding; `?lines=` and its cap;
  the attach command with `=` and the verified window; the 64-connection
  cap; the skipped dot-files and non-UTF-8 names; and the exposure to
  other local users.
- **"Keep the screen in step":** a rule in CONTRIBUTING "Rules every
  change keeps". It is about how changes are made, so it is not in the
  requirement rows.
- **Section 8, operator row:** the service step: fill in the unit file
  with the human's `PATH` and a port outside 7460-7509, show it, load
  it only after the human agrees, and restart it after an upgrade. The
  `tmux attach -t '=<session>'` form.
- **Section 11:** "A UI, a server, MCP, or a hosted service" becomes "A
  UI or server other than `foil view`, a viewer that writes or types
  into a pane, a daemon mode or a command that installs a service, MCP,
  or a hosted service."
- **D2:** the README limits gain the viewer's exposure to other local
  users.
- **N4:** no text change. The pull request states the reason.
- **Finish everywhere:** README "runs no daemon" and "no daemon and no
  GUI" (and the same lines in README.zh-CN.md), and the `pyproject.toml`
  description "Headless", are reworded: Foil itself runs no daemon, and
  `foil view` is an optional read-only page. A README section has the
  service steps, and README.zh-CN.md carries the same section (D3). [architecture.md](architecture.md) gains a row for the
  module. CHANGELOG gets an entry.

## 11. Settled by common practice

1. Default port: a fixed default with fallback to the next free port,
   as Jupyter does. An explicit `--port` does not fall back.
2. Other local users: no auth on loopback, stated as a limit, as
   opencode and Syncthing do. Optional basic auth through an environment
   variable is the later step if it is needed.
3. Large files: a preview and a raw view, as code hosts and CI logs do.
4. Service: the platform supervisor runs the foreground process; Foil
   writes no unit file.
