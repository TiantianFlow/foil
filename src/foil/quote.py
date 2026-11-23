"""Quote selected lines and mail the seat that owns them.

The mail is ``foil send``: a message from ``user`` to a worker appends the
lead pointer, and the delivery log is what ``mail list --sent`` reads (F13).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from foil.board import send_mail
from foil.errors import FoilError, shown
from foil.front import parse_front
from foil.store import RESERVED, fleet_dir, load_registry, scan

TEXT_CAP = 16 * 1024
FILE_CAP = 1024 * 1024
_RANGE = re.compile(r"\A([1-9][0-9]{0,6})(?:-([1-9][0-9]{0,6}))?\Z")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,200}")
_DECISIONS = "status.md#decisions"


def parse_lines(text: str) -> tuple[int, int]:
    """``N`` or ``N-M``, 1-based and inclusive."""

    if not isinstance(text, str):
        raise FoilError("foil: --lines is N or N-M")
    match = _RANGE.fullmatch(text.strip())
    if match is None:
        raise FoilError("foil: --lines is N or N-M")
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if end < start:
        raise FoilError("foil: --lines is N or N-M")
    return start, end


def decision_lines(text: str) -> list[str]:
    """One folded line per ``status.md`` decision. An unparsed file has none."""

    front = parse_front(text)
    value = (front.fields or {}).get("decisions") if front.raw is None else None
    if not isinstance(value, list):
        return []
    lines = []
    for item in value:
        if isinstance(item, str) and item.strip():
            lines.append(" ".join(item.split()))
    return lines


def send_quote(toplevel: Path, path: str, start: int, end: int, sentence: str) -> str:
    """Mail ``path`` lines ``start-end`` plus ``sentence``. Returns the seat."""

    sentence = _sentence(sentence)
    label, rows, owner = _load(toplevel, path, end)
    if start > len(rows):
        raise FoilError("foil: line range is outside the file")
    chosen = rows[start - 1 : end]
    for row in chosen:
        scan(row)
    body = _body(label, start, end, chosen, sentence)
    if len(body.encode()) > TEXT_CAP:
        raise FoilError("foil: quote text is too long")
    scan(body)
    send_mail(toplevel, owner, body)
    return owner


def _sentence(text: str) -> str:
    if not isinstance(text, str):
        raise FoilError("foil: quote text is empty")
    if len(text.encode()) > TEXT_CAP:
        raise FoilError("foil: quote text is too long")
    cleaned = text.strip()
    if not cleaned:
        raise FoilError("foil: quote text is empty")
    scan(cleaned)
    return cleaned


def _body(path: str, start: int, end: int, rows: list[str], sentence: str) -> str:
    lines = [f"path: {path}", f"lines: {start}-{end}"]
    number = start
    for row in rows:
        lines.append(f"{number}|{row}")
        number += 1
    lines.append("")
    lines.append(sentence)
    return "\n".join(lines) + "\n"


def _load(toplevel: Path, path: str, end: int) -> tuple[str, list[str], str]:
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 300
        or any(char in path for char in "\n\r\x00\\")
    ):
        raise FoilError("foil: path must stay on the board")
    if path == _DECISIONS:
        return _decisions(toplevel, end)
    if path.startswith("branch:"):
        return _branch(toplevel, path[len("branch:") :], path, end)
    return _file(toplevel, path, end)


def _file(toplevel: Path, path: str, end: int) -> tuple[str, list[str], str]:
    opened = _board_file(toplevel, path)
    rows = _read_lines(opened, end, path)
    if end > len(rows):
        raise FoilError("foil: line range is outside the file")
    owner = _file_owner(_seats(toplevel), _fields(rows))
    return path, rows, owner


def _decisions(toplevel: Path, end: int) -> tuple[str, list[str], str]:
    opened = _board_file(toplevel, "status.md")
    # The list is short. A status file over the cap is not a decision source.
    try:
        data = opened.read_bytes()
    except OSError:
        raise FoilError("foil: cannot read: status.md") from None
    if len(data) > FILE_CAP:
        raise FoilError("foil: file is too large")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FoilError("foil: cannot read: status.md") from None
    rows = decision_lines(text)
    if end > len(rows):
        raise FoilError("foil: line range is outside the file")
    return _DECISIONS, rows, "lead"


def _branch(
    toplevel: Path, name: str, label: str, end: int
) -> tuple[str, list[str], str]:
    if not _ref_ok(name):
        raise FoilError("foil: unknown branch")
    seats = _seats(toplevel)
    owner = _branch_owner(seats, name)
    lead = seats.get("lead")
    lead_branch = ""
    if isinstance(lead, dict) and isinstance(lead.get("branch"), str):
        lead_branch = lead["branch"]
    base = _base(toplevel, owner, lead_branch, name)
    if not base:
        raise FoilError("foil: no diff")
    data, truncated = _diff_bytes(toplevel, base, name)
    if not data.strip() and not truncated:
        raise FoilError("foil: no diff")
    rows = _complete_lines(data, end, truncated=truncated)
    return label, rows, owner


def _fields(rows: list[str]) -> dict:
    front = parse_front("\n".join(rows) + "\n")
    if front.raw is not None or not isinstance(front.fields, dict):
        return {}
    return front.fields


def _file_owner(seats: dict, fields: dict) -> str:
    contract = fields.get("contract")
    if contract == "task/v1":
        name = fields.get("owner")
    elif contract == "result/v1":
        name = fields.get("author")
    else:
        name = None
    if isinstance(name, str) and name in seats and name not in RESERVED:
        row = seats[name]
        if isinstance(row, dict):
            return name
    return "lead"


def _branch_owner(seats: dict, branch: str) -> str:
    matches = [
        name
        for name, row in seats.items()
        if isinstance(row, dict) and row.get("branch") == branch
    ]
    if not matches:
        return "lead"
    live = [name for name in matches if seats[name].get("state") != "killed"]
    return sorted(live or matches)[0]


def _seats(toplevel: Path) -> dict:
    registry = load_registry(toplevel)
    seats = registry.get("seats")
    return seats if isinstance(seats, dict) else {}


def _board_file(toplevel: Path, relative: str) -> Path:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FoilError("foil: path must stay on the board")
    board = fleet_dir(toplevel) / "board"
    try:
        if board.is_symlink() or not board.is_dir():
            raise FoilError("foil: path must stay on the board")
        current = board
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise FoilError("foil: refusing symlink")
        if not current.is_file():
            raise FoilError(f"foil: file not found: {shown(relative)}")
        root = board.resolve()
        if not current.resolve().is_relative_to(root):
            raise FoilError("foil: path must stay on the board")
    except FoilError:
        raise
    except OSError:
        raise FoilError(f"foil: cannot read: {shown(relative)}") from None
    return current


def _read_lines(path: Path, end: int, label: str) -> list[str]:
    """Lines from the start, through ``end`` and through a front matter block."""

    rows: list[str] = []
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                have_range = len(rows) >= end
                if have_range and _front_closed(rows):
                    break
                piece = handle.readline(FILE_CAP + 1)
                if not piece:
                    break
                if len(piece) > FILE_CAP or total + len(piece) > FILE_CAP:
                    if have_range and rows and rows[0] == "---":
                        break
                    raise FoilError("foil: file is too large")
                total += len(piece)
                try:
                    text = piece.decode("utf-8")
                except UnicodeDecodeError:
                    raise FoilError(f"foil: cannot read: {shown(label)}") from None
                rows.append(text.rstrip("\r\n"))
    except FoilError:
        raise
    except OSError:
        raise FoilError(f"foil: cannot read: {shown(label)}") from None
    return rows


def _front_closed(rows: list[str]) -> bool:
    if not rows or rows[0] != "---":
        return True
    return "---" in rows[1:]


def _complete_lines(data: bytes, end: int, *, truncated: bool) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    if truncated and text and not text.endswith("\n"):
        text = text.rsplit("\n", 1)[0]
    rows = text.splitlines()
    if len(rows) < end:
        raise FoilError(
            "foil: file is too large" if truncated else "foil: line range is outside the file"
        )
    return rows


def _ref_ok(ref: str) -> bool:
    return bool(ref) and ".." not in ref.split("/") and bool(_REF.fullmatch(ref))


def _base(checkout: Path, seat: str, lead_branch: str, branch: str) -> str:
    """The base that seat's page uses, except a branch the lead also records.

    A worker page diffs the lead's branch. The lead page diffs the checkout.
    When ``branch`` is the lead's own branch, the worker page is empty and the
    lines a person can select are the lead page's, so the mail uses that base.
    """

    if (
        seat != "lead"
        and lead_branch
        and lead_branch != branch
        and _ref_exists(checkout, lead_branch)
    ):
        return lead_branch
    return _head(checkout)


def _head(checkout: Path) -> str:
    name = (_git_text(checkout, ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    if name and name != "HEAD" and _ref_ok(name):
        return name
    sha = (_git_text(checkout, ["rev-parse", "HEAD"]) or "").strip()
    return sha if _ref_ok(sha) else ""


def _ref_exists(checkout: Path, ref: str) -> bool:
    if not _ref_ok(ref):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "show-ref", "--verify", "--quiet", f"refs/heads/{ref}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _git_text(checkout: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _diff_bytes(checkout: Path, base: str, branch: str) -> tuple[bytes, bool]:
    if not _ref_ok(base) or not _ref_ok(branch):
        raise FoilError("foil: no diff")
    try:
        process = subprocess.Popen(
            [
                "git",
                "-C",
                str(checkout),
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=3",
                f"{base}...{branch}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise FoilError("foil: no diff") from None
    stream = process.stdout
    if stream is None:
        process.kill()
        raise FoilError("foil: no diff")
    try:
        data = stream.read(FILE_CAP + 1)
        truncated = len(data) > FILE_CAP
        if truncated:
            data = data[:FILE_CAP]
            process.kill()
        try:
            code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise FoilError("foil: no diff") from None
    finally:
        stream.close()
    if code not in (0, None) and not truncated:
        raise FoilError("foil: no diff")
    return data, truncated
