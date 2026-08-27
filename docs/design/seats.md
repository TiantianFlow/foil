# Predefined Seat Recipes

Status: Implemented for `foil seats` and `foil seat spawn`
Schema: `schemas/seats-v1.schema.json` (also packaged at
`foil/resources/schemas/seats-v1.schema.json`)

`.foil/seats.toml` is a spawn recipe, not live membership. The registry
still owns who is running. A seat id looks up CLI, model, profile, role,
role-file, and workdir so `foil seat spawn --seat spec` can run without
ad hoc flags. Flags remain overrides. Personas stay untouched Markdown;
this file only points at them.

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
project root. `role` and `role_file` are mutually exclusive. After
merging flags, a seat must have `cli` or `profile`. Spawn fails closed
if the seat has no recipe and no `--cli` / `--profile` override.

`foil init` writes an empty `schema_version = 1` file when missing and
never overwrites a user-owned roster.

```sh
foil seats set --seat lead --lead --cli grok --role manager
foil seats list
foil seats show --seat lead
foil seat spawn --state-dir STATE_ROOT --fleet FLEET_ID --seat lead
```

Each provided spawn flag wins over the file. `--role` clears a
configured `role_file` unless `--role-file` is also passed, and the
reverse. Remainder argv after `--` still replaces a profile's launch
argv. `--permission` on the CLI is optional: the file can set `auto`,
and the implicit default is `supervised` only when neither is set.

`foil catalog-map` does not assign a CLI. `cli` and `preset` stay null.
Persist staffing here.

A profile remains a reusable CLI launch contract
(`docs/design/profiles.md`). This file is the per-seat staffing map.
Shipped presets `grok` and `opencode` can back a recipe; they are not
the default staffing story.
