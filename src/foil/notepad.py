"""Shared notepad files (CAP-020)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
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

MAX_BODY_BYTES = 64 * 1024


class NotepadError(MailboxError):
    """Notepad input or storage is unsafe or malformed."""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class NotepadRecord:
    fleet_id: str
    notepad_id: str
    body: str
    updated_by: str
    updated_at: str
    revision: int = 1
    acknowledgements: tuple[str, ...] = ()
    extensions: dict[str, Any] = field(default_factory=dict)
    schema_version: int = field(default=SCHEMA_VERSION, init=False)

    def __post_init__(self) -> None:
        _validate_id(self.fleet_id, "fleet_id")
        _validate_id(self.notepad_id, "notepad_id")
        _validate_id(self.updated_by, "updated_by")
        _validate_timestamp(self.updated_at)
        if not isinstance(self.body, str) or not self.body.strip():
            raise NotepadError("body must be a non-empty string")
        if len(self.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise NotepadError(f"body exceeds {MAX_BODY_BYTES} bytes")
        try:
            _assert_no_secret(self.body, path="body")
        except ValueError as exc:
            raise NotepadError(str(exc)) from exc
        if type(self.revision) is not int or self.revision < 1:
            raise NotepadError("revision must be a positive integer")
        for actor in self.acknowledgements:
            _validate_id(actor, "acknowledgement.actor")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fleet_id": self.fleet_id,
            "notepad_id": self.notepad_id,
            "body": self.body,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "acknowledgements": list(self.acknowledgements),
            "extensions": self.extensions,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> NotepadRecord:
        if not isinstance(payload, dict):
            raise NotepadError("notepad must be an object")
        acks = payload.get("acknowledgements", [])
        if not isinstance(acks, list) or not all(isinstance(item, str) for item in acks):
            raise NotepadError("acknowledgements must be an array of ids")
        return cls(
            fleet_id=payload["fleet_id"],
            notepad_id=payload["notepad_id"],
            body=payload["body"],
            updated_by=payload["updated_by"],
            updated_at=payload["updated_at"],
            revision=payload.get("revision", 1),
            acknowledgements=tuple(acks),
            extensions=payload.get("extensions", {}),
        )


class NotepadStore:
    def __init__(self, state_root: Path | str):
        self.state_root = Path(state_root)

    def path(self, fleet_id: str, notepad_id: str) -> Path:
        _validate_id(fleet_id, "fleet_id")
        _validate_id(notepad_id, "notepad_id")
        return (
            self.state_root
            / f"v{SCHEMA_VERSION}"
            / "fleets"
            / fleet_id
            / "notepads"
            / f"{notepad_id}.json"
        )

    def write(self, record: NotepadRecord) -> tuple[NotepadRecord, bool]:
        path = self.path(record.fleet_id, record.notepad_id)
        _ensure_private_directory(path.parent)
        lock = path.parent.parent / "locks" / f"notepad-{record.notepad_id}.lock"
        with _exclusive_lock(lock):
            if path.is_file():
                existing = NotepadRecord.from_dict(
                    _read_json_file(path, error_type=NotepadError)
                )
                if existing.body == record.body and existing.updated_by == record.updated_by:
                    return existing, True
                record = NotepadRecord(
                    fleet_id=record.fleet_id,
                    notepad_id=record.notepad_id,
                    body=record.body,
                    updated_by=record.updated_by,
                    updated_at=record.updated_at,
                    revision=existing.revision + 1,
                    acknowledgements=(),
                    extensions=record.extensions,
                )
            _atomic_write_json(path, record.to_dict())
            return record, False

    def read(self, fleet_id: str, notepad_id: str) -> NotepadRecord:
        payload = _read_json_file(self.path(fleet_id, notepad_id), error_type=NotepadError)
        return NotepadRecord.from_dict(payload)

    def acknowledge(self, fleet_id: str, notepad_id: str, actor: str) -> NotepadRecord:
        _validate_id(actor, "actor")
        path = self.path(fleet_id, notepad_id)
        lock = path.parent.parent / "locks" / f"notepad-{notepad_id}.lock"
        with _exclusive_lock(lock):
            current = self.read(fleet_id, notepad_id)
            acks = current.acknowledgements
            if actor not in acks:
                acks = (*acks, actor)
            updated = NotepadRecord(
                fleet_id=current.fleet_id,
                notepad_id=current.notepad_id,
                body=current.body,
                updated_by=current.updated_by,
                updated_at=current.updated_at,
                revision=current.revision,
                acknowledgements=acks,
                extensions=current.extensions,
            )
            _atomic_write_json(path, updated.to_dict())
            return updated


def write_notepad(
    state_root: Path | str,
    fleet_id: str,
    notepad_id: str,
    *,
    author: str,
    body: str,
) -> dict[str, Any]:
    record, duplicate = NotepadStore(state_root).write(
        NotepadRecord(
            fleet_id=fleet_id,
            notepad_id=notepad_id,
            body=body,
            updated_by=author,
            updated_at=_now(),
        )
    )
    payload = record.to_dict()
    payload["duplicate"] = duplicate
    return payload


def read_notepad(state_root: Path | str, fleet_id: str, notepad_id: str) -> dict[str, Any]:
    return NotepadStore(state_root).read(fleet_id, notepad_id).to_dict()


def ack_notepad(
    state_root: Path | str,
    fleet_id: str,
    notepad_id: str,
    *,
    actor: str,
) -> dict[str, Any]:
    record = NotepadStore(state_root).acknowledge(fleet_id, notepad_id, actor)
    payload = record.to_dict()
    payload["state"] = "acknowledged"
    return payload
