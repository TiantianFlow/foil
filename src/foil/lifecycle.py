"""Seat lifecycle. Init creates the folder; spawn launches a seat."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

from foil import __version__
from foil.board import ensure_board, items_for, write_mail
from foil.errors import FoilError, shown
from foil.guard import hook_environment
from foil.presets import (
    BUILTIN_IDS,
    TURN_TIMEOUT,
    _builtin,
    _init_check,
    entry_label,
    expand_argv,
    fleet_overrides,
    installed_harness,
    installed_presets,
    load_preset,
    load_template,
    model_warning,
    persona_text,
    read_description,
    read_roles,
    rotation_index,
    rotation_key,
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
from foil.runner import empty_record, load_record, record_path, save_record
from foil.store import (
    SAFE_ID,
    SCHEMA_VERSION,
    SEAT_FIELDS,
    create_exclusive,
    ensure_registry,
    exclusive_lock,
    fleet_dir,
    fleet_id,
    fleet_ids,
    in_fleet,
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
RETRY = (30, 120, 480)  # seconds before each retry of a failed turn (F43)


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
    print("Run foil seat join lead, then read the file it names. My goal: <goal>.")


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
        # A parent that is a symlink is the link. Unlink it and do not follow.
        parent = root
        linked = False
        for part in Path(relative).parts:
            parent = parent / part
            if parent.is_symlink():
                parent.unlink()
                removed.append(parent)
                linked = True
                break
        if linked:
            continue
        if path.is_file():
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
            # A symlink is unlinked later and not followed, so it is not chmod'd.
            if path.is_symlink():
                continue
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
        if existing and existing.get("state") == "joined":
            raise FoilError("foil: lead is joined. Run foil seat leave first")
        if existing and existing.get("state") != "killed":
            raise FoilError("foil: lead already exists")
        return "lead"
    if not seats:
        raise FoilError("foil: the first seat must be the lead")
    if requested == "user":
        raise FoilError("foil: seat name 'user' is the human's mailbox")
    if requested == "foil":
        raise FoilError("foil: seat name 'foil' is reserved")
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


def _worktree(toplevel: Path, seat: str, base: str = "") -> tuple[str, str]:
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
        created = _git(
            toplevel, ["worktree", "add", "-b", branch, str(destination), *([base] if base else [])]
        )
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
foil post [TEXT] [--re PATH] [--decline] [--members NAMES] [--close]
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
foil roster update ROLE FIELD=VALUE"""

_WORKER_COMMANDS = """foil seat list [--json]
foil seat peek NAME [--lines N]
foil send TO TEXT
foil post --re PATH [TEXT] [--decline]
foil mail read PATH [--json]
foil mail list [--json]
foil board read PATH [--json]
foil board list PATTERN [--json]
foil memory add TEXT [--replaces ID]
foil memory list [--all] [--json]"""

_JOINED_COMMANDS = """foil seat kill --all
foil seat leave
foil mail wait [--timeout SECONDS] [--wake]
foil roster add ROLE [--from FILE] [--project]
foil roster update ROLE FIELD=VALUE [--project]
foil roster remove ROLE [--project]
foil init
foil view [--port N]"""


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


_MARK = re.compile(r"^<!-- (launched|joined|end) -->$")
# A joined lead has done the setup, and the README carries the rest (F24).
_JOINED_DROPPED = ("Setup", "Roster", "The viewer", "Teardown and upgrading")


def _for_lead(text: str, *, joined: bool) -> str:
    """The lead skill as one kind of lead reads it, without the other kind's paragraphs."""

    keep, block = [], None
    for line in text.splitlines():
        mark = _MARK.match(line)
        if mark:
            block = None if mark.group(1) == "end" else mark.group(1)
            continue
        if block is None or (block == "joined") == joined:
            keep.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def _joined_sections(text: str) -> str:
    keep, dropping = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            dropping = line[3:].strip() in _JOINED_DROPPED
        if not dropping:
            keep.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


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
    joined: bool = False,
    handoff: Path | None = None,
    branch: str = "",
) -> str:
    board = (fleet_dir(root) / "board").resolve()
    lessons = _accepted(root)
    learned = "none yet"
    if lessons:
        learned = "\n".join(f"- {item}: {text}" for item, text in lessons)
    lead = template["name"] == "lead"
    skill_text = _builtin("skills", "lead.md" if lead else "worker.md").strip()
    if lead:
        skill_text = _for_lead(skill_text, joined=joined)
    work = (
        "Stay in your worktree. Do not modify the project toplevel. "
        "Killing you will not delete your branch."
        if template["worktree"]
        else "You have no worktree. Killing you will not delete your branch."
    )
    identity = (
        "You joined with `foil seat join lead`. Every foil command you run outside a seat "
        "acts as `lead` until `foil seat leave`."
        if joined
        else f"Identity is `FOIL_SEAT_ID` (yours is `{seat}`). You cannot change it with flags."
    )
    commands = _LEAD_COMMANDS if lead else _WORKER_COMMANDS
    if joined:
        commands = f"{commands}\n{_JOINED_COMMANDS}"
    lines = [
        f"Written by foil {__version__}.",
        f"You are seat `{seat}`. Lead is `lead`.",
        f"Board: `{board}`.",
        identity,
        "Commands you may run:",
        commands,
        "Mail: `foil send TO TEXT`. Room: `foil post --re PATH TEXT`.",
        "Send is one seat. Post is a room's members. "
        "The task and result files are the durable record. A note wakes nobody.",
        (
            "A `foil mail wait` line is `time from path`. "
            if joined
            else "You are woken by a prompt that lists new files as `time from path`. "
            "End your turn when you wait for mail or a post: Foil starts your next turn. "
        )
        + "A path under `board/mail/` is `foil mail read`. "
        "A path under `board/room/` is `foil board read`.",
        "List `board/room` and read the open rooms this seat is a member of or that name it "
        "as @name, where it has not answered.",
        "There is no ack command.",
        "Notes: write files under `board/notes/`. They wake no one.",
        "Contracts (parsed only to print; Foil does not act on them):",
        "status/v1 (state working|blocked|done, updated, questions, decisions; "
        "the body starts with ## Mandate),",
        "task/v1 (id, owner, state open|doing|done, acceptance, optional source),",
        "result/v1 (task, author, branch, outcome pass|fail|blocked|dropped, optional question),",
        "room/v2 (from, time, optional re, members, closed, wakes).",
        f"Worktree: {work}",
        f"Persona:\n{_persona_line(template)}",
        skill_text,
        *([_joined_sections(_builtin("skills", "joined.md"))] if joined else []),
        f"Accepted lessons: {learned}",
    ]
    if lead and branch:
        lines.append(
            f"Your goal branch is `{branch}`, checked out in your worktree. Integrate there; "
            "the human merges it when status.md says done."
        )
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
        global_root = store_root()
        project = foil_root(root)
        lines.extend(
            [
                f"Workspace: `{workspace}`. The repository is shared by every fleet.",
                f"Posts: `{posts}`.",
                f"Harnesses and model ids: `{global_root / 'harnesses.toml'}`.",
                f"Profiles: `{global_root / 'roles.toml'}`.",
                f"Default mapping: `{global_root / 'mapping.toml'}`.",
            ]
        )
        for relative in (
            "roles.toml",
            "mapping.toml",
            "harnesses.toml",
            "harnesses",
            "personas",
        ):
            path = project / relative
            if path.exists() or path.is_symlink():
                lines.append(f"Project override: `{path}`.")
        lines.extend(
            [
                f"Fleet memory: `{override_path}`. Re-read that file before you spawn.",
                "A role's rows in that file replace its default list for this fleet.",
                "You do not pass a harness or a model flag.",
                "Current overrides:",
                memory,
            ]
        )
        if not joined:
            lines.append(
                "Record them with `foil roster update ROLE harness=ID/MODEL,ID/MODEL` "
                "or, for one entry, `model=VALUE`. That leaves the default mapping unchanged."
            )
    if joined:
        lines.append(
            "The mail from before the join was listed in the brief. Hear from the fleet "
            "the way the brief named. `foil mail wait --timeout 0` at the start of each "
            "turn is always safe."
        )
    elif restarted:
        lines.append(
            "You were restarted. Check before you repeat a merge, push, post or send. "
            "List your mail with `foil mail list` and read each file with `foil mail read`."
        )
    elif handoff is not None:
        lines.append(
            f"Read your handoff `{handoff.resolve()}` first, then list your mail with "
            "`foil mail list`."
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
        kill = (
            "You may kill another seat by name. `foil seat kill --all` stops every other seat."
            if joined
            else "You may kill another seat by name. You may not kill yourself "
            "and you may not run `foil seat kill --all`."
        )
        lines.extend(
            [
                kill,
                "Templates:",
                *roster,
                'Staff with `foil seat spawn implementer --task "..."`. '
                "Change a template only with the harness and model updates named above.",
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
    joined: bool = False,
    handoff: Path | None = None,
    branch: str = "",
) -> Path:
    path = (fleet_dir(root) / "run" / "instructions" / f"{seat}.md").resolve()
    body = _instruction(
        root,
        seat,
        template,
        restarted=restarted,
        first_launch=first_launch,
        task=task,
        joined=joined,
        handoff=handoff,
        branch=branch,
    ).rstrip("\n")
    reread = (
        f"Re-read `{path}` after your harness compacts this conversation, or when unsure."
        if joined
        else f"Re-read `{path}` whenever you are woken."
    )
    text = f"{body}\n{reread}\n"
    scan(text)
    write_bytes(path, text.encode())
    return path


def _board(root: Path) -> Path:
    return (fleet_dir(root) / "board").resolve()


def _present(root: Path, seat: str) -> list[str]:
    """Every item for the seat now, so a fresh session does not get older mail as new."""

    return [item["relative"] for item in items_for(_board(root), seat, set())]


def _seat_cwd(root: Path, registry: dict, worktree: str) -> Path:
    """Its worktree, else a launched lead's worktree, else the checkout (F45)."""

    if worktree:
        return Path(worktree)
    lead = registry["seats"].get("lead") or {}
    path = lead.get("worktree") or ""
    if path and lead.get("state") not in ("joined", "killed") and Path(path).is_dir():
        return Path(path)
    return root


def _write_plan(
    root: Path,
    registry: dict,
    seat: str,
    template: dict,
    preset: dict,
    *,
    worktree: str,
    session_id: str,
    model: str,
    first: str,
    restarted: bool,
    instruction: Path,
) -> tuple[Path, Path]:
    """The plan the runner runs, and the directory it runs in (F43)."""

    cwd = _seat_cwd(root, registry, worktree)
    argv = {
        kind: expand_argv(
            preset,
            model=model or None,
            session_id=session_id or None,
            permission=template["permission"],
            turn=kind == "turn",
        )
        for kind in ("start", "turn")
    }
    forward = list(preset["env"])
    for name in ("PATH", "HOME"):
        if name not in forward:
            forward.append(name)
    environment = {
        **preset.get("set_env", {}),
        "FOIL_SEAT_ID": seat,
        "FOIL_PROJECT": project_key(common_dir(root)),
        "FOIL_FLEET": fleet_id(),
    }
    for name in ("FOIL_HOME", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(hook_environment(root, cwd, worktree))
    plan = {
        **argv,
        "session_key": preset["session_key"],
        "session_id": session_id,
        "cwd": str(cwd.resolve()),
        "env": environment,
        "env_forward": forward,
        "instruction": str(instruction),
        "board": str(_board(root)),
        "seat": seat,
        "reader": "user" if seat == "lead" else "lead",
        "worker": seat != "lead",
        "turn_timeout": int(template.get("turn_timeout", TURN_TIMEOUT)),
        "retry": list(RETRY),
        "first": first,
        "restarted": restarted,
    }
    scan(plan)
    plan_path = (fleet_dir(root) / "run" / "plans" / f"{seat}.json").resolve()
    write_bytes(plan_path, json.dumps(plan, sort_keys=True).encode())
    return plan_path, cwd


def _launch(root: Path, registry: dict, seat: str, plan_path: Path, cwd: Path) -> str:
    session = registry.get("tmux_session") or _session_name(root)
    registry["tmux_session"] = session
    try:
        target = TmuxController().launch(
            fleet_id=str(registry["fleet_id"]),
            seat_id=seat,
            session_name=session,
            window_name=seat,
            working_directory=cwd.resolve(),
            runner_argv=[sys.executable, "-P", "-m", "foil.runner", str(plan_path)],
        )
    except TmuxError as exc:
        raise FoilError("foil: could not launch seat") from exc
    if not target.window_id:
        TmuxController().abandon_window(target)
        raise FoilError("foil: could not launch seat")
    return target.window_id


def _open(
    root: Path,
    registry: dict,
    seat: str,
    template: dict,
    preset: dict,
    *,
    worktree: str,
    session_id: str,
    model: str,
    first: str,
    restarted: bool,
    record: dict,
    first_launch: bool = False,
    task: Path | None = None,
    handoff: Path | None = None,
    branch: str = "",
) -> str:
    instruction = _write_instruction(
        root,
        seat,
        template,
        restarted=restarted and first == "start",
        first_launch=first_launch,
        task=task,
        handoff=handoff,
        branch=branch,
    )
    plan_path, cwd = _write_plan(
        root,
        registry,
        seat,
        template,
        preset,
        worktree=worktree,
        session_id=session_id,
        model=model,
        first=first,
        restarted=restarted,
        instruction=instruction,
    )
    save_record(_board(root), seat, record)
    return _launch(root, registry, seat, plan_path, cwd)


JOINED = "foil: lead is joined. It has no pane."


def _state(registry: dict, name: str, record: dict[str, str]) -> str:
    if record.get("state") in ("killed", "joined"):
        return record["state"]
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


def _shown_state(
    root: Path, registry: dict, name: str, record: dict[str, str], fleet: str | None = None
) -> str:
    """F8: an alive seat is working, idle, or paused by its runner's record."""

    state = _state(registry, name, record)
    if state != "alive":
        return state
    turns = load_record(fleet_dir(root, fleet) / "board", name)
    if turns["paused"]:
        return "paused"
    return "working" if turns["turn_started"] is not None else "idle"


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
    role = loaded["name"]
    key = rotation_key(root, role, loaded["harness"])
    count = rotation_index(registry, role, key)
    chosen = spawn_entry(root, role, loaded["harness"], count)
    model = chosen["model"]
    preset = load_preset(root, chosen["id"])
    warning = model_warning(preset, model)
    if warning:
        print(warning, file=sys.stderr)
    native = str(uuid.uuid4()) if preset["session_id"] == "generated" else ""
    lead = registry["seats"].get("lead") or {}
    goal = lead.get("branch", "") if lead.get("state") not in ("joined", "killed") else ""
    if seat == "lead":
        worktree, branch = _worktree(root, f"goal-{fleet_id()}")
    elif loaded["worktree"]:
        worktree, branch = _worktree(root, seat, goal)
    else:
        worktree, branch = "", ""
    seeded = empty_record()
    seeded["delivered"] = _present(root, seat)
    mailed = write_mail(root, seat, task) if task is not None else None
    if mailed is not None:
        seeded["inflight"].append(mailed.relative_to(_board(root)).as_posix())
    try:
        window = _open(
            root,
            registry,
            seat,
            loaded,
            preset,
            worktree=worktree,
            session_id=native,
            model=model,
            first="start",
            restarted=False,
            record=seeded,
            first_launch=True,
            task=mailed,
            branch=branch if seat == "lead" else "",
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
        fresh["rotation"][role] = {
            "list": key,
            "next": max(rotation_index(fresh, role, key), count) + 1,
        }
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
    if seat == "lead" and not os.environ.get("FOIL_SEAT_ID", "").strip():
        print(_supervisor_note(root, worktree, branch))


def _supervisor_note(root: Path, worktree: str, branch: str) -> str:
    """What whoever started a launched lead needs to follow it, under 1 KB (F45)."""

    fleet = fleet_id()
    status = _board(root) / "status.md"
    return "\n".join(
        [
            f"Lead started in fleet {fleet} on branch {branch} ({worktree}).",
            "Hear its mail to user: foil mail wait --all-fleets (in the background).",
            f"Answer it: FOIL_FLEET={fleet} foil send lead TEXT.",
            f"Progress: {status}.",
            f"When status.md says done, merge {branch}.",
        ]
    )


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
        if record.get("state") == "joined":
            if all_seats:
                continue
            raise FoilError(JOINED)
        window = record.get("window_id") or ""
        state = _state(registry, seat_name, record)
        if state == "alive":
            _end_turn(root, seat_name)
        if state != "killed":
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


def _end_turn(root: Path, seat: str) -> None:
    """End a running turn's process group. The runner does the same when its window closes."""

    pgid = load_record(_board(root), seat)["pgid"]
    if pgid > 1:
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGTERM)


def _relaunch(
    root: Path, registry: dict, name: str, record: dict, preset: dict, turns: dict, **opened
) -> None:
    """Launch a dead seat's runner again and store the new window."""

    loaded = load_template(root, record["template"])
    persona_text(loaded)
    old_window = record.get("window_id") or ""
    _remove_leftover(registry, root, name, old_window)
    window = _open(
        root,
        registry,
        name,
        loaded,
        preset,
        worktree=record["worktree"],
        session_id=record["session_id"],
        model=record.get("model") or "",
        record=turns,
        **opened,
    )
    record["window_id"] = window
    record["state"] = ""
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
        TmuxController().abandon_window(_target(registry, root, name, window))
        raise


def _restart(root: Path, registry: dict, name: str) -> None:
    record = dict(registry["seats"][name])
    preset = load_preset(root, record["harness"])
    turns = load_record(_board(root), name)
    # The runner learns an output preset's id; the registry has a generated one.
    record["session_id"] = turns["session_id"] or record["session_id"]
    if not record["session_id"] and preset["session_id"] == "generated":
        record["session_id"] = str(uuid.uuid4())
    # A generated id names no session until a turn on it has ended.
    first = "turn" if record["session_id"] and turns["last_exit"] is not None else "start"
    turns.update({"turn_started": None, "paused": False, "failures": 0, "pgid": 0})
    turns["session_id"] = record["session_id"]
    branch = record.get("branch", "") if name == "lead" else ""
    _relaunch(
        root, registry, name, record, preset, turns, first=first, restarted=True, branch=branch
    )


def _resume_paused(root: Path, name: str) -> None:
    marker = record_path(_board(root), name, ".resume")
    write_bytes(marker, b"")


def resume_seats(root: Path, name: str | None) -> int:
    registry = load_registry(root)
    if name is not None:
        _known(registry, name)
        state = _shown_state(root, registry, name, registry["seats"][name])
        if state == "joined":
            raise FoilError(JOINED)
        if state == "killed":
            raise FoilError(f"foil: seat '{name}' is killed")
        if state == "paused":
            _resume_paused(root, name)
        elif state != "dead":
            print(f"foil: seat '{name}' is {state}")
            return 0
        else:
            _restart(root, registry, name)
        return 0
    failures: list[str] = []
    for seat_name in sorted(registry["seats"]):
        state = _shown_state(root, registry, seat_name, registry["seats"][seat_name])
        if state == "paused":
            _resume_paused(root, seat_name)
            continue
        if state != "dead":
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


def handoff_on_board(root: Path, name: str) -> Path | None:
    """board/handoffs/<name>.md when no part of it from the board down is a symlink."""

    board = fleet_dir(root) / "board"
    handoffs = board / "handoffs"
    handoff = handoffs / f"{name}.md"
    for part in (board, handoffs, handoff):
        if part.is_symlink():
            return None
    if not handoff.is_file() or not handoff.resolve().is_relative_to(board.resolve()):
        return None
    return handoff


def renew_seat(root: Path, name: str) -> None:
    """A fresh session on the same name, worktree, and branch, after its handoff (F44)."""

    registry = load_registry(root)
    record = dict(_known(registry, name))
    state = _state(registry, name, record)
    if state == "joined":
        raise FoilError(JOINED)
    if state == "killed":
        raise FoilError(f"foil: seat '{name}' is killed")
    handoff = handoff_on_board(root, name)
    if handoff is None:
        raise FoilError(f"foil: write board/handoffs/{name}.md first")
    loaded = load_template(root, record["template"])
    persona_text(loaded)
    role = loaded["name"]
    key = rotation_key(root, role, loaded["harness"])
    count = rotation_index(registry, role, key)
    chosen = spawn_entry(root, role, loaded["harness"], count)
    preset = load_preset(root, chosen["id"])
    warning = model_warning(preset, chosen["model"])
    if warning:
        print(warning, file=sys.stderr)
    record.update(
        {
            "harness": chosen["id"],
            "model": chosen["model"],
            "session_id": str(uuid.uuid4()) if preset["session_id"] == "generated" else "",
        }
    )
    present = _present(root, name)
    branch = record.get("branch", "") if name == "lead" else ""
    if state == "dead":
        turns = load_record(_board(root), name)
        fresh = empty_record()
        fresh["delivered"] = sorted({*turns["delivered"], *turns["inflight"], *present})
        _relaunch(
            root, registry, name, record, preset, fresh,
            first="start", restarted=False, handoff=handoff, branch=branch,
        )
    else:
        instruction = _write_instruction(
            root, name, loaded, restarted=False, handoff=handoff, branch=branch
        )
        _write_plan(
            root,
            registry,
            name,
            loaded,
            preset,
            worktree=record["worktree"],
            session_id=record["session_id"],
            model=record["model"],
            first="start",
            restarted=False,
            instruction=instruction,
        )
        write_bytes(record_path(_board(root), name, ".renew"), json.dumps(present).encode())

        def apply(fresh: dict) -> None:
            current = fresh["seats"].get(name)
            if current is None or current.get("window_id") != record.get("window_id"):
                raise FoilError(f"foil: seat '{shown(name)}' changed")
            current.update({key: record[key] for key in ("harness", "model", "session_id")})

        update_registry(root, apply)

    def rotate(fresh: dict) -> None:
        fresh["rotation"][role] = {
            "list": key,
            "next": max(rotation_index(fresh, role, key), count) + 1,
        }

    update_registry(root, rotate)
    print(f"renewed seat '{name}' on {entry_label(chosen)}")


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
    root: Path,
    registry: dict,
    *,
    only: str | None = None,
    owned: bool = False,
    fleet: str | None = None,
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
        turns = load_record(fleet_dir(root, fleet) / "board", seat_name)
        shown_state = state
        if state == "alive":
            shown_state = "paused" if turns["paused"] else "idle"
            if not turns["paused"] and turns["turn_started"] is not None:
                shown_state = "working"
        rows.append(
            {
                "name": seat_name,
                "template": record["template"],
                "state": shown_state,
                "alive": state == "alive",
                "session_id": turns["session_id"] or record.get("session_id", ""),
                "turn_started": turns["turn_started"] if state == "alive" else None,
                "last_exit": turns["last_exit"],
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


def list_seats(
    root: Path, *, as_json: bool = False, archived: bool = False, all_fleets: bool = False
) -> None:
    if archived:
        if all_fleets:
            raise FoilError("foil: --archived takes no --all-fleets")
        _list_archived(root, as_json=as_json)
        return
    keys = (
        "name",
        "template",
        "state",
        "worktree",
        "harness",
        "model",
        "session_id",
        "turn_started",
        "last_exit",
        "description",
    )
    rows = []
    for fleet in fleet_ids(root) if all_fleets else [None]:
        with in_fleet(fleet):
            for row in seat_rows(root, load_registry(root)):
                picked = {key: row[key] for key in keys}
                if fleet is not None:
                    picked["fleet"] = fleet
                rows.append(picked)
    if as_json:
        print(json.dumps(rows, sort_keys=True))
        return
    for row in rows:
        launched = f"{row['harness']}/{row['model']}" if row["model"] else row["harness"]
        lead = f"{row['fleet']}\t" if "fleet" in row else ""
        print(
            f"{lead}{row['name']}\t{row['template']}\t{row['state']}\t"
            f"{row['worktree']}\t{launched}\t{row['session_id'] or '-'}\t{row['description']}"
        )


def peek_seat(root: Path, name: str, *, lines: int) -> None:
    sys.stdout.write(peek_text(root, name, lines=lines))


def peek_text(root: Path, name: str, *, lines: int, fleet: str | None = None) -> str:
    if lines < 1:
        raise FoilError("foil: invalid lines")
    registry = load_registry(root, fleet)
    record = _known(registry, name)
    state = _state(registry, name, record)
    if state == "joined":
        raise FoilError(JOINED)
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
