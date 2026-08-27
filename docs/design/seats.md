# Predefined Seat Recipes

Status: Implemented for `foil seats` and `foil seat spawn`
Schema: `schemas/seats-v1.schema.json` (also packaged at
`foil/resources/schemas/seats-v1.schema.json`)

`.foil/seats.toml` is a spawn recipe, not live membership. The registry
still owns who is running. A seat id looks up CLI, model, profile, role,
role-file, and workdir so spawn can omit ad hoc `--cli` / `--model`.
Flags remain overrides. Personas stay untouched Markdown; this file only
points at them.

`foil init` writes a complementary starter roster when the file is
missing (lead and implementer on `grok`, challenger on `opencode`) and
never overwrites a user-owned file. Edit with `foil seats set` after you
discover local CLIs. Seat id `lead` defaults to `lead = true` unless the
file or `--no-lead` says otherwise.

```toml
schema_version = 1

[seats.lead]
lead = true
cli = "grok"
role = "manager"
model = "grok-4.6"

[seats.spec]
profile = "profiles/spec.toml"
role_file = "personas/engineering-frontend.md"
```

Relative `profile`, `role_file`, and `cwd` paths resolve against the
project root. File recipes require `cli` or `profile`, and `role` XOR
`role_file`. Spawn flags may pass both: `role` is the label, `role_file`
is the path. If both `cli` and `profile` are set, they must match the
profile's `cli` at load and `seats set`, not only at spawn.

Copy `state_root` and `fleet_id` from `foil init` JSON. Spawn still
needs those flags; `--seat ID` is not enough.

```sh
foil seats set --seat lead --cli grok --role manager
foil seats list
foil seats show --seat lead
foil seat spawn --state-dir "$STATE_DIR" --fleet "$FLEET_ID" --seat lead
```

Each provided spawn flag wins over the file. `--lead` / `--no-lead` and
`--shared-cwd` / `--no-shared-cwd` are tri-state. Remainder argv after
`--` still replaces a profile's launch argv. `--permission` on the CLI
is optional: the file can set `auto`, and the implicit default is
`supervised` only when neither is set.

`foil catalog-map` does not assign a CLI. `cli` and `preset` stay null.
Persist staffing here.

A profile remains a reusable CLI launch contract
(`docs/design/profiles.md`). This file is the per-seat staffing map.
Shipped presets `grok` and `opencode` can back a recipe; they are not
the default staffing story.
