"""Project-scoped lessons."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from foil.errors import FoilError
from foil.project import foil_root
from foil.store import SAFE_ID, actor, exclusive_lock, private_dir, read_json, scan, write_json


def _lock(toplevel: Path) -> Path:
    return foil_root(toplevel) / "run" / "memory.lock"


def _shown(lesson_id: str) -> str:
    return lesson_id.replace("\n", "").replace("\r", "")


def _path(toplevel: Path, lesson_id: str) -> Path:
    if not SAFE_ID.fullmatch(lesson_id):
        raise FoilError(f"foil: unknown lesson '{_shown(lesson_id)}'")
    return foil_root(toplevel) / "memory" / f"{lesson_id}.json"


def _load(path: Path, lesson_id: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise FoilError(f"foil: unknown lesson '{_shown(lesson_id)}'")
    lesson = read_json(path)
    if lesson.get("id") != lesson_id:
        raise FoilError(f"foil: unknown lesson '{_shown(lesson_id)}'")
    return lesson


def add_lesson(toplevel: Path, text: str, *, replaces: str | None = None) -> None:
    scan(text)
    if not text.strip():
        raise FoilError("foil: lesson text is empty")
    private_dir(foil_root(toplevel) / "memory")
    with exclusive_lock(_lock(toplevel)):
        if replaces:
            _load(_path(toplevel, replaces), replaces)
        lesson_id = secrets.token_hex(4)
        while _path(toplevel, lesson_id).exists():
            lesson_id = secrets.token_hex(4)
        write_json(
            _path(toplevel, lesson_id),
            {
                "schema_version": 1,
                "id": lesson_id,
                "text": text,
                "proposer": actor(),
                "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "state": "proposed",
                "reviewer": "",
                "replaces": replaces or "",
            },
        )
    print(lesson_id)


def _review(toplevel: Path, lesson_id: str, state: str) -> None:
    path = _path(toplevel, lesson_id)
    with exclusive_lock(_lock(toplevel)):
        lesson = _load(path, lesson_id)
        if lesson.get("state") != "proposed":
            raise FoilError(f"foil: lesson '{_shown(lesson_id)}' is not proposed")
        replaces = lesson.get("replaces") or ""
        target_path = None
        target = None
        if state == "accepted" and replaces:
            target_path = _path(toplevel, replaces)
            target = _load(target_path, replaces)
            target["state"] = "superseded"
        lesson["state"] = state
        lesson["reviewer"] = actor()
        if target is not None and target_path is not None:
            write_json(target_path, target)
        write_json(path, lesson)


def accept_lesson(toplevel: Path, lesson_id: str) -> None:
    _review(toplevel, lesson_id, "accepted")


def reject_lesson(toplevel: Path, lesson_id: str) -> None:
    _review(toplevel, lesson_id, "rejected")


def list_lessons(toplevel: Path, *, all_lessons: bool = False, as_json: bool = False) -> None:
    directory = foil_root(toplevel) / "memory"
    if directory.is_symlink():
        raise FoilError("foil: refusing symlink")
    lessons: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            lessons.append(read_json(path))
    if not all_lessons:
        lessons = [item for item in lessons if item.get("state") == "accepted"]
    lessons.sort(key=lambda item: (str(item.get("time", "")), str(item.get("id", ""))))
    if as_json:
        print(json.dumps(lessons, ensure_ascii=False, sort_keys=True))
        return
    for item in lessons:
        text = " ".join(str(item.get("text", "")).split())
        if all_lessons:
            print(f"{item['id']}\t{item.get('state', '')}\t{text}")
        else:
            print(f"{item['id']}\t{text}")
