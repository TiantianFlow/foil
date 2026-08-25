"""Reviewed memory lessons (CAP-021)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from foil.mailbox import MailboxError
from foil.registry import (
    SCHEMA_VERSION,
    _assert_no_secret,
    _atomic_write_json,
    _ensure_private_directory,
    _exclusive_lock,
    _read_json_file,
    _validate_id,
    _validate_timestamp,
)

MAX_BODY_BYTES = 8 * 1024


class MemoryError(MailboxError):
    """Memory lesson input or storage is unsafe or malformed."""


class LessonState(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class MemoryLesson:
    fleet_id: str
    lesson_id: str
    body: str
    proposed_by: str
    source_task_id: str
    state: LessonState = LessonState.PROPOSED
    proposed_at: str = ""
    reviewed_by: str | None = None
    source_event_id: str | None = None
    previous_lesson_id: str | None = None
    replacement_lesson_id: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _validate_id(self.fleet_id, "fleet_id")
        _validate_id(self.lesson_id, "lesson_id")
        _validate_id(self.proposed_by, "proposed_by")
        _validate_id(self.source_task_id, "source_task_id")
        if self.reviewed_by is not None:
            _validate_id(self.reviewed_by, "reviewed_by")
        if self.source_event_id is not None:
            _validate_id(self.source_event_id, "source_event_id")
        if self.previous_lesson_id is not None:
            _validate_id(self.previous_lesson_id, "previous_lesson_id")
        if self.replacement_lesson_id is not None:
            _validate_id(self.replacement_lesson_id, "replacement_lesson_id")
        if not isinstance(self.body, str) or not self.body.strip():
            raise MemoryError("body must be a non-empty string")
        if len(self.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise MemoryError(f"body exceeds {MAX_BODY_BYTES} bytes")
        try:
            _assert_no_secret(self.body, path="body")
        except ValueError as exc:
            raise MemoryError(str(exc)) from exc
        object.__setattr__(self, "state", LessonState(self.state))
        proposed_at = self.proposed_at or _now()
        object.__setattr__(self, "proposed_at", proposed_at)
        _validate_timestamp(self.proposed_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "lesson_id": self.lesson_id,
            "body": self.body,
            "proposed_by": self.proposed_by,
            "proposed_at": self.proposed_at,
            "source_task_id": self.source_task_id,
            "source_event_id": self.source_event_id,
            "state": self.state.value,
            "reviewed_by": self.reviewed_by,
            "previous_lesson_id": self.previous_lesson_id,
            "replacement_lesson_id": self.replacement_lesson_id,
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> MemoryLesson:
        if not isinstance(payload, dict):
            raise MemoryError("lesson must be an object")
        return cls(
            fleet_id=payload["fleet_id"],
            lesson_id=payload["lesson_id"],
            body=payload["body"],
            proposed_by=payload["proposed_by"],
            source_task_id=payload["source_task_id"],
            state=LessonState(payload.get("state", "proposed")),
            proposed_at=payload.get("proposed_at") or _now(),
            reviewed_by=payload.get("reviewed_by"),
            source_event_id=payload.get("source_event_id"),
            previous_lesson_id=payload.get("previous_lesson_id"),
            replacement_lesson_id=payload.get("replacement_lesson_id"),
            extensions=payload.get("extensions") or {},
        )


class MemoryStore:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def path(self, fleet_id: str, lesson_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        _validate_id(lesson_id, "lesson_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "memory"
            / f"{lesson_id}.json"
        )

    def _lock(self, fleet_id: str, lesson_id: str) -> Path:
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "locks"
            / f"memory-{lesson_id}.lock"
        )

    def _write(self, lesson: MemoryLesson) -> MemoryLesson:
        path = self.path(lesson.fleet_id, lesson.lesson_id)
        _ensure_private_directory(path.parent)
        _atomic_write_json(path, lesson.to_dict())
        return lesson

    def read(self, fleet_id: str, lesson_id: str) -> MemoryLesson:
        return MemoryLesson.from_dict(
            _read_json_file(self.path(fleet_id, lesson_id), error_type=MemoryError)
        )

    def propose(self, lesson: MemoryLesson) -> MemoryLesson:
        path = self.path(lesson.fleet_id, lesson.lesson_id)
        with _exclusive_lock(self._lock(lesson.fleet_id, lesson.lesson_id)):
            if path.is_file():
                existing = self.read(lesson.fleet_id, lesson.lesson_id)
                if existing.to_dict() == lesson.to_dict():
                    return existing
                raise MemoryError("lesson already exists")
            return self._write(lesson)

    def accept(self, fleet_id: str, lesson_id: str, actor: str) -> MemoryLesson:
        _validate_id(actor, "actor")
        with _exclusive_lock(self._lock(fleet_id, lesson_id)):
            current = self.read(fleet_id, lesson_id)
            if current.state is LessonState.ACCEPTED and current.reviewed_by == actor:
                return current
            if current.state is not LessonState.PROPOSED:
                raise MemoryError("only proposed lessons can be accepted")
            return self._write(
                MemoryLesson(
                    fleet_id=current.fleet_id,
                    lesson_id=current.lesson_id,
                    body=current.body,
                    proposed_by=current.proposed_by,
                    source_task_id=current.source_task_id,
                    state=LessonState.ACCEPTED,
                    proposed_at=current.proposed_at,
                    reviewed_by=actor,
                    source_event_id=current.source_event_id,
                    previous_lesson_id=current.previous_lesson_id,
                    replacement_lesson_id=current.replacement_lesson_id,
                    extensions=current.extensions,
                )
            )

    def reject(self, fleet_id: str, lesson_id: str, actor: str) -> MemoryLesson:
        _validate_id(actor, "actor")
        with _exclusive_lock(self._lock(fleet_id, lesson_id)):
            current = self.read(fleet_id, lesson_id)
            if current.state is LessonState.REJECTED and current.reviewed_by == actor:
                return current
            if current.state not in {LessonState.PROPOSED, LessonState.ACCEPTED}:
                raise MemoryError("lesson cannot be rejected")
            return self._write(
                MemoryLesson(
                    fleet_id=current.fleet_id,
                    lesson_id=current.lesson_id,
                    body=current.body,
                    proposed_by=current.proposed_by,
                    source_task_id=current.source_task_id,
                    state=LessonState.REJECTED,
                    proposed_at=current.proposed_at,
                    reviewed_by=actor,
                    source_event_id=current.source_event_id,
                    previous_lesson_id=current.previous_lesson_id,
                    replacement_lesson_id=current.replacement_lesson_id,
                    extensions=current.extensions,
                )
            )

    def supersede(
        self,
        fleet_id: str,
        lesson_id: str,
        *,
        author: str,
        replacement_id: str,
        body: str,
        task_id: str,
    ) -> MemoryLesson:
        with _exclusive_lock(self._lock(fleet_id, lesson_id)):
            current = self.read(fleet_id, lesson_id)
            if current.state not in {LessonState.PROPOSED, LessonState.ACCEPTED}:
                raise MemoryError("lesson cannot be superseded")
            replacement = MemoryLesson(
                fleet_id=fleet_id,
                lesson_id=replacement_id,
                body=body,
                proposed_by=author,
                source_task_id=task_id,
                state=LessonState.PROPOSED,
                previous_lesson_id=lesson_id,
            )
            self._write(replacement)
            updated = MemoryLesson(
                fleet_id=current.fleet_id,
                lesson_id=current.lesson_id,
                body=current.body,
                proposed_by=current.proposed_by,
                source_task_id=current.source_task_id,
                state=LessonState.SUPERSEDED,
                proposed_at=current.proposed_at,
                reviewed_by=author,
                source_event_id=current.source_event_id,
                previous_lesson_id=current.previous_lesson_id,
                replacement_lesson_id=replacement_id,
                extensions=current.extensions,
            )
            return self._write(updated)


def propose_memory(
    state_root: Path | str,
    fleet_id: str,
    lesson_id: str,
    *,
    author: str,
    body: str,
    task_id: str,
) -> dict[str, Any]:
    return MemoryStore(state_root).propose(
        MemoryLesson(
            fleet_id=fleet_id,
            lesson_id=lesson_id,
            body=body,
            proposed_by=author,
            source_task_id=task_id,
        )
    ).to_dict()


def accept_memory(
    state_root: Path | str, fleet_id: str, lesson_id: str, *, actor: str
) -> dict[str, Any]:
    return MemoryStore(state_root).accept(fleet_id, lesson_id, actor).to_dict()


def reject_memory(
    state_root: Path | str, fleet_id: str, lesson_id: str, *, actor: str
) -> dict[str, Any]:
    return MemoryStore(state_root).reject(fleet_id, lesson_id, actor).to_dict()


def supersede_memory(
    state_root: Path | str,
    fleet_id: str,
    lesson_id: str,
    *,
    author: str,
    replacement_id: str,
    body: str,
    task_id: str,
) -> dict[str, Any]:
    return MemoryStore(state_root).supersede(
        fleet_id,
        lesson_id,
        author=author,
        replacement_id=replacement_id,
        body=body,
        task_id=task_id,
    ).to_dict()


def status_memory(state_root: Path | str, fleet_id: str, lesson_id: str) -> dict[str, Any]:
    return MemoryStore(state_root).read(fleet_id, lesson_id).to_dict()
