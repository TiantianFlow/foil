"""Seat lifecycle. Init creates the folder; spawn launches a seat."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from foil import __version__
from foil.board import ensure_board, write_mail
from foil.errors import FoilError, shown
from foil.presets import (
    BUILTIN_IDS,
    _builtin,
    _init_check,
    entry_label,
    expand_argv,
    fleet_overrides,
    installed_harness,
    installed_presets,
    load_preset,
    load_template,
    persona_text,
    read_description,
    read_roles,
    spawn_entry,
    write_default_templates,
)
from foil.project import (
    SKELETON,
    common_dir,
    foil_root,
    git_toplevel,
    lift_root_runtime,
    prepare_project,
    project_key,
    project_record,
    require_host_tools,
    rewrite_stored_paths,
    store_root,
)
from foil.store import (
    SAFE_ID,
    SCHEMA_VERSION,
    SEAT_FIELDS,
    create_exclusive,
    ensure_registry,
    exclusive_lock,
    fleet_dir,
    fleet_id,
    load_registry,
    read_json,
    registry_path,
    scan,
    update_registry,
    write_bytes,
    write_json,
)
from foil.tmux import ProbeState, TmuxController, TmuxError, TmuxTarget

STAR_URL = "https://github.com/TiantianFlow/foil"
STAR_GAPS = (3, 14)  # days before the second and the third star line
STAR_LATER = 30  # days before one more line on a new minor version


def _minor(version: str) -> tuple[int, int]:
    match = re.match(r"(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(version)
    return int(match[1]), int(match[2])


def _star_due() -> dict | None:
    """The star record to save with this report's line, or None when no line is due."""
    path = store_root() / "star.json"
    today = date.today()
    running = _minor(__version__)
    if not (path.exists() or path.is_symlink()):
        record, shown, seen = {}, [], running
    else:
        try:
            record = read_json(path)
            shown = [date.fromisoformat(day) for day in record["shown"]]
            seen = _minor(str(record["minor"]))
            stop = record["stop"]
        except (FoilError, KeyError, TypeError, ValueError, RecursionError):
            return None
        if stop is not False or not shown:
            return None
        gap = (today - shown[-1]).days
        if len(shown) <= len(STAR_GAPS):
            due = gap >= STAR_GAPS[len(shown) - 1]
        else:
            due = gap >= STAR_LATER and running > seen
        if not due:
            return None
    return {
        **record,
        "schema_version": SCHEMA_VERSION,
        "shown": [*record.get("shown", []), today.isoformat()],
        "minor": ".".join(map(str, max(running, seen))),
        "stop": False,
    }


def _star_saved(record: dict | None) -> bool:
    """Write the record atomically. The store root is the user's, so leave its mode alone."""
    if record is None:
        return False
    path = store_root() / "star.json"
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".star.", suffix=".tmp")
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError, RecursionError):
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        return False
    return True


def _names(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def _overridden_builtins(toplevel: Path) -> list[str]:
    del toplevel
    directory = store_root() / "harnesses"
    names: list[str] = []
    for harness_id in BUILTIN_IDS:
        if harness_id == "fake":
            continue
        path = directory / f"{harness_id}.toml"
        if path.is_symlink() or not path.is_file():
            continue
        names.append(harness_id)
    return names


def _print_init_report(
    toplevel: Path,
    had_templates: set[str],
    _had_personas: set[str],
    updated_skills: list[str],
    removed: list[Path] | None = None,
) -> None:
    print(f"foil {__version__}")
    presets, skipped = installed_presets(toplevel)
    found = [item["id"] for item in presets]
    first = found[0] if found else ""
    second = found[1] if len(found) > 1 else first
    print(f"Installed harnesses, in id order (a tiebreak, not a ranking): {_names(found)}")
    overridden = _overridden_builtins(toplevel)
    if overridden:
        print("Built-in presets overridden by harnesses: " + _names(overridden))
    for path, reason in skipped:
        print(f"Skipped {path}: {reason}")
    roles = ("lead", "implementer", "reviewer")
    stored: list[str] = []
    for role in roles:
        ids = [entry["id"] for entry in load_template(toplevel, role)["harness"]]
        chosen = ids[0]
        label = chosen if len(ids) == 1 else ", ".join(ids)
        fresh = role not in had_templates
        second_reviewer = role == "reviewer" and chosen == second and second != first
        if fresh and second_reviewer:
            why = "second installed id"
        elif fresh and chosen == first:
            why = "first installed id"
        else:
            why = "left alone"
        print(f"{role}: {label} ({why})")
        stored.extend(ids)
    if not found:
        print("No eligible harness is installed.")
    distinct = len(set(stored))
    if distinct == 1:
        print("The three default templates use one harness id.")
    else:
        word = {2: "two", 3: "three"}.get(distinct, str(distinct))
        print(
            f"The three default templates use {word} different harness ids. "
            "Two ids can still run the same program or model; "
            "set model on a template to choose one."
        )
    personas = [
        path.stem
        for path in files("foil").joinpath("defaults", "personas").iterdir()
        if path.name.endswith(".md")
    ]
    personas.sort()
    template_names = {item["id"] for item in (read_roles(toplevel) or [])}
    available_personas = [name for name in personas if name not in template_names]

    print("Wrote templates: " + _names([role for role in roles if role not in had_templates]))
    print("Left templates: " + _names([role for role in roles if role in had_templates]))
    print("Wrote personas: none")
    print("Left personas: " + _names(personas))
    if available_personas:
        print(f"Available personas (use 'foil roster add'): {_names(available_personas)}")
    for path in removed or []:
        print(f"Removed: {path}")
    permissions = ", ".join(
        f"{role} {load_template(toplevel, role)['permission']}" for role in roles
    )
    del updated_skills
    operator = store_root() / "skills" / "operator.md"
    if operator.is_file() and not operator.is_symlink():
        print(f"Skill: {operator}")
    else:
        print("Skill: packaged")
    print(f"permission: {permissions}.")
    print(
        "ask stops a seat at its first approval prompt; auto asks the harness to skip "
        "approval prompts, though some harnesses still ask for some commands. "
        "Set permission in roles.toml. "
        "An omitted permission is auto. "
        "auto on the lead alone does not "
        "let the workers run unattended."
    )
    print(f"Project folder: {foil_root(toplevel)}")
    # Flush first: a closed stdout then fails before the record is spent.
    sys.stdout.flush()
    if _star_saved(_star_due()):
        print(f"For the human: if Foil helps, a GitHub star helps others find it: {STAR_URL}")
    operator = store_root() / "skills" / "operator.md"
    if operator.is_file() and not operator.is_symlink():
        print(f"Read {operator} and follow it. My goal: <goal>.")
    else:
        print("Read the packaged operator skill and follow it. My goal: <goal>.")


def _unlink_project_settings(root: Path) -> list[Path]:
    """Drop the named project paths. A symlink is removed and not followed."""

    removed: list[Path] = []
    for relative in (
        "roles.toml",
        "mapping.toml",
        "harnesses.toml",
        "harnesses",
        "templates",
        "skills/operator.md",
        "skills/lead.md",
        "skills/worker.md",
    ):
        path = root / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
            removed.append(path)
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
    return removed


def init_project(directory: str | None) -> None:
    require_host_tools()
    start = Path(directory).expanduser().resolve() if directory else Path.cwd()
    if not start.exists():
        raise FoilError("foil: not a git repository")
    toplevel = git_toplevel(start)
    root = foil_root(toplevel)
    # Writing the global lists chmods the store root. The star record must not
    # change a mode the user already set, so remember it and put it back.
    store = store_root()
    prior_mode = store.stat().st_mode & 0o777 if store.is_dir() and not store.is_symlink() else None
    # Project settings are unread until the unlink. A symlink must not be followed.
    token = _init_check.set(True)
    try:
        present = {item["id"] for item in (read_roles(toplevel) or [])}
        if any(role not in present for role in ("lead", "implementer", "reviewer")):
            installed_harness(toplevel)
        root, previous, rewritten = prepare_project(toplevel)
        for relative in SKELETON:
            path = root / relative
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.chmod(0o700)
        lift_root_runtime(root)
        # Only after a move, an adopt, or a retry. A healthy re-init leaves run/
        # alone, including a plan whose seat worktree the user has deleted.
        if rewritten:
            rewrite_stored_paths(root, toplevel, previous=previous)
        ensure_board(toplevel)
        ensure_registry(toplevel)
        roles = ("lead", "implementer", "reviewer")
        had_templates = {
            role
            for role in roles
            if any(item["id"] == role for item in (read_roles(toplevel) or []))
        }
        had_personas: set[str] = set()
        write_default_templates(toplevel)
    finally:
        _init_check.reset(token)
    # After every step that can still exit non-zero. A failure above deletes nothing.
    removed = _unlink_project_settings(root)
    _print_init_report(toplevel, had_templates, had_personas, [], removed)
    if prior_mode is not None and store.is_dir() and not store.is_symlink():
        store.chmod(prior_mode)


def _seat_name(registry: dict, template_name: str, requested: str | None) -> str:
    seats = registry["seats"]
    if template_name == "lead":
        if requested not in (None, "lead"):
            raise FoilError("foil: lead seat must be named lead")
        existing = seats.get("lead")
        if existing and existing.get("state") != "killed":
            raise FoilError("foil: lead already exists")
        return "lead"
    if not seats:
        raise FoilError("foil: the first seat must be the lead")
    if requested == "lead" or (requested is not None and requested in seats):
        raise FoilError(f"foil: seat '{shown(requested or '')}' already exists")
    if requested:
        if not SAFE_ID.fullmatch(requested):
            raise FoilError(f"foil: invalid name '{shown(requested)}'")
        return requested
    for number in range(1, 10001):
        candidate = f"{template_name}-{number}"
        if candidate not in seats and SAFE_ID.fullmatch(candidate):
            return candidate
    raise FoilError("foil: could not name seat")


def _git(toplevel: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(toplevel), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _refused_worktree(project: Path, destination: Path) -> bool:
    foil = foil_root(project)
    top = project / "worktrees"
    if destination == top or top in destination.parents:
        return True
    inside = destination == project or project in destination.parents
    under_foil = destination == foil or foil in destination.parents
    return inside and not under_foil


def _worktree(toplevel: Path, seat: str) -> tuple[str, str]:
    project = toplevel.resolve()
    parent = foil_root(project) / "worktrees"
    for number in range(1, 10001):
        label = seat if number == 1 else f"{seat}-{number}"
        branch = f"foil/{label}"
        taken = _git(toplevel, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
        if taken.returncode == 0:
            continue
        destination = (parent / label).resolve()
        if _refused_worktree(project, destination):
            raise FoilError("foil: refusing worktree inside the project")
        if destination.exists() or destination.is_symlink():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        created = _git(toplevel, ["worktree", "add", "-b", branch, str(destination)])
        if created.returncode != 0:
            raise FoilError("foil: could not create worktree")
        return str(destination), branch
    raise FoilError("foil: could not name worktree")


def _session_name(toplevel: Path, fleet: str | None = None) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", toplevel.name).strip("-")[:32] or "repo"
    digest = hashlib.sha256(str(toplevel.resolve()).encode()).hexdigest()[:8]
    base = f"foil-{slug}-{digest}"
    fleet = fleet or fleet_id()
    if fleet == "main":
        return base
    extra = re.sub(r"[^A-Za-z0-9]+", "-", fleet).strip("-")[:32] or "fleet"
    return f"{base}-{extra}"


_LEAD_COMMANDS = """foil seat spawn TEMPLATE [--name NAME] [--task TEXT]
foil seat kill NAME
foil seat archive NAME
foil seat resume [NAME]
foil seat list [--json] [--archived]
foil seat peek NAME [--lines N]
foil send TO TEXT
foil post [TEXT] [--re PATH] [--decline]
foil mail read PATH [--json]
foil mail list [--json]
foil board read PATH [--json]
foil board list PATTERN [--json]
foil memory add TEXT [--replaces ID]
foil memory list [--all] [--json]
foil memory accept ID
foil memory reject ID
foil roster list [--json]
foil roster show ROLE [--json]
foil roster add ROLE [--from FILE]
foil roster update ROLE FIELD=VALUE
foil roster remove ROLE"""

_WORKER_COMMANDS = """foil seat list [--json]
foil seat peek NAME [--lines N]
foil send TO TEXT
foil post [TEXT] [--re PATH] [--decline]
foil mail read PATH [--json]
foil mail list [--json]
foil board read PATH [--json]
foil board list PATTERN [--json]
foil memory add TEXT [--replaces ID]
foil memory list [--all] [--json]"""


def _accepted(root: Path) -> list[tuple[str, str]]:
    directory = foil_root(root) / "memory"
    if directory.is_symlink() or not directory.is_dir():
        return []
    found: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink():
            continue
        lesson = read_json(path)
        if lesson.get("state") == "accepted":
            found.append((str(lesson.get("id", "")), str(lesson.get("text", ""))))
    return found


def _persona_line(template: dict) -> str:
    return persona_text(template).strip() or "none"


def _instruction(
    root: Path,
    seat: str,
    template: dict,
    *,
    restarted: bool,
    first_launch: bool = False,
    task: Path | None = None,
) -> str:
    board = (fleet_dir(root) / "board").resolve()
    lessons = _accepted(root)
    learned = "none yet"
    if lessons:
        learned = "\n".join(f"- {item}: {text}" for item, text in lessons)
    lead = template["name"] == "lead"
    skill_text = _builtin("skills", "lead.md" if lead else "worker.md").strip()
    work = (
        "Stay in your worktree. Do not modify the project toplevel. "
        "Killing you will not delete your branch."
        if template["worktree"]
        else "You have no worktree. Killing you will not delete your branch."
    )
    lines = [
        f"Written by foil {__version__}.",
        f"You are seat `{seat}`. Lead is `lead`.",
        f"Board: `{board}`.",
        f"Identity is `FOIL_SEAT_ID` (yours is `{seat}`). You cannot change it with flags.",
        "Commands you may run:",
        _LEAD_COMMANDS if lead else _WORKER_COMMANDS,
        "Mail: `foil send TO TEXT`. Room: `foil post TEXT`.",
        "Send is one seat. Post is every other alive seat. "
        "The task and result files are the durable record. A note wakes nobody.",
        "A nudge line is `from path`. A path under `board/mail/` is `foil mail read`. "
        "A path under `board/room/` is `foil board read`.",
        "List `board/room` and read threads this seat has not answered.",
        "There is no ack command.",
        "Notes: write files under `board/notes/`. They wake no one.",
        "Contracts (parsed only to print; Foil does not act on them):",
        "status/v1 (state working|blocked|done, updated, questions),",
        "task/v1 (id, owner, state open|doing|done, acceptance),",
        "result/v1 (task, author, branch, outcome pass|fail),",
        "room/v1 (from, time, optional re, nudged).",
        f"Worktree: {work}",
        f"Persona:\n{_persona_line(template)}",
        skill_text,
        f"Accepted lessons: {learned}",
    ]
    if lead:
        workspace = project_record(foil_root(root)).get("path") or str(root.resolve())
        posts = (fleet_dir(root) / "board" / "room").resolve()
        override_path = (fleet_dir(root) / "overrides.toml").resolve()
        overrides = fleet_overrides(root)
        memory = (
            "\n".join(
                f"- {item['role']}: {entry_label(item) if item['id'] else item['model']}"
                for item in overrides
            )
            if overrides
            else "none"
        )
        lines.extend(
            [
                f"Workspace: `{workspace}`. The repository is shared by every fleet.",
                f"Posts: `{posts}`.",
                "Harnesses and model ids: "
                f"`{files('foil').joinpath('defaults', 'harnesses.toml')}`.",
                f"Profiles: `{store_root() / 'roles.toml'}`.",
                f"Default mapping: `{store_root() / 'mapping.toml'}`.",
                f"Fleet memory: `{override_path}`. Re-read that file before you spawn.",
                "A role's rows in that file replace its default list for this fleet.",
                "You do not pass a harness or a model flag.",
                "Current overrides:",
                memory,
                "Record them with `foil roster update ROLE harness=ID/MODEL,ID/MODEL` "
                "or, for one entry, `model=VALUE`. That leaves the default mapping unchanged.",
            ]
        )
    if restarted:
        lines.append(
            "You were restarted. List your mail with `foil mail list` "
            "and read each file with `foil mail read`."
        )
    elif task is not None:
        lines.append(
            f"Your task is `{task.resolve()}`. Read it first with `foil mail read`."
        )
    else:
        lines.append("Run `foil mail list` and read your mail before you act.")
    # First launch only. A native resume also leaves restarted false, and
    # F24 does not repeat this sentence on crash recovery.
    if first_launch and (lead or _has_archive(root, seat)):
        lines.append("Older mail in your mailbox may be from an earlier goal.")
    if lead:
        names = {item["id"] for item in (read_roles(root) or [])}
        roster = []
        for name in sorted(names):
            try:
                item = load_template(root, name)
            except FoilError:
                continue
            flag = "yes" if item["worktree"] else "no"
            labels = ", ".join(entry_label(entry) for entry in item["harness"])
            roster.append(f"- {item['name']}: harness {labels}, worktree {flag}")
        lines.extend(
            [
                "You may kill another seat by name. You may not kill yourself "
                "and you may not run `foil seat kill --all`.",
                "Templates:",
                *roster,
                'Staff with `foil seat spawn implementer --task "..."`. '
                "Use `foil roster` to change a template.",
                "Keep `board/status.md` current.",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_instruction(
    root: Path,
    seat: str,
    template: dict,
    *,
    restarted: bool,
    first_launch: bool = False,
    task: Path | None = None,
) -> Path:
    path = (fleet_dir(root) / "run" / "instructions" / f"{seat}.md").resolve()
    body = _instruction(
        root,
        seat,
        template,
        restarted=restarted,
        first_launch=first_launch,
        task=task,
    ).rstrip("\n")
    text = f"{body}\nRe-read `{path}` whenever you are woken.\n"
    scan(text)
    write_bytes(path, text.encode())
    return path


def _native_resume(preset: dict, session_id: str, *, worktree: str) -> bool:
    argv = preset.get("resume") or []
    if not argv:
        return False
    if preset["session_id"] == "generated" and session_id:
        return True
    # `--continue` / `--last` mean "the last session in this directory".
    # A seat with no worktree runs in the project directory, so those flags
    # would resume someone else's session. Start that seat fresh instead.
    if not worktree:
        return False
    return preset["session_id"] == "none" and bool({"--continue", "--last"} & set(argv))


def _open(
    root: Path,
    registry: dict,
    seat: str,
    template: dict,
    preset: dict,
    *,
    worktree: str,
    session_id: str,
    native: bool,
    restarted: bool,
    model: str,
    first_launch: bool = False,
    task: Path | None = None,
) -> str:
    cwd = Path(worktree) if worktree else root
    instruction = _write_instruction(
        root, seat, template, restarted=restarted, first_launch=first_launch, task=task
    )
    prompt = instruction.read_text(encoding="utf-8")
    argv = expand_argv(
        preset,
        model=model or None,
        prompt=prompt,
        session_id=session_id or None,
        permission=template["permission"],
        resume=native,
    )
    forward = list(preset["env"])
    for name in ("PATH", "HOME"):
        if name not in forward:
            forward.append(name)
    plan_path = (fleet_dir(root) / "run" / "plans" / f"{seat}.json").resolve()
    environment = {
        "FOIL_SEAT_ID": seat,
        "FOIL_PROJECT": project_key(common_dir(root)),
        "FOIL_FLEET": fleet_id(),
    }
    for name in ("FOIL_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    plan = {
        "argv": argv,
        "cwd": str(cwd.resolve()),
        "env": environment,
        "env_forward": forward,
    }
    scan(plan)
    write_bytes(plan_path, json.dumps(plan, sort_keys=True).encode())
    session = registry.get("tmux_session") or _session_name(root)
    registry["tmux_session"] = session
    try:
        target = TmuxController().launch(
            fleet_id=str(registry["fleet_id"]),
            seat_id=seat,
            session_name=session,
            window_name=seat,
            working_directory=cwd.resolve(),
            runner_argv=[sys.executable, "-m", "foil.runner", str(plan_path)],
        )
    except TmuxError as exc:
        raise FoilError("foil: could not launch seat") from exc
    if not target.window_id:
        TmuxController().abandon_window(target)
        raise FoilError("foil: could not launch seat")
    return target.window_id


def _state(registry: dict, name: str, record: dict[str, str]) -> str:
    if record.get("state") == "killed":
        return "killed"
    try:
        owned = TmuxController().matches_window(
            str(registry["fleet_id"]),
            name,
            str(registry.get("tmux_session") or ""),
            record.get("window_id") or "",
        )
    except TmuxError:
        return "dead"
    return "alive" if owned else "dead"


def _owned(registry: dict, name: str, record: dict[str, str], state: str) -> bool:
    """Whether the seat's window still carries this fleet and this seat, dead pane or not."""

    window = record.get("window_id") or ""
    if state != "dead" or not window.startswith("@"):
        return state == "alive"
    target = TmuxTarget(
        session_name=str(registry.get("tmux_session") or ""),
        window_name=name,
        session_id=None,
        window_id=window,
    )
    try:
        probe = TmuxController().probe(str(registry["fleet_id"]), name, target)
    except TmuxError:
        return False
    return bool(probe.identity_matches)


def spawn_seat(
    root: Path,
    template: str,
    *,
    name: str | None = None,
    task: str | None = None,
) -> None:
    ensure_board(root)
    loaded = load_template(root, template)
    persona_text(loaded)
    if task is not None:
        scan(task)
    # A fleet's first spawn must launch with the fleet id it stores.
    ensure_registry(root)
    registry = load_registry(root)
    seat = _seat_name(registry, loaded["name"], name)
    stored = sum(item["template"] == loaded["name"] for item in registry["seats"].values())
    chosen = spawn_entry(root, loaded["name"], loaded["harness"], stored)
    model = chosen["model"]
    preset = load_preset(root, chosen["id"])
    native = str(uuid.uuid4()) if preset["session_id"] == "generated" else ""
    worktree, branch = _worktree(root, seat) if loaded["worktree"] else ("", "")
    mailed = write_mail(root, seat, task) if task is not None else None
    try:
        window = _open(
            root,
            registry,
            seat,
            loaded,
            preset,
            worktree=worktree,
            session_id=native,
            native=False,
            restarted=False,
            model=model,
            first_launch=True,
            task=mailed,
        )
    except Exception:
        _drop_mail(mailed)
        raise
    record = {key: "" for key in SEAT_FIELDS}
    record.update(
        {
            "name": seat,
            "template": loaded["name"],
            "harness": chosen["id"],
            "model": model,
            "window_id": window,
            "worktree": worktree,
            "branch": branch,
            "session_id": native,
        }
    )
    session = str(registry.get("tmux_session") or "")

    def apply(fresh: dict) -> None:
        existing = fresh["seats"].get(seat)
        if existing is not None and not (seat == "lead" and existing.get("state") == "killed"):
            raise FoilError(f"foil: seat '{shown(seat)}' already exists")
        fresh["seats"][seat] = record
        if session:
            fresh["tmux_session"] = session
        if loaded["name"] == "lead":
            fresh["lead"] = "lead"

    try:
        update_registry(root, apply)
    except Exception:
        TmuxController().abandon_window(
            TmuxTarget(
                session_name=str(registry.get("tmux_session") or ""),
                window_name=seat,
                session_id=None,
                window_id=window,
            )
        )
        _drop_mail(mailed)
        raise


def _drop_mail(path: Path | None) -> None:
    if path is not None and not path.is_symlink() and path.is_file():
        path.unlink()


def _target(
    registry: dict, root: Path, seat_name: str, window: str, fleet: str | None = None
) -> TmuxTarget:
    return TmuxTarget(
        session_name=str(registry.get("tmux_session") or _session_name(root, fleet)),
        window_name=seat_name,
        session_id=None,
        window_id=window,
    )


def _remove_leftover(registry: dict, root: Path, seat_name: str, window: str) -> None:
    """Drop a dead seat's window before the record is killed or relaunched."""

    if not window:
        return
    try:
        TmuxController().remove_verified(
            str(registry["fleet_id"]), seat_name, _target(registry, root, seat_name, window)
        )
    except TmuxError as exc:
        raise FoilError("foil: could not stop seat") from exc


def _known(registry: dict, name: str | None) -> dict[str, str]:
    if not name or not SAFE_ID.fullmatch(name) or name not in registry["seats"]:
        raise FoilError(f"foil: unknown seat '{shown(name or '')}'")
    return registry["seats"][name]


def kill_seats(
    root: Path, *, name: str | None = None, all_seats: bool = False
) -> None:
    registry = load_registry(root)
    names = list(registry["seats"]) if all_seats else [_known(registry, name)["name"]]
    for seat_name in names:
        record = registry["seats"][seat_name]
        window = record.get("window_id") or ""
        if _state(registry, seat_name, record) != "killed":
            _remove_leftover(registry, root, seat_name, window)

        def apply(fresh: dict, seat_name: str = seat_name, window: str = window) -> None:
            current = fresh["seats"].get(seat_name)
            if current is None:
                if not all_seats:
                    raise FoilError(f"foil: unknown seat '{shown(seat_name)}'")
                return
            if (current.get("window_id") or "") != window:
                if not all_seats:
                    raise FoilError(f"foil: seat '{shown(seat_name)}' changed")
                return
            current["state"] = "killed"

        update_registry(root, apply)


def _restart(root: Path, registry: dict, name: str) -> None:
    record = registry["seats"][name]
    loaded = load_template(root, record["template"])
    persona_text(loaded)
    preset = load_preset(root, record["harness"])
    stored = record["session_id"]
    native = _native_resume(preset, stored, worktree=record.get("worktree") or "")
    if native:
        session = stored
    elif preset["session_id"] == "generated":
        session = str(uuid.uuid4())
    else:
        session = ""
    old_window = record.get("window_id") or ""
    _remove_leftover(registry, root, name, old_window)
    window = _open(
        root,
        registry,
        name,
        loaded,
        preset,
        worktree=record["worktree"],
        session_id=session,
        native=native,
        restarted=not native,
        model=record.get("model") or "",
    )
    record["window_id"] = window
    record["state"] = ""
    record["session_id"] = session
    tmux_session = str(registry.get("tmux_session") or "")

    def apply(fresh: dict) -> None:
        current = fresh["seats"].get(name)
        if current is None:
            raise FoilError(f"foil: unknown seat '{shown(name)}'")
        if current.get("state") == "killed":
            raise FoilError(f"foil: seat '{shown(name)}' is killed")
        if (current.get("window_id") or "") != old_window:
            raise FoilError(f"foil: seat '{shown(name)}' changed")
        fresh["seats"][name] = dict(record)
        if tmux_session:
            fresh["tmux_session"] = tmux_session

    try:
        update_registry(root, apply)
    except Exception:
        TmuxController().abandon_window(
            TmuxTarget(
                session_name=str(registry.get("tmux_session") or ""),
                window_name=name,
                session_id=None,
                window_id=window,
            )
        )
        raise


def resume_seats(root: Path, name: str | None) -> int:
    registry = load_registry(root)
    if name is not None:
        _known(registry, name)
        state = _state(registry, name, registry["seats"][name])
        if state == "killed":
            raise FoilError(f"foil: seat '{name}' is killed")
        if state == "alive":
            print(f"foil: seat '{name}' is alive")
            return 0
        _restart(root, registry, name)
        return 0
    failures: list[str] = []
    for seat_name in sorted(registry["seats"]):
        if _state(registry, seat_name, registry["seats"][seat_name]) != "dead":
            continue
        try:
            _restart(root, registry, seat_name)
        except FoilError as exc:
            reason = str(exc).removeprefix("foil: ")
            failures.append(f"foil: seat '{seat_name}' not resumed: {reason}")
    if not failures:
        return 0
    print("\n".join(failures), file=sys.stderr)
    return 1


def _archive_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _archive_dir(root: Path) -> Path:
    return fleet_dir(root) / "run" / "archive"


def _archive_body(record: dict[str, str]) -> bytes:
    lines = [f"{key}: {record.get(key, '')}" for key in SEAT_FIELDS]
    return ("\n".join(lines) + "\n").encode()


def _write_archive(root: Path, name: str, record: dict[str, str]) -> Path:
    """Write the prose record. A crash after this still leaves the seat killed."""

    directory = _archive_dir(root)
    payload = _archive_body(record)
    stem = f"{_archive_stamp()}-{name}"
    for number in range(10000):
        label = stem if number == 0 else f"{stem}-{number}"
        path = directory / f"{label}.txt"
        try:
            create_exclusive(path, payload)
        except FileExistsError:
            continue
        return path
    raise FoilError("foil: could not archive seat")


def _read_archive(path: Path) -> dict[str, str]:
    record = {key: "" for key in SEAT_FIELDS}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(": ")
        if separator and key in record:
            record[key] = value
    return record


def _archive_files(root: Path) -> list[Path]:
    directory = _archive_dir(root)
    if directory.is_symlink() or not directory.is_dir():
        return []
    files = [
        path
        for path in directory.iterdir()
        if path.is_file() and not path.is_symlink() and path.suffix == ".txt"
    ]
    files.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    return files


def _has_archive(root: Path, name: str) -> bool:
    return any(_read_archive(path).get("name") == name for path in _archive_files(root))


def _window_gone(registry: dict, root: Path, name: str, record: dict[str, str]) -> None:
    """Refuse unless the stored window is gone.

    This is the probe result, not `_state` and not `matches_window`.
    `DEAD` with `identity_matches` false is the only result that proceeds.
    That is also what an empty id, a non-@ id, a failed display-message,
    and an empty tmux line return.
    """

    window = record.get("window_id") or ""
    try:
        probe = TmuxController().probe(
            str(registry["fleet_id"]),
            name,
            _target(registry, root, name, window),
        )
    except TmuxError as exc:
        raise FoilError(f"foil: seat '{name}' window is unknown") from exc
    if probe.identity_matches is None or probe.state is ProbeState.UNKNOWN:
        raise FoilError(f"foil: seat '{name}' window is unknown")
    if probe.state is ProbeState.DEAD and probe.identity_matches is False:
        return
    if probe.state is ProbeState.ALIVE:
        raise FoilError(f"foil: seat '{name}' window is still running")
    raise FoilError(f"foil: seat '{name}' window still exists")


def archive_seat(root: Path, name: str) -> None:
    """Free a killed worker's name. The worktree, branch, and mailbox stay."""

    path = registry_path(root)
    # The lock covers the read, the probe, the archive file, and the
    # registry replace. A second caller waits, then sees the key is gone.
    with exclusive_lock(path.with_suffix(".lock")):
        registry = load_registry(root)
        record = dict(_known(registry, name))
        stored = record.get("state") or ""
        if stored == "alive":
            raise FoilError(f"foil: seat '{name}' is alive")
        if stored == "dead":
            raise FoilError(f"foil: seat '{name}' is dead")
        if name == "lead":
            raise FoilError("foil: seat 'lead' cannot be archived")
        if stored != "killed":
            raise FoilError(f"foil: seat '{name}' is {_state(registry, name, record)}")
        _window_gone(registry, root, name, record)
        _write_archive(root, name, record)
        registry["seats"].pop(name)
        write_json(path, registry)
    print(f"archived seat '{name}'")
    if record.get("worktree"):
        print(f"kept branch {record.get('branch') or ''}")


def _list_archived(root: Path, *, as_json: bool) -> None:
    rows = []
    for path in _archive_files(root):
        record = _read_archive(path)
        record["archived"] = path.name
        rows.append(record)
    if as_json:
        print(json.dumps(rows, sort_keys=True))
        return
    for row in rows:
        launched = f"{row['harness']}/{row['model']}" if row["model"] else row["harness"]
        print(
            f"{row['name']}\t{row['template']}\t{row['state']}\t"
            f"{row['worktree']}\t{launched}\t{row['branch']}"
        )


def seat_rows(
    root: Path, registry: dict, *, only: str | None = None, owned: bool = False
) -> list[dict]:
    """Seat facts for `seat list`, plus branch and window id.

    ``only`` keeps one seat. ``owned`` adds whether its window still carries
    this fleet and seat, which costs a second probe for a dead seat.
    """

    rows = []
    for seat_name, record in sorted(registry["seats"].items()):
        if only is not None and seat_name != only:
            continue
        try:
            description = read_description(load_template(root, record["template"]))
        except (FoilError, OSError, UnicodeDecodeError):
            description = ""
        state = _state(registry, seat_name, record)
        rows.append(
            {
                "name": seat_name,
                "template": record["template"],
                "state": state,
                "worktree": record["worktree"],
                "harness": record.get("harness", ""),
                "model": record.get("model", ""),
                "description": description,
                "branch": record.get("branch", ""),
                "window_id": record.get("window_id", ""),
                "owned": owned and _owned(registry, seat_name, record, state),
            }
        )
    return rows


def list_seats(root: Path, *, as_json: bool = False, archived: bool = False) -> None:
    if archived:
        _list_archived(root, as_json=as_json)
        return
    keys = ("name", "template", "state", "worktree", "harness", "model", "description")
    rows = [{key: row[key] for key in keys} for row in seat_rows(root, load_registry(root))]
    if as_json:
        print(json.dumps(rows, sort_keys=True))
        return
    for row in rows:
        launched = f"{row['harness']}/{row['model']}" if row["model"] else row["harness"]
        print(
            f"{row['name']}\t{row['template']}\t{row['state']}\t"
            f"{row['worktree']}\t{launched}\t{row['description']}"
        )


def peek_seat(root: Path, name: str, *, lines: int) -> None:
    sys.stdout.write(peek_text(root, name, lines=lines))


def peek_text(root: Path, name: str, *, lines: int, fleet: str | None = None) -> str:
    if lines < 1:
        raise FoilError("foil: invalid lines")
    registry = load_registry(root, fleet)
    record = _known(registry, name)
    state = _state(registry, name, record)
    window = record.get("window_id") or ""
    controller = TmuxController()
    try:
        probe = (
            controller.probe(
                str(registry["fleet_id"]), name, _target(registry, root, name, window, fleet)
            )
            if window
            else None
        )
    except TmuxError as exc:
        raise FoilError("foil: could not peek seat") from exc
    # A dead pane is still this seat's pane when the markers match.
    owned = probe is not None and probe.observed is not None and bool(probe.identity_matches)
    if state == "killed" or not owned:
        raise FoilError(f"foil: seat '{name}' is {state}")
    try:
        return controller.capture_pane(probe.observed.window_id or "", lines)
    except TmuxError as exc:
        raise FoilError("foil: could not peek seat") from exc
