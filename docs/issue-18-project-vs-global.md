# Issue #18: project-level vs global files

Issue #18, quoted in full:

> The role templates, board and skills are in the project repo.
> Many of them should be global.

Surveyed against this checkout (`foil/issue-18`, HEAD `78ab3f7`, foil 0.3.1).

## Two facts that frame the answer

1. **None of these files are in the user's tracked repo.** `.foil/` is
   excluded via `.git/info/exclude` (`/.foil/`, written by
   `src/foil/project.py` `EXCLUDE_PATTERN`), and `git status --ignored`
   shows only `!! .foil/`. F7 already holds. So "in the project repo"
   means "in the project folder", not "committed".
2. **"Global" has two possible meanings, and they cost very different
   amounts.**
   - *Version-global*: read from the installed package
     (`src/foil/defaults/...`) at the point of use instead of copying it into
     `.foil/`. No new location and no new concept.
   - *Machine-global*: a new user-level directory (for example under
     `~/.config/`). That needs a new layout in requirements §7.1, which
     today says "Everything lives in one Foil folder inside the project".
     It also needs a lookup order, an override rule, and an upgrade story.

   Recommendation: use version-global where it applies. Do not introduce a
   machine-global directory for #18.

## Verdicts for the three items the issue names

### 1. Role templates

| Path today | Verdict | One-line reason |
|---|---|---|
| `.foil/templates/<role>.toml` (`lead`, `implementer`, `reviewer`, `verifier`, `architect`) | **keep-in-project** | Harness, model, permission, and worktree are per-repository choices, as F20 says ("Templates are per project"). This project's `architect.toml` uses a harness and model not in the defaults, and trust is asked once per harness per repository. |
| `.foil/templates/personas/*.md` | **keep-in-project** | The global copy already exists at `src/foil/defaults/personas/`. The project file is the editable override that `persona = "personas/<role>.md"` resolves to. `persona_text` in `src/foil/presets.py` refuses paths outside `.foil/templates`. `personas/architect.md` here has no packaged source. |

Note for the lead, not a recommendation for #18: 8 of the 9 persona files
here are byte-identical to the packaged ones. Because `init` never
overwrites a persona (F20), an unedited copy goes stale after an upgrade.
This is the same upgrade problem the 0.2.x skills had (see CHANGELOG). If
it matters, it is its own ticket.

### 2. Board

| Path today | Verdict | One-line reason |
|---|---|---|
| `.foil/board/` (`mail/`, `notes/`, `tasks/`, `results/`, `status.md`) | **keep-in-project**. This is runtime state, not source. | A board belongs to one fleet, and a fleet belongs to one repository. Its registry is `.foil/run/registry.json`. A global board would mix mail and `status.md` across repositories. It would also break the containment checks in `mail read` and `board read`, which require paths inside `.foil/board/` (F29, F31). |

The same reasoning covers `.foil/run/` (registry, instruction files, and
plans), which is also runtime state.

### 3. Skills

| Path today | Verdict | One-line reason |
|---|---|---|
| `.foil/skills/lead.md`, `.foil/skills/worker.md` | **move-global** (version-global) | They are byte-identical to `src/foil/defaults/skills/`, and `init` overwrites them whenever their bytes differ (`write_default_templates` in `src/foil/presets.py`). A project cannot keep an edit to them, so the copy is only a cache. Its only reader is `_instruction` in `src/foil/lifecycle.py`, which inlines the text into `.foil/run/instructions/<seat>.md`. That function could read the packaged file instead. |
| `.foil/skills/operator.md` | **keep-in-project** | The onboarding pointer line `Read .foil/skills/operator.md and follow it.` (`src/foil/lifecycle.py`, README, `tests/test_init.py`) needs a stable relative path that an agent with nothing installed can read. A path inside the uv tool directory is neither stable nor short. |
| `skills/` at the top of Foil's own repo | **keep** (Foil's repo, not a user project) | These are symlinks to `src/foil/defaults/skills/`, pinned by `test_skill_links_point_at_the_packaged_files` in `tests/test_skills.py`. They are the README's links for readers, not a second copy. |

## Smallest change if the lead takes the skills verdict

- Requirements first: update §7.1 (`skills/<role>.md` becomes
  `skills/operator.md` only), the `foil init` row in §6 ("the three skills"
  becomes "the operator skill"), and §8 wherever it implies on-disk lead
  and worker skills.
- `src/foil/lifecycle.py` `_instruction`: read `lead.md` and `worker.md`
  through the packaged loader (`_builtin("skills", ...)` in
  `presets.py`), not from `foil_root(root) / "skills"`. The `Skill:` line in
  the instruction text should then stop naming a project path.
- `src/foil/presets.py` `write_default_templates`: write only
  `operator.md`. The report `Updated skills: …` / `Skills: current` keeps
  its shape.
- Existing projects: leave old `.foil/skills/lead.md` and `worker.md` alone
  and let them go unread, or have `init` report them. Do not delete user
  files silently.
- Tests to touch: `tests/test_presets.py` (skill replacement, around
  lines 562-638), `tests/test_spawn.py` (around line 519, which reads the
  skill from `.foil/skills`), and `tests/test_init.py` (the report).
- This makes the package smaller and fits N4. It needs no new command,
  flag, or directory.

Honest value check: since 0.3.x already overwrites these files on `init`,
the user-visible gain is small. There are two fewer files per project, and
a seat cannot pick up a stale skill when `init` was skipped after an
upgrade. If the lead wants #18 closed with no code change, "survey done, all
keep-in-project except the lead and worker skill cache" is a defensible
outcome.

## Outside the three named items (flagged, not recommended under #18)

- `.foil/harnesses/*.toml` is the strongest real case for
  *machine-global*. This project's `claude`, `grok`, and `opencode` files
  override the built-ins, and `agent` and `agy` are user presets. They hold
  machine-specific wrapper paths and env-name lists that have nothing to do
  with this repository, so every new project needs the same copies. The
  issue does not name harnesses, and fixing this would need a new
  machine-global location (requirements §7.1 and F27). That is a separate
  ticket if the lead or human wants it.
- `.foil/memory/` holds lessons that "survive fleets" (§7.1). Whether they
  should survive across projects is a product question, not part of #18.
