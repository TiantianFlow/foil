"""Seat lifecycle. Init creates the folder; spawn launches a seat."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from foil.board import ensure_board, send_mail
from foil.errors import FoilError
from foil.presets import (
    expand_argv,
    installed_harness,
    installed_presets,
    load_preset,
    load_template,
    persona_text,
    write_default_templates,
)
from foil.project import (
    SKELETON,
    ensure_exclude,
    foil_root,
    git_toplevel,
    require_host_tools,
)
from foil.store import (
    SAFE_ID,
    SEAT_FIELDS,
    ensure_registry,
    load_registry,
    read_json,
    save_registry,
    scan,
    write_bytes,
)
from foil.tmux import TmuxController, TmuxError, TmuxTarget


def _names(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def _print_init_report(toplevel: Path, had_templates: set[str], had_personas: set[str]) -> None:
    presets, skipped = installed_presets(toplevel)
    found = [item["id"] for item in presets]
    first = found[0] if found else ""
    second = found[1] if len(found) > 1 else first
    print(f"Installed harnesses, in id order (a tiebreak, not a ranking): {_names(found)}")
    for path, reason in skipped:
        print(f"Skipped {path}: {reason}")
    roles = ("lead", "implementer", "reviewer")
    stored: list[str] = []
    for role in roles:
        chosen = load_template(toplevel, role)["harness"]
        fresh = role not in had_templates
        second_reviewer = role == "reviewer" and chosen == second and second != first
        if fresh and second_reviewer:
            why = "second installed id"
        elif fresh and chosen == first:
            why = "first installed id"
        else:
            why = "left alone"
        print(f"{role}: {chosen} ({why})")
        stored.append(chosen)
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
    persona_dir = foil_root(toplevel) / "templates" / "personas"
    personas = sorted(
        path.stem
        for path in persona_dir.glob("*.md")
        if path.is_file() and not path.is_symlink()
    )
    template_dir = foil_root(toplevel) / "templates"
    template_names = {path.stem for path in template_dir.glob("*.toml")}
    available_personas = [name for name in personas if name not in template_names]

    print("Wrote templates: " + _names([role for role in roles if role not in had_templates]))
    print("Left templates: " + _names([role for role in roles if role in had_templates]))
    print("Wrote personas: " + _names([name for name in personas if name not in had_personas]))
    print("Left personas: " + _names([name for name in personas if name in had_personas]))
    if available_personas:
        print(f"Available personas (use 'foil roster add'): {_names(available_personas)}")
    permissions = ", ".join(
        f"{role} {load_template(toplevel, role)['permission']}" for role in roles
    )
    print(f"permission: {permissions}.")
    print(
        "ask stops a seat at its first approval prompt; auto asks the harness to skip "
        "approval prompts, though some harnesses still ask for some commands. "
        "Set it in each .foil/templates/<role>.toml; auto on the lead alone does not "
        "let the workers run unattended."
    )
    print("Read .foil/skills/operator.md and follow it. My goal: <goal>.")


def init_project(directory: str | None) -> None:
    require_host_tools()
    start = Path(directory).expanduser().resolve() if directory else Path.cwd()
    if not start.exists():
        raise FoilError("foil: not a git repository")
    toplevel = git_toplevel(start)
    root = foil_root(toplevel)
    if any(
        not ((root / "templates" / f"{role}.toml").exists())
        for role in ("lead", "implementer", "reviewer")
    ):
        installed_harness(toplevel)
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for relative in SKELETON:
        path = root / relative
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    ensure_exclude(toplevel)
    ensure_board(toplevel)
    ensure_registry(toplevel)
    template_dir = root / "templates"
    persona_dir = template_dir / "personas"
    roles = ("lead", "implementer", "reviewer")
    had_templates = {role for role in roles if (template_dir / f"{role}.toml").exists()}
    had_personas = {
        path.stem
        for path in persona_dir.glob("*.md")
        if path.is_file() and not path.is_symlink()
    }
    write_default_templates(toplevel)
    _print_init_report(toplevel, had_templates, had_personas)


def _shown(value: str) -> str:
    return value.replace("\n", "").replace("\r", "")


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
        raise FoilError(f"foil: seat '{_shown(requested or '')}' already exists")
    if requested:
        if not SAFE_ID.fullmatch(requested):
            raise FoilError(f"foil: invalid name '{_shown(requested)}'")
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


def _session_name(toplevel: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", toplevel.name).strip("-")[:32] or "repo"
    digest = hashlib.sha256(str(toplevel.resolve()).encode()).hexdigest()[:8]
    return f"foil-{slug}-{digest}"


_LEAD_COMMANDS = """foil seat spawn TEMPLATE [--name NAME] [--task TEXT]
foil seat kill NAME
foil seat resume [NAME]
foil seat list [--json]
foil seat peek NAME [--lines N]
foil send TO TEXT
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


def _instruction(root: Path, seat: str, template: dict, *, restarted: bool) -> str:
    board = (foil_root(root) / "board").resolve()
    lessons = _accepted(root)
    learned = "none yet"
    if lessons:
        learned = "\n".join(f"- {item}: {text}" for item, text in lessons)
    lead = template["name"] == "lead"
    skill = foil_root(root) / "skills" / ("lead.md" if lead else "worker.md")
    skill_text = skill.read_text(encoding="utf-8").strip() if skill.is_file() else ""
    work = (
        "Stay in your worktree. Do not modify the project toplevel. "
        "Killing you will not delete your branch."
        if template["worktree"]
        else "You have no worktree. Killing you will not delete your branch."
    )
    lines = [
        f"You are seat `{seat}`. Lead is `lead`.",
        f"Board: `{board}`.",
        f"Identity is `FOIL_SEAT_ID` (yours is `{seat}`). You cannot change it with flags.",
        "Commands you may run:",
        _LEAD_COMMANDS if lead else _WORKER_COMMANDS,
        "Mail: `foil send TO TEXT`. A nudge line is `from path`. Read it with `foil mail read`.",
        "There is no ack command.",
        "Notes: write files under `board/notes/`. They wake no one.",
        "Contracts (parsed only to print; Foil does not act on them):",
        "status/v1 (state working|blocked|done, updated, questions),",
        "task/v1 (id, owner, state open|doing|done, acceptance),",
        "result/v1 (task, author, branch, outcome pass|fail).",
        f"Worktree: {work}",
        f"Persona:\n{_persona_line(template)}",
        f"Skill: `{skill.resolve()}`.",
        *([skill_text] if skill_text else []),
        f"Accepted lessons: {learned}",
    ]
    if restarted:
        lines.append(
            "You were restarted. List your mail with `foil mail list` "
            "and read each file with `foil mail read`."
        )
    if lead:
        roster = []
        directory = foil_root(root) / "templates"
        for path in sorted(directory.glob("*.toml")):
            if path.is_symlink() or not path.is_file():
                continue
            item = load_template(root, path.stem)
            flag = "yes" if item["worktree"] else "no"
            roster.append(f"- {item['name']}: harness {item['harness']}, worktree {flag}")
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


def _write_instruction(root: Path, seat: str, template: dict, *, restarted: bool) -> Path:
    path = (foil_root(root) / "run" / "instructions" / f"{seat}.md").resolve()
    body = _instruction(root, seat, template, restarted=restarted).rstrip("\n")
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
) -> str:
    cwd = Path(worktree) if worktree else root
    instruction = _write_instruction(root, seat, template, restarted=restarted)
    prompt = instruction.read_text(encoding="utf-8")
    argv = expand_argv(
        preset,
        model=template["model"] or None,
        prompt=prompt,
        session_id=session_id or None,
        permission=template["permission"],
        resume=native,
    )
    forward = list(preset["env"])
    for name in ("PATH", "HOME"):
        if name not in forward:
            forward.append(name)
    plan_path = (foil_root(root) / "run" / "plans" / f"{seat}.json").resolve()
    plan = {
        "argv": argv,
        "cwd": str(cwd.resolve()),
        "env": {"FOIL_SEAT_ID": seat},
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


def spawn_seat(
    root: Path,
    template: str,
    *,
    name: str | None = None,
    task: str | None = None,
) -> None:
    loaded = load_template(root, template)
    persona_text(loaded)
    if task is not None:
        scan(task)
    registry = load_registry(root)
    seat = _seat_name(registry, loaded["name"], name)
    preset = load_preset(root, loaded["harness"])
    native = str(uuid.uuid4()) if preset["session_id"] == "generated" else ""
    worktree, branch = _worktree(root, seat) if loaded["worktree"] else ("", "")
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
    )
    record = {key: "" for key in SEAT_FIELDS}
    record.update(
        {
            "name": seat,
            "template": loaded["name"],
            "harness": loaded["harness"],
            "window_id": window,
            "worktree": worktree,
            "branch": branch,
            "session_id": native,
        }
    )
    if loaded["name"] == "lead":
        registry["lead"] = "lead"
    registry["seats"][seat] = record
    try:
        save_registry(root, registry)
    except Exception:
        TmuxController().abandon_window(
            TmuxTarget(
                session_name=str(registry.get("tmux_session") or ""),
                window_name=seat,
                session_id=None,
                window_id=window,
            )
        )
        raise
    if task is not None:
        send_mail(root, seat, task)


def _known(registry: dict, name: str | None) -> dict[str, str]:
    if not name or not SAFE_ID.fullmatch(name) or name not in registry["seats"]:
        raise FoilError(f"foil: unknown seat '{_shown(name or '')}'")
    return registry["seats"][name]


def kill_seats(
    root: Path, *, name: str | None = None, all_seats: bool = False
) -> None:
    registry = load_registry(root)
    names = list(registry["seats"]) if all_seats else [_known(registry, name)["name"]]
    for seat_name in names:
        record = registry["seats"][seat_name]
        window = record.get("window_id") or ""
        if record.get("state") != "killed" and window:
            target = TmuxTarget(
                session_name=str(registry.get("tmux_session") or _session_name(root)),
                window_name=seat_name,
                session_id=None,
                window_id=window,
            )
            try:
                TmuxController().stop_verified(str(registry["fleet_id"]), seat_name, target)
            except TmuxError as exc:
                raise FoilError("foil: could not stop seat") from exc
        record["state"] = "killed"
        save_registry(root, registry)


def _restart(root: Path, registry: dict, name: str) -> None:
    record = registry["seats"][name]
    loaded = load_template(root, record["template"])
    persona_text(loaded)
    preset = load_preset(root, loaded["harness"])
    stored = "" if loaded["harness"] != record["harness"] else record["session_id"]
    native = _native_resume(preset, stored, worktree=record.get("worktree") or "")
    if native:
        session = stored
    elif preset["session_id"] == "generated":
        session = str(uuid.uuid4())
    else:
        session = ""
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
    )
    record["window_id"] = window
    record["state"] = ""
    record["session_id"] = session
    record["harness"] = loaded["harness"]
    try:
        save_registry(root, registry)
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


def list_seats(root: Path, *, as_json: bool = False) -> None:
    registry = load_registry(root)
    rows = [
        {
            "name": seat_name,
            "template": record["template"],
            "state": _state(registry, seat_name, record),
            "worktree": record["worktree"],
        }
        for seat_name, record in sorted(registry["seats"].items())
    ]
    if as_json:
        print(json.dumps(rows, sort_keys=True))
        return
    for row in rows:
        print(f"{row['name']}\t{row['template']}\t{row['state']}\t{row['worktree']}")


def peek_seat(root: Path, name: str, *, lines: int) -> None:
    if lines < 1:
        raise FoilError("foil: invalid lines")
    registry = load_registry(root)
    record = _known(registry, name)
    state = _state(registry, name, record)
    if state != "alive":
        raise FoilError(f"foil: seat '{name}' is {state}")
    try:
        text = TmuxController().capture_pane(record["window_id"], lines)
    except TmuxError as exc:
        raise FoilError("foil: could not peek seat") from exc
    sys.stdout.write(text)
