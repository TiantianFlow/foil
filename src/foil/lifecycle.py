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
    load_preset,
    load_template,
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
        installed_harness()
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    for relative in SKELETON:
        path = root / relative
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    ensure_exclude(toplevel)
    ensure_board(toplevel)
    ensure_registry(toplevel)
    write_default_templates(toplevel)


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


def _worktree(toplevel: Path, seat: str) -> tuple[str, str]:
    branch = f"foil/{seat}"
    if _git(toplevel, ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        raise FoilError(f"foil: branch '{branch}' already exists")
    destination = (toplevel.parent / f"{toplevel.name}.foil" / seat).resolve()
    project = toplevel.resolve()
    inside = destination == project or project in destination.parents
    if inside or (project / "worktrees") in destination.parents:
        raise FoilError("foil: refusing worktree inside the project")
    if destination.exists() or destination.is_symlink():
        raise FoilError("foil: worktree path already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = _git(toplevel, ["worktree", "add", "-b", branch, str(destination)])
    if created.returncode != 0:
        raise FoilError("foil: could not create worktree")
    return str(destination), branch


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
foil memory add TEXT [--replaces ID]
foil memory list [--all] [--json]
foil memory accept ID
foil memory reject ID"""

_WORKER_COMMANDS = """foil seat list [--json]
foil seat peek NAME [--lines N]
foil send TO TEXT
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
    persona = template["persona"]
    if persona and "\n" not in persona and "\r" not in persona and not persona.startswith("/"):
        path = template["path"].parent / persona
        if path.is_file() and not path.is_symlink():
            return f"Read `{path.resolve()}` untouched."
    return persona or "none"


def _instruction(root: Path, seat: str, template: dict, *, restarted: bool) -> str:
    board = (foil_root(root) / "board").resolve()
    lessons = _accepted(root)
    learned = "none yet"
    if lessons:
        learned = "\n".join(f"- {item}: {text}" for item, text in lessons)
    lead = template["name"] == "lead"
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
        "Mail: `foil send TO TEXT`. A nudge line is `from path`; read that file.",
        "There is no ack command.",
        "Notes: write files under `board/notes/`. They wake no one.",
        "Contracts (Foil does not read them):",
        "status/v1 (state working|blocked|done, updated, questions),",
        "task/v1 (id, owner, state open|doing|done, acceptance),",
        "result/v1 (task, author, branch, outcome pass|fail).",
        f"Worktree: {work}",
        f"Persona: {_persona_line(template)}",
        f"Accepted lessons: {learned}",
    ]
    if restarted:
        lines.append(f"You were restarted. Re-read `{board / 'mail' / seat}/`.")
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
                "The roster is `.foil/templates/*.toml`. There is no roster command.",
                "Keep `board/status.md` current.",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_instruction(root: Path, seat: str, template: dict, *, restarted: bool) -> Path:
    path = (foil_root(root) / "run" / "instructions" / f"{seat}.md").resolve()
    text = _instruction(root, seat, template, restarted=restarted)
    scan(text)
    write_bytes(path, text.encode())
    return path


def _native_resume(preset: dict, session_id: str) -> bool:
    argv = preset.get("resume") or []
    if not argv:
        return False
    if preset["session_id"] == "generated" and session_id:
        return True
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
    prompt = f"Read {instruction} first."
    argv = expand_argv(
        preset,
        model=template["model"] or None,
        prompt=prompt,
        session_id=session_id or None,
        permission=template["permission"],
        resume=native,
    )
    forward = list(preset["env"])
    if "PATH" not in forward:
        forward.append("PATH")
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


def _state(record: dict[str, str]) -> str:
    if record.get("state") == "killed":
        return "killed"
    window = record.get("window_id") or ""
    if window and TmuxController().window_exists(window):
        return "alive"
    return "dead"


def spawn_seat(
    root: Path,
    template: str,
    *,
    name: str | None = None,
    task: str | None = None,
) -> None:
    loaded = load_template(root, template)
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
    preset = load_preset(root, loaded["harness"])
    stored = "" if loaded["harness"] != record["harness"] else record["session_id"]
    native = _native_resume(preset, stored)
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


def resume_seats(root: Path, name: str | None) -> None:
    registry = load_registry(root)
    if name is not None:
        _known(registry, name)
        state = _state(registry["seats"][name])
        if state == "killed":
            raise FoilError(f"foil: seat '{name}' is killed")
        if state == "alive":
            print(f"foil: seat '{name}' is alive")
            return
        _restart(root, registry, name)
        return
    for seat_name in sorted(registry["seats"]):
        if _state(registry["seats"][seat_name]) == "dead":
            _restart(root, registry, seat_name)


def list_seats(root: Path, *, as_json: bool = False) -> None:
    registry = load_registry(root)
    rows = [
        {
            "name": seat_name,
            "template": record["template"],
            "state": _state(record),
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
    state = _state(record)
    if state != "alive":
        raise FoilError(f"foil: seat '{name}' is {state}")
    try:
        text = TmuxController().capture_pane(record["window_id"], lines)
    except TmuxError as exc:
        raise FoilError("foil: could not peek seat") from exc
    sys.stdout.write(text)
