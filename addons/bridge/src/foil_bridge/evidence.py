"""Pages the home links to. The GET writes nothing. Controls are a separate POST."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from foil.delivery import fleet_paused
from foil.errors import FoilError, shown
from foil.inbox import collect, collect_brief
from foil.journal import read_log
from foil.journal import render as render_event
from foil.lifecycle import seat_rows
from foil.project import checkout_for_key, foil_root
from foil.quote import decision_lines
from foil.store import fleet_dir, fleet_ids, load_registry
from foil_bridge import home
from foil_bridge.controls import needs_confirm, next_roles
from foil_bridge.terminal import attach_command, owned_worktree
from foil_bridge.transcript import LEVELS, NOTE, event_line, mask, read

FILE_CAP = 64 * 1024
LINE_BOUND = 8 * 1024
DIFF_CAP = 64 * 1024
JOURNAL_CAP = 40
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,200}")


def page(target: str, level: str = "summary") -> dict[str, Any] | None:
    """One evidence page, or None when ``target`` does not resolve."""

    described = home.describe(target)
    if described is None:
        return None
    chosen = level if level in LEVELS else "summary"
    try:
        checkout = checkout_for_key(described["project"])
        if foil_root(checkout).name != described["project"]:
            return None
    except (FoilError, OSError):
        return None
    kind = described["kind"]
    if kind == "project":
        return _fleets(described, checkout)
    if kind == "fleet":
        return _fleet(described, checkout)
    if kind == "file":
        return _file(described, checkout)
    if kind == "seat":
        return _seat(described, checkout, chosen)
    return None


def _fleets(described: dict[str, str], checkout: Path) -> dict[str, Any]:
    project = described["project"]
    rows = []
    for fleet in fleet_ids(checkout):
        directory = fleet_dir(checkout, fleet)
        board = directory / "board"
        run = directory / "run"
        # A symlinked board or run is followed by later reads. A symlinked
        # status.md is ignored by the inbox, so mail and seats still count.
        if _symlink(board) is not False or _symlink(run) is not False:
            items = []
        else:
            try:
                items = collect(checkout, [fleet], project)
            except (FoilError, OSError):
                items = []
        top = items[0] if items else None
        rows.append(
            {
                "fleet": fleet,
                "target": f"projects/{project}/fleets/{fleet}",
                "rank": None if top is None else top["rank"],
                "kind": "" if top is None else top["kind"],
                "title": "" if top is None else mask(str(top["title"])),
            }
        )
    return {
        "kind": "fleets",
        "title": "Fleets",
        "crumbs": _crumbs(project),
        "note": NOTE,
        "fleets": rows,
    }


def _fleet(described: dict[str, str], checkout: Path) -> dict[str, Any]:
    project, fleet = described["project"], described["fleet"]
    directory = fleet_dir(checkout, fleet)
    goal, state, paused, decisions, questions = _status_view(checkout, project, fleet, directory)
    try:
        registry = load_registry(checkout, fleet)
        rows = seat_rows(checkout, registry, fleet=fleet)
        session = str(registry.get("tmux_session") or "")
    except (FoilError, OSError):
        rows = []
        session = ""
    seats = []
    incoming = outgoing = 0
    for row in rows:
        totals = _meter(row["name"], project, fleet)
        incoming += totals["input"]
        outgoing += totals["output"]
        seats.append(
            {
                "name": row["name"],
                "template": row["template"],
                "state": row["state"],
                "branch": row["branch"],
                "target": f"projects/{project}/fleets/{fleet}/seats/{row['name']}",
                "tokens": totals,
            }
        )
    return {
        "kind": "fleet",
        "title": fleet,
        "crumbs": _crumbs(project, fleet),
        "note": NOTE,
        "goal": goal,
        "state": state,
        "paused": paused,
        "decisions": decisions,
        "journal": _journal(directory),
        "questions": questions,
        "seats": seats,
        "files": [
            {
                "path": rel,
                "target": f"projects/{project}/fleets/{fleet}/board/{rel}",
            }
            for rel in board_listing(directory / "board")
        ],
        "tokens": {"input": incoming, "output": outgoing},
        "roles": next_roles(checkout, fleet),
        "pause_confirm": needs_confirm(checkout, fleet, "pause"),
        "resume_confirm": needs_confirm(checkout, fleet, "resume-fleet"),
        "attach": attach_command(session),
    }


def _file(described: dict[str, str], checkout: Path) -> dict[str, Any] | None:
    project, fleet = described["project"], described["fleet"]
    relative = described["path"].removeprefix("board/")
    directory = fleet_dir(checkout, fleet)
    if relative not in board_listing(directory / "board"):
        return None
    opened = _read_file(directory, f"board/{relative}")
    body = {
        "path": relative,
        "size": 0,
        "text": "",
        "truncated": False,
        "error": "This file cannot be read.",
    }
    if opened is not None:
        data, size = opened
        text, truncated = _preview(data, size)
        body = {
            "path": relative,
            "size": size,
            "text": text,
            "truncated": truncated,
            "error": "",
        }
    return {
        "kind": "file",
        "title": relative,
        "crumbs": _crumbs(project, fleet, relative),
        "note": NOTE,
        "file": body,
    }


def _seat(described: dict[str, str], checkout: Path, level: str) -> dict[str, Any] | None:
    project, fleet, name = described["project"], described["fleet"], described["path"]
    try:
        registry = load_registry(checkout, fleet)
        rows = seat_rows(checkout, registry, fleet=fleet, owned=True)
        session = str(registry.get("tmux_session") or "")
    except (FoilError, OSError):
        return None
    row = next((item for item in rows if item["name"] == name), None)
    if row is None:
        return None
    lead = next((item.get("branch") or "" for item in rows if item["name"] == "lead"), "")
    try:
        conversation = read(name, project=project, fleet=fleet, level=level)
        missing = ""
    except FoilError:
        conversation = {
            "format": "unknown",
            "source": "none",
            "level": level,
            "truncated": False,
            "omitted": 0,
            "tokens": {"input": 0, "output": 0},
            "events": [],
        }
        missing = f"foil-bridge: no transcript for '{shown(name)}'"
    return {
        "kind": "seat",
        "title": name,
        "crumbs": _crumbs(project, fleet, name),
        "note": NOTE,
        "seat": {
            "name": name,
            "template": row["template"],
            "state": row["state"],
            "branch": row["branch"],
        },
        "levels": LEVELS,
        "level": level,
        "target": described["target"],
        "conversation": conversation,
        "lines": _lines(conversation),
        "missing": missing,
        "diff": _diff(checkout, row["branch"], _base(checkout, name, row["branch"], lead)),
        "chat": {"project": project, "fleet": fleet, "seat": name},
        "worktree": _owned_path(row.get("worktree") or ""),
        "attach": attach_command(session, row["window_id"] if row.get("owned") else ""),
        "actions": [
            {
                "op": "resume",
                "label": "Resume",
                "confirm": needs_confirm(checkout, fleet, "resume"),
            },
            {
                "op": "renew",
                "label": "Renew",
                "confirm": needs_confirm(checkout, fleet, "renew"),
            },
            {"op": "kill", "label": "Kill", "confirm": True},
            {"op": "archive", "label": "Archive", "confirm": True},
        ],
    }


def _owned_path(text: str) -> str:
    path = owned_worktree(text)
    return "" if path is None else str(path)


def _lines(conversation: dict[str, Any]) -> list[str]:
    if conversation.get("source") == "pane":
        return []
    return [event_line(event) for event in conversation.get("events") or []]


def _meter(name: str, project: str, fleet: str) -> dict[str, int]:
    try:
        payload = read(name, project=project, fleet=fleet, level="summary", pane=False)
    except FoilError:
        return {"input": 0, "output": 0}
    totals = payload.get("tokens")
    if not isinstance(totals, dict):
        return {"input": 0, "output": 0}
    return {"input": int(totals.get("input") or 0), "output": int(totals.get("output") or 0)}


def _status_view(
    checkout: Path, project: str, fleet: str, directory: Path
) -> tuple[str, str, bool, list[str], list[dict[str, Any]]]:
    """Goal, state, paused, decisions, and questions. A symlink is not followed."""

    board = directory / "board"
    status = board / "status.md"
    board_link = _symlink(board)
    status_link = None if board_link is not False else _symlink(status)
    if board_link is not False or status_link is not False:
        return "", "", _paused(directory), [], []
    opened = _read_file(directory, "board/status.md")
    if opened is None:
        return "", "", _paused(directory), [], []
    try:
        record = collect_brief(checkout, [fleet], project)[0]
    except (FoilError, OSError):
        return "", "", _paused(directory), [], []
    questions = []
    for item in record["questions"]:
        evidence = item.get("evidence") or ""
        questions.append(
            {
                "id": item["id"],
                "title": mask(str(item["title"])),
                "links": home.links_in(
                    evidence if isinstance(evidence, str) else "", project=project, fleet=fleet
                ),
            }
        )
    decisions = _decision_lines(opened[0]) if opened is not None else []
    return mask(record["goal"]), record["state"], bool(record["paused"]), decisions, questions


def _paused(directory: Path) -> bool:
    try:
        return fleet_paused(directory)
    except (FoilError, OSError):
        return False


def _symlink(path: Path) -> bool | None:
    """True, False, or None when the path cannot be looked up."""

    try:
        return path.is_symlink()
    except OSError:
        return None


def _journal(directory: Path) -> list[str]:
    try:
        events = read_log(directory)[-JOURNAL_CAP:]
    except (FoilError, OSError, UnicodeDecodeError):
        return []
    lines = []
    for event in events:
        masked = {
            key: mask(value) if isinstance(value, str) else value for key, value in event.items()
        }
        lines.append(mask(render_event(masked)))
    return lines


def _decision_lines(data: bytes) -> list[str]:
    return [mask(line) for line in decision_lines(data.decode("utf-8", errors="replace"))]


def _crumbs(*parts: str) -> list[dict[str, str]]:
    crumbs = [{"label": "Home", "target": "/"}]
    if not parts:
        return crumbs
    project = parts[0]
    crumbs.append({"label": project, "target": f"projects/{project}"})
    if len(parts) > 1:
        crumbs.append({"label": parts[1], "target": f"projects/{project}/fleets/{parts[1]}"})
    if len(parts) > 2:
        crumbs.append({"label": parts[2], "target": ""})
    return crumbs


def board_listing(board: Path) -> list[str]:
    """Board files, skipping a symlink, a dot name, or a name that is not UTF-8."""

    try:
        if board.is_symlink() or not board.is_dir():
            return []
    except OSError:
        return []
    found = []
    for top, dirs, names in os.walk(board, followlinks=False, onerror=lambda _err: None):
        base = Path(top)
        dirs[:] = [name for name in dirs if _name_ok(name) and not (base / name).is_symlink()]
        for name in names:
            path = base / name
            if not _name_ok(name) or path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(board).as_posix()
            if all(_name_ok(part) for part in relative.split("/")):
                found.append(relative)
    return sorted(found, key=lambda item: (item != "status.md", item))


def _name_ok(name: str) -> bool:
    if not name or name.startswith(".") or name in {".", ".."}:
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _read_file(root: Path, relative: str) -> tuple[bytes, int] | None:
    """Bytes through the line bound, and the file's size.

    None when any component is a symlink, the path leaves ``root``, or the
    file is not a regular file. The file is opened without blocking on a FIFO.
    """

    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    try:
        folder = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
    handle = -1
    try:
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=folder)
                os.close(folder)
                folder = child
            handle = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder
            )
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode):
                return None
            data = os.read(handle, FILE_CAP + LINE_BOUND + 1)
        except OSError:
            return None
    finally:
        if handle >= 0:
            os.close(handle)
        os.close(folder)
    return data, info.st_size


def _preview(data: bytes, size: int, cap: int = FILE_CAP) -> tuple[str, bool]:
    """Masked text, cut to ``cap``. The line that crosses the cut is masked whole."""

    truncated = size > cap
    if not truncated:
        piece = data
    else:
        window = data[: cap + LINE_BOUND]
        newline = window.find(b"\n", min(cap, len(window)))
        piece = window if newline < 0 else window[:newline]
    text = mask(piece.decode("utf-8", errors="replace"))
    raw = text.encode()
    if len(raw) > cap:
        text = raw[:cap].decode(errors="ignore")
        truncated = True
    return text, truncated


def _base(checkout: Path, seat: str, branch: str, lead_branch: str) -> str:
    if not branch:
        return ""
    if seat != "lead" and lead_branch and _ref_exists(checkout, lead_branch):
        return lead_branch
    return _head(checkout)


def _head(checkout: Path) -> str:
    name = (_git(checkout, ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    if name and name != "HEAD" and _ref_ok(name):
        return name
    sha = (_git(checkout, ["rev-parse", "HEAD"]) or "").strip()
    return sha if _ref_ok(sha) else ""


def _ref_exists(checkout: Path, ref: str) -> bool:
    if not _ref_ok(ref):
        return False
    return _git_code(checkout, ["show-ref", "--verify", "--quiet", f"refs/heads/{ref}"]) == 0


def _ref_ok(ref: str) -> bool:
    return bool(ref) and ".." not in ref.split("/") and bool(_REF.fullmatch(ref))


def _diff(checkout: Path, branch: str, base: str) -> dict[str, Any]:
    shown_base = base
    if not branch:
        return {
            "base": "",
            "branch": "",
            "text": "",
            "truncated": False,
            "note": "This seat has no branch.",
        }
    if not base or not _ref_ok(branch) or not _ref_ok(base):
        return {
            "base": shown_base,
            "branch": branch,
            "text": "",
            "truncated": False,
            "note": "No diff.",
        }
    found = _git_diff(
        checkout,
        [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--unified=3",
            f"{base}...{branch}",
        ],
    )
    if found is None:
        return {"base": base, "branch": branch, "text": "", "truncated": False, "note": "No diff."}
    data, cut = found
    size = DIFF_CAP + LINE_BOUND + 1 if cut else len(data)
    # Mask before the cut, so a secret that straddles the cap is hidden whole.
    text, truncated = _preview(data, size, DIFF_CAP)
    note = "truncated" if truncated else ""
    if not text.strip() and not truncated:
        note = "No changes."
    return {"base": base, "branch": branch, "text": text, "truncated": truncated, "note": note}


def _git_diff(checkout: Path, args: list[str]) -> tuple[bytes, bool] | None:
    """Diff bytes, stopped at the line bound. The second value means the rest was dropped."""

    try:
        process = subprocess.Popen(
            ["git", "-C", str(checkout), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    stream = process.stdout
    if stream is None:
        process.kill()
        return None
    try:
        data, cut = _take(stream, DIFF_CAP + LINE_BOUND)
        if cut:
            process.kill()
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            return None
    finally:
        stream.close()
    if code not in (0, None) and not cut:
        return None
    return data, cut


def _take(stream: Any, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        piece = stream.read(min(64 * 1024, limit - total))
        if not piece:
            return b"".join(chunks), False
        chunks.append(piece)
        total += len(piece)
    return b"".join(chunks), True


def _git(checkout: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git_code(checkout: Path, args: list[str]) -> int:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    return result.returncode
