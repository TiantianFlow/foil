# Declarative Seat Profiles

Status: Implemented for `foil seat spawn --profile`
Schema: `schemas/profile-v1.schema.json` (also packaged at
`foil/resources/schemas/profile-v1.schema.json`)

A seat profile is the CLI-agnostic, reusable launch contract. One TOML file
owns everything a seat needs so the core runtime keeps no provider-named
branches:

- **executable / candidates** — `[executable] candidates`, with the profile
  `cli` required among them; optional `version_argv`.
- **launch argv** — `[launch] argv`, including argv[0], exactly like shipped
  adapter records. Placeholders `{seat_id}`, `{working_directory}`,
  `{adapter_state_dir}`, `{bootstrap_path}`, `{model}`, and
  `{native_session_id}` expand at launch. Remainder argv after `--` still
  wins over the file's launch argv (the executable is prepended for you).
- **startup / bootstrap delivery** — `[startup] argv` is appended to the
  launch argv, so an interactive CLI can be told to read `{bootstrap_path}`
  and its sibling `FOIL.md` before beginning.
- **resume argv** — `[resume] supported = true` plus `argv`.
- **cwd / workdir behavior** — `[working] isolated = true|false` supplies the
  seat's default isolation; `--isolated` / `--no-isolated` / `--shared-cwd`
  still override it.
- **permission-mode flags** — `[permissions] supervised` / `auto` hold plain
  flag lists (no placeholders). `auto`, when present, must be non-empty.
- **session capture** — `[session_capture] kind` is `none`,
  `generated_uuid`, or `command_json_list_delta`; the command kind requires
  `argv` (including argv[0]) and `id_pointer`, with optional `cwd_pointer`.
- **safe environment forwarding** — `[environment] forward` lists variable
  *names* only.

```toml
schema_version = 1
id = "pi-interactive"
cli = "pi"

[executable]
candidates = ["pi"]

# Interactive CLIs stay interactive: no print/batch flag here. Pi blocks on
# `-p` (one-shot print mode), so an interactive profile omits it; batch mode
# is an explicit profile choice, never a default.
[launch]
argv = ["pi", "{working_directory}"]

[startup]
argv = ["Read {bootstrap_path}, its sibling FOIL.md, and the role_path recorded in bootstrap.json before beginning."]

[session_capture]
kind = "generated_uuid"

[environment]
forward = ["PI_API_KEY"]
```

```sh
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --json \
  --seat researcher --profile ./profiles/pi-interactive.toml --role researcher
```

`--cli` is optional when `--profile` is given and must match the profile's
`cli` when both are present. `--role` / `--role-file`, `--model`,
`--permission`, and the session-capture flags compose with a profile; flags
beat file values.

## Environment forwarding

Profiles declare variable names; values are resolved from the host
environment at each launch (spawn or resume) and reach the seat through tmux
`-e` injection plus the runner's exec-time merge. Values are never written to
runner plans, bootstrap cards, registry records, or the audit log — the plan
stores names under `env_forward` only. Names that are absent on the host are
skipped, keeping optional credentials opt-in.

Credential-shaped launch data is rejected before any runner-plan, bootstrap,
or registry artifact is written: profile files and remainder argv pass
through the same secret-shaped-data heuristics the registry enforces.

## Backward compatibility and migrations

- Shipped `grok` / `opencode` spawns without extra argv still use the
  built-in adapter presets; `--profile` is strictly additive.
- Profile-owned remainder argv still wins: `--cli grok -- --model ...` builds
  the same implicit profile as before, with no declarative file involved.
- Existing registries keep working: seats spawned before profiles stored no
  `profile_id`, `startup_argv`, permission flags, or `environment_forward`,
  and resume rebuilds them with the previous implicit-profile semantics.

## Limitations

- Injected values live in the tmux server's memory (visible to the same user
  via `tmux show-environment` or the process list while the window is
  created). Foil never persists or prints them; this is cooperative
  same-user protection, not a credential boundary.
- `tmux >= 3.2` is required for `-e` injection.
- A profile file is validated fail-closed: unknown fields, unsafe IDs,
  placeholder misuse, empty `permissions.auto`, and credential-shaped values
  are all load-time errors.
