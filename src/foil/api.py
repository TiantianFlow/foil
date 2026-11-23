"""One function per command.

Each takes the checkout (``project``), the fleet id, and the caller.
``caller`` is ``user``, ``lead``, or a seat id. ``inside`` is true when
that caller is a seat: a launched lead is ``lead`` with ``inside`` true,
and a joined lead is ``lead`` with ``inside`` false. ``user`` is the human
even while a lead is joined. ``VERSION`` is the major the Bridge pins to.

A function returns the JSON value of that command. A read command's
``--json`` prints that same value. The call binds project, fleet, and
caller for its thread and does not read or write them on the process.
"""

from __future__ import annotations

from pathlib import Path

from foil.answer import answer_question
from foil.board import post_room, send_mail
from foil.board_ops import board_list_payload, board_read_payload, show_board_list, show_board_read
from foil.delivery import pause_fleet, resume_fleet
from foil.errors import FoilError
from foil.inbox import (
    collect,
    collect_brief,
    collect_brief_projects,
    collect_projects,
    show_brief,
    show_items,
)
from foil.joined import join_lead, leave_lead, mail_wait
from foil.journal import check_line, check_since, note, read_log, show_log, stream
from foil.lifecycle import (
    archive_seat,
    init_project,
    kill_seats,
    peek_text,
    renew_seat,
    resume_seats,
    seat_list_rows,
    show_peek,
    show_seat_list,
    spawn_seat,
)
from foil.mail_ops import (
    mail_read_payload,
    mail_rows,
    sent_rows,
    show_mail_list,
    show_mail_read,
    show_sent,
)
from foil.memory import (
    accept_lesson,
    add_lesson,
    read_lessons,
    reject_lesson,
    show_lessons,
)
from foil.merge import merge_goal
from foil.project import foil_root, is_initialized, refuse_legacy_layout
from foil.roster_ops import (
    add_template,
    remove_template,
    roster_list_payload,
    roster_show_payload,
    show_roster_list,
    show_roster_template,
    update_template,
)
from foil.scope import bound
from foil.store import RESERVED, SAFE_ID, fleet_dir, fleet_id, fleet_ids

VERSION = 1

_OUTSIDE = "outside"
_LEAD = "lead"
_WORKER = "worker"

_ALLOWED = {
    "init": {_OUTSIDE},
    "spawn": {_OUTSIDE, _LEAD},
    "kill": {_OUTSIDE, _LEAD},
    "kill-all": {_OUTSIDE},
    "join": {_OUTSIDE},
    "leave": {_OUTSIDE},
    "archive": {_OUTSIDE, _LEAD},
    "resume": {_OUTSIDE, _LEAD},
    "renew": {_OUTSIDE, _LEAD},
    "list": {_OUTSIDE, _LEAD, _WORKER},
    "peek": {_OUTSIDE, _LEAD, _WORKER},
    "send": {_OUTSIDE, _LEAD, _WORKER},
    "answer": {_OUTSIDE},
    "post": {_OUTSIDE, _LEAD, _WORKER},
    "mail-read": {_OUTSIDE, _LEAD, _WORKER},
    "mail-list": {_OUTSIDE, _LEAD, _WORKER},
    "mail-wait": {_OUTSIDE},
    "board-read": {_OUTSIDE, _LEAD, _WORKER},
    "board-list": {_OUTSIDE, _LEAD, _WORKER},
    "memory-add": {_OUTSIDE, _LEAD, _WORKER},
    "memory-list": {_OUTSIDE, _LEAD, _WORKER},
    "memory-accept": {_OUTSIDE, _LEAD},
    "memory-reject": {_OUTSIDE, _LEAD},
    "roster-list": {_OUTSIDE, _LEAD},
    "roster-show": {_OUTSIDE, _LEAD},
    "roster-add": {_OUTSIDE, _LEAD},
    "roster-update": {_OUTSIDE, _LEAD},
    "roster-remove": {_OUTSIDE, _LEAD},
    "journal": {_OUTSIDE, _LEAD, _WORKER},
    "decide": {_OUTSIDE, _LEAD, _WORKER},
    "fleet-pause": {_OUTSIDE, _LEAD},
    "fleet-resume": {_OUTSIDE, _LEAD},
    "fleet-merge": {_OUTSIDE},
    "inbox": {_OUTSIDE, _LEAD},
    "brief": {_OUTSIDE, _LEAD},
    "master-start": {_OUTSIDE},
    "master-stop": {_OUTSIDE},
    "master-renew": {_OUTSIDE},
    "doctor": {_OUTSIDE},
    "bridge": {_OUTSIDE},
}


def _kind(caller: str, *, inside: bool) -> str:
    if not inside:
        return _OUTSIDE
    if caller == "lead":
        return _LEAD
    return _WORKER


def _authorize(action: str, *, caller: str, inside: bool, name: str | None = None) -> None:
    if _kind(caller, inside=inside) not in _ALLOWED[action]:
        raise FoilError("foil: not allowed")
    if action == "kill" and inside and caller == "lead" and name == "lead":
        raise FoilError("foil: cannot kill the lead")


def _enter(project: Path | None, fleet: str, caller: str, *, inside: bool, pinned: bool):
    """Bind the call. A seat id is checked here, after the command's own refusal.

    Fleet text is checked when an operation reads it, as ``fleet_id`` always did.
    """

    if inside:
        if not SAFE_ID.fullmatch(caller) or caller in RESERVED:
            raise FoilError("foil: unknown seat")
    elif caller not in {"user", "lead"}:
        raise FoilError("foil: unknown caller")
    return bound(project, fleet, caller, inside=inside, pinned=pinned)


def _root(project: Path | None) -> Path:
    if project is None:
        raise FoilError("foil: not initialized")
    refuse_legacy_layout(project)
    if not is_initialized(project):
        raise FoilError("foil: not initialized")
    return project


def _roster_root(project: Path | None, *, project_layer: bool) -> Path:
    if project_layer:
        if project is None or not is_initialized(project):
            raise FoilError("foil: --project needs a checkout")
        refuse_legacy_layout(project)
        return project
    if project is None:
        return Path.cwd()
    return project


def init(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    directory: str | None = None,
) -> dict:
    _authorize("init", caller=caller, inside=inside)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        init_project(directory)
    return {"ok": True}


def seat_spawn(
    project: Path | None,
    fleet: str,
    caller: str,
    template: str,
    *,
    inside: bool = False, pinned: bool = True,
    name: str | None = None,
    task: str | None = None,
) -> dict:
    _authorize("spawn", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        spawn_seat(root, template, name=name, task=task)
    return {"ok": True}


def seat_kill(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    name: str | None = None,
    all_seats: bool = False,
) -> dict:
    if all_seats:
        _authorize("kill-all", caller=caller, inside=inside)
        if name:
            raise FoilError("foil: NAME and --all cannot be combined")
    else:
        if not name:
            raise FoilError("foil: NAME is required")
        _authorize("kill", caller=caller, inside=inside, name=name)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        kill_seats(root, name=name, all_seats=all_seats)
    return {"ok": True}


def seat_archive(
    project: Path | None,
    fleet: str,
    caller: str,
    name: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("archive", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        archive_seat(root, name)
    return {"ok": True}


def seat_resume(
    project: Path | None,
    fleet: str,
    caller: str,
    name: str | None,
    *,
    inside: bool = False, pinned: bool = True,
) -> dict:
    _authorize("resume", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        code = resume_seats(root, name)
    return {"code": code}


def seat_renew(
    project: Path | None,
    fleet: str,
    caller: str,
    name: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("renew", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        renew_seat(root, name)
    return {"ok": True}


def seat_list(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
    archived: bool = False,
    all_fleets: bool = False,
) -> list:
    _authorize("list", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        rows = seat_list_rows(root, archived=archived, all_fleets=all_fleets)
        show_seat_list(rows, as_json=as_json, archived=archived)
    return rows


def seat_peek(
    project: Path | None,
    fleet: str,
    caller: str,
    name: str,
    *,
    inside: bool = False, pinned: bool = True,
    lines: int = 40,
    as_json: bool = False,
) -> dict:
    _authorize("peek", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        payload = {"name": name, "text": peek_text(root, name, lines=lines)}
        show_peek(payload, as_json=as_json)
    return payload


def seat_join(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    harness: str | None = None,
    session: str | None = None,
    take: bool = False,
) -> dict:
    _authorize("join", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        join_lead(root, harness=harness, session=session, take=take)
    return {"ok": True}


def seat_leave(
    project: Path | None, fleet: str, caller: str, *, inside: bool = False, pinned: bool = True
) -> dict:
    _authorize("leave", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        leave_lead(root)
    return {"ok": True}


def answer(
    project: Path | None,
    fleet: str,
    caller: str,
    question_id: str,
    text: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    read_text=None,
) -> dict:
    """Mail the asking lead as ``user``, quoting the question id.

    ``read_text`` expands ``-`` after the refusal. Callers pass the message.
    """

    _authorize("answer", caller=caller, inside=inside)
    if caller != "user":
        raise FoilError("foil: answer is from user")
    body = text if read_text is None else read_text(text)
    if not body.strip():
        raise FoilError("foil: answer text is empty")
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        answer_question(root, question_id, body)
    return {"ok": True}


def send(
    project: Path | None,
    fleet: str,
    caller: str,
    to: str,
    text: str,
    *,
    inside: bool = False, pinned: bool = True,
    read_text=None,
) -> dict:
    """``read_text`` expands ``-`` after the refusal. Callers pass the message."""

    _authorize("send", caller=caller, inside=inside)
    body = text if read_text is None else read_text(text)
    if to == "master":
        from foil.master import deliver

        with _enter(project, fleet, caller, inside=inside, pinned=pinned):
            deliver(body, project if project is not None and is_initialized(project) else None)
        return {"ok": True}
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        send_mail(root, to, body)
    return {"ok": True}


def post(
    project: Path | None,
    fleet: str,
    caller: str,
    text: str | None,
    *,
    inside: bool = False, pinned: bool = True,
    reply: str | None = None,
    decline: bool = False,
    members: str | None = None,
    close: bool = False,
    read_text=None,
) -> dict:
    """``text`` is the argv token. ``read_text`` expands ``-`` after the refusal.

    An in-process caller passes the message and leaves ``read_text`` unset.
    """

    _authorize("post", caller=caller, inside=inside)
    if decline:
        if text is not None:
            raise FoilError("foil: --decline takes no text")
        body = ""
    else:
        if text is None:
            raise FoilError("foil: TEXT is required")
        body = text if read_text is None else read_text(text)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        post_room(root, body, reply=reply, decline=decline, members=members, close=close)
    return {"ok": True}


def mail_read(
    project: Path | None,
    fleet: str,
    caller: str,
    path: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
) -> dict:
    _authorize("mail-read", caller=caller, inside=inside)
    _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        payload = mail_read_payload(path)
        show_mail_read(payload, as_json=as_json)
    return payload


def mail_list(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
    sent: bool = False,
) -> list:
    _authorize("mail-list", caller=caller, inside=inside)
    _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        if sent:
            rows = sent_rows()
            show_sent(rows, as_json=as_json)
        else:
            rows = mail_rows()
            show_mail_list(rows, as_json=as_json)
    return rows


def mail_wait_for(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    timeout: int = 1800,
    wake: bool = False,
    all_fleets: bool = False,
    as_json: bool = False,
) -> dict:
    """``mail wait``. Named so it does not shadow the joined helper."""

    if inside:
        raise FoilError("foil: mail wait is outside the fleet only")
    _authorize("mail-wait", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        return mail_wait(root, timeout=timeout, wake=wake, all_fleets=all_fleets, as_json=as_json)


def board_read(
    project: Path | None,
    fleet: str,
    caller: str,
    path: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
) -> dict:
    _authorize("board-read", caller=caller, inside=inside)
    _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        text, payload = board_read_payload(path)
        show_board_read(text, payload, as_json=as_json)
    return payload


def board_list(
    project: Path | None,
    fleet: str,
    caller: str,
    pattern: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
) -> dict:
    _authorize("board-list", caller=caller, inside=inside)
    _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        payload = board_list_payload(pattern)
        show_board_list(payload, as_json=as_json)
    return payload


def memory_add(
    project: Path | None,
    fleet: str,
    caller: str,
    text: str,
    *,
    inside: bool = False, pinned: bool = True,
    replaces: str | None = None,
    read_text=None,
) -> dict:
    """``read_text`` expands ``-`` after the refusal. Callers pass the lesson."""

    _authorize("memory-add", caller=caller, inside=inside)
    body = text if read_text is None else read_text(text)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        add_lesson(root, body, replaces=replaces)
    return {"ok": True}


def memory_accept(
    project: Path | None,
    fleet: str,
    caller: str,
    lesson_id: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("memory-accept", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        accept_lesson(root, lesson_id)
    return {"ok": True}


def memory_reject(
    project: Path | None,
    fleet: str,
    caller: str,
    lesson_id: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("memory-reject", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        reject_lesson(root, lesson_id)
    return {"ok": True}


def memory_list(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    all_lessons: bool = False,
    as_json: bool = False,
) -> list:
    _authorize("memory-list", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        lessons = read_lessons(root, all_lessons=all_lessons)
        show_lessons(lessons, all_lessons=all_lessons, as_json=as_json)
    return lessons


def roster_list(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
) -> dict:
    _authorize("roster-list", caller=caller, inside=inside)
    root = _roster_root(project, project_layer=False)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        payload = roster_list_payload(root)
        show_roster_list(payload, as_json=as_json)
    return payload


def roster_show(
    project: Path | None,
    fleet: str,
    caller: str,
    role: str,
    *,
    inside: bool = False, pinned: bool = True,
    as_json: bool = False,
) -> dict:
    _authorize("roster-show", caller=caller, inside=inside)
    root = _roster_root(project, project_layer=False)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        payload = roster_show_payload(root, role)
        show_roster_template(payload, as_json=as_json)
    return payload


def roster_add(
    project: Path | None,
    fleet: str,
    caller: str,
    role: str,
    *,
    inside: bool = False, pinned: bool = True,
    from_file: str | None = None,
    project_layer: bool = False,
    base: str | None = None,
) -> dict:
    _authorize("roster-add", caller=caller, inside=inside)
    if project_layer and inside:
        raise FoilError("foil: --project is outside the fleet only")
    if inside:
        raise FoilError("foil: roles are outside the fleet only")
    root = _roster_root(project, project_layer=project_layer)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        add_template(
            root, role, from_file=from_file, in_fleet=inside, project=project_layer, base=base
        )
    return {"ok": True}


def roster_update(
    project: Path | None,
    fleet: str,
    caller: str,
    role: str,
    field_value: str,
    *,
    inside: bool = False, pinned: bool = True,
    project_layer: bool = False,
    base: str | None = None,
) -> dict:
    _authorize("roster-update", caller=caller, inside=inside)
    if "=" not in field_value:
        raise FoilError("foil: FIELD=VALUE format required")
    field, value = field_value.split("=", 1)
    if project_layer and inside:
        raise FoilError("foil: --project is outside the fleet only")
    if field == "permission" and inside:
        raise FoilError("foil: permission is outside the fleet only")
    if inside and field in {"persona", "worktree"}:
        raise FoilError("foil: roles are outside the fleet only")
    root = _roster_root(project, project_layer=project_layer)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        update_template(
            root, role, field, value, in_fleet=inside, project=project_layer, base=base
        )
    return {"ok": True}


def roster_remove(
    project: Path | None,
    fleet: str,
    caller: str,
    role: str,
    *,
    inside: bool = False, pinned: bool = True,
    project_layer: bool = False,
    base: str | None = None,
) -> dict:
    _authorize("roster-remove", caller=caller, inside=inside)
    if project_layer and inside:
        raise FoilError("foil: --project is outside the fleet only")
    if inside:
        raise FoilError("foil: roles are outside the fleet only")
    root = _roster_root(project, project_layer=project_layer)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        remove_template(root, role, project=project_layer, base=base)
    return {"ok": True}


def journal(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    since: str | None = None,
    follow: bool = False,
    as_json: bool = False,
) -> list:
    _authorize("journal", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        if since is not None:
            check_since(since)
        directory = fleet_dir(root)
        if follow:
            stream(directory, since, as_json=as_json)
            return []
        events = read_log(directory, since)
        show_log(events, as_json=as_json)
    return events


def decide(
    project: Path | None,
    fleet: str,
    caller: str,
    text: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    why: str,
) -> dict:
    _authorize("decide", caller=caller, inside=inside)
    choice = check_line(text, "decision text")
    reason = check_line(why, "decision reason")
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        note(fleet_dir(root), "decision", by=caller, text=choice, why=reason)
    return {"ok": True}


def fleet_pause(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("fleet-pause", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        # The joined lead just ran this command, so the next one is not the first notice.
        pause_fleet(fleet_dir(root), told=caller == "lead" and not inside)
    print("foil: fleet paused")
    return {"paused": True}


def fleet_resume(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("fleet-resume", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        resume_fleet(fleet_dir(root))
    print("foil: fleet resumed")
    return {"paused": False}


def fleet_merge(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    into: str | None = None,
) -> dict:
    _authorize("fleet-merge", caller=caller, inside=inside)
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        merged = merge_goal(root, fleet_id(), into=into)
    print(f"foil: merged {merged['branch']} into {merged['into']}")
    return {"merged": True, **merged}


def _fleets(root: Path, *, all_fleets: bool) -> list[str]:
    if all_fleets:
        return fleet_ids(root)
    return [fleet_id()]


def _project_key(root: Path) -> str:
    return foil_root(root).name


def inbox(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    all_fleets: bool = False,
    all_projects: bool = False,
    as_json: bool = False,
) -> list:
    _authorize("inbox", caller=caller, inside=inside)
    if all_projects:
        items = collect_projects()
        show_items(items, scope="projects", as_json=as_json)
        return items
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        items = collect(root, _fleets(root, all_fleets=all_fleets), _project_key(root))
        show_items(items, scope="fleets" if all_fleets else "", as_json=as_json)
    return items


def master_start(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    harness: str | None = None,
) -> dict:
    _authorize("master-start", caller=caller, inside=inside)
    from foil.master import start

    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        start(harness)
    return {"ok": True}


def master_stop(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _authorize("master-stop", caller=caller, inside=inside)
    from foil.master import stop

    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        stop()
    return {"ok": True}


def master_renew(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    harness: str | None = None,
) -> dict:
    _authorize("master-renew", caller=caller, inside=inside)
    from foil.master import renew

    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        renew(harness)
    return {"ok": True}


def brief(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
    all_projects: bool = False,
    as_json: bool = False,
) -> list:
    _authorize("brief", caller=caller, inside=inside)
    if all_projects:
        records = collect_brief_projects()
        show_brief(records, projects_named=len(records) > 1, as_json=as_json)
        return records
    root = _root(project)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        records = collect_brief(root, [fleet_id()], _project_key(root))
        show_brief(records, projects_named=False, as_json=as_json)
    return records


def _outside(command: str, *, inside: bool) -> None:
    if inside:
        raise FoilError(f"foil: {command} is outside the fleet only")


def doctor(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _outside("doctor", inside=inside)
    _authorize("doctor", caller=caller, inside=inside)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        from foil.bridge_cmd import run_doctor

        return run_doctor()


def bridge(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _outside("bridge", inside=inside)
    _authorize("bridge", caller=caller, inside=inside)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        from foil.bridge_cmd import show_bridge

        return show_bridge()


def bridge_install(
    project: Path | None,
    fleet: str,
    caller: str,
    *,
    inside: bool = False,
    pinned: bool = True,
) -> dict:
    _outside("bridge", inside=inside)
    _authorize("bridge", caller=caller, inside=inside)
    with _enter(project, fleet, caller, inside=inside, pinned=pinned):
        from foil.bridge_cmd import install_bridge

        return install_bridge()
